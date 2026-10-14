"""Seeded randomized invariant tests (spec item 23, section G).

Invariant under test: after ANY sequence of steal_work() calls and
pause/resume cycles, the segments must exactly tile [0, total-1] --
no gaps, no overlaps -- and a real download must finish with the
source's hash.

Determinism: every random choice comes from random.Random(0x23).
No assertion depends on wall-clock timing (the mid-download pause is
triggered by byte progress, not by sleep); generous timeout guards are
only there to fail loudly on a hang instead of blocking CI forever.
"""

import hashlib
import os
import random
import threading
import time

import pytest

from opendm import engine

from conftest import make_server, PAYLOAD, PAYLOAD_SHA256

SEED = 0x23
UNIT_ITERATIONS = 300          # seeded segment-tiling iterations
UNIT_STEALS_PER_ITER = 25      # max steal_work() calls per iteration
RESUME_STEALS = 40             # steal_work() calls against the real paused sidecar
PAUSE_AT_FRACTION = 0.25       # trigger pause once this fraction of bytes landed
HANG_GUARD_SECONDS = 120       # generous timeout guard, never an assertion on timing


def assert_exact_tiling(segments, total, where):
    """Assert segments' [start..end] exactly tile [0, total-1].

    Explicit, separate failure modes so a gap and an overlap can never
    be confused (and a would-be gap/overlap bug fails loudly here).
    """
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    assert ordered, f"{where}: no segments at all"
    first, last = ordered[0], ordered[-1]
    assert first.start == 0, (
        f"{where}: GAP at head: first segment starts at {first.start}, expected 0")
    assert last.end == total - 1, (
        f"{where}: TAIL MISMATCH: last segment ends at {last.end}, "
        f"expected {total - 1} (missing or excess tail bytes)")
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start == prev.end + 1:
            continue
        if nxt.start > prev.end + 1:
            raise AssertionError(
                f"{where}: GAP of {nxt.start - prev.end - 1} bytes between "
                f"[{prev.start}..{prev.end}] and [{nxt.start}..{nxt.end}]")
        raise AssertionError(
            f"{where}: OVERLAP of {prev.end - nxt.start + 1} bytes between "
            f"[{prev.start}..{prev.end}] and [{nxt.start}..{nxt.end}]")


def assert_roundtrip_fidelity(segments, where):
    """Segment.to_dict()/from_dict() must preserve id/start/cursor/end."""
    for s in segments:
        r = engine.Segment.from_dict(s.to_dict())
        assert (r.seg_id, r.start, r.cursor, r.end) == \
               (s.seg_id, s.start, s.cursor, s.end), (
            f"{where}: sidecar round-trip changed segment "
            f"{s.seg_id}: {(s.start, s.cursor, s.end)} -> "
            f"{(r.start, r.cursor, r.end)}")
    ids = [s.seg_id for s in segments]
    assert len(set(ids)) == len(ids), f"{where}: duplicate segment ids: {ids}"


def test_segment_tiling_under_randomized_steal_and_pause_resume():
    """300 seeded iterations: random plan + steals + cursor progress +
    pause/resume round-trip must keep segments exactly tiling [0, total-1].
    """
    rng = random.Random(SEED)
    lock = threading.Lock()
    for it in range(UNIT_ITERATIONS):
        where = f"iter {it}"
        total = rng.randint(1, 10 * 1024 * 1024)
        n = rng.randint(1, 16)
        segments = [
            engine.Segment(i, s, e, cursor=s)
            for i, (s, e) in enumerate(engine.plan_ranges(total, n))
        ]
        assert_exact_tiling(segments, total, f"{where} initial plan")

        # Simulate download progress: random cursors inside [start, end+1].
        for s in segments:
            if rng.random() < 0.5:
                s.cursor = rng.randint(s.start, s.end + 1)

        # Randomized work-stealing sequence (steal_work enforces its own
        # preconditions; a None return is a legitimate no-op).
        for _ in range(rng.randint(0, UNIT_STEALS_PER_ITER)):
            engine.steal_work(segments, lock)

        # Simulate pause/resume: sidecar round-trip must be lossless and the
        # tiling invariant must hold on the restored segments too.
        restored = [engine.Segment.from_dict(s.to_dict()) for s in segments]
        assert_roundtrip_fidelity(restored, f"{where} after steals")
        assert_exact_tiling(restored, total, f"{where} after steals+roundtrip")


def _run_download_guarded(kwargs, timeout=HANG_GUARD_SECONDS):
    """Run engine.download in a thread so a hang fails loudly, not forever."""
    result = {}

    def run():
        try:
            result["ret"] = engine.download(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised by caller
            result["exc"] = exc

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout=timeout)
    assert not th.is_alive(), (
        f"download() did not finish within {timeout}s (hang guard)")
    if "exc" in result:
        raise result["exc"]
    return result["ret"]


def test_download_pause_resume_with_stealing_preserves_hash(tmp_path):
    """Real download against the local fixture with work stealing active,
    paused mid-download via stop_event and resumed: segments tile
    throughout and the final file hash/size match the source exactly.
    """
    total = len(PAYLOAD)
    with make_server() as server:
        url = server.url("/file.bin")
        dest = str(tmp_path / "file.bin")
        stop = threading.Event()

        # Deterministic pause: set stop_event from inside status_cb once
        # enough bytes landed. status_cb(done, total, elapsed) fires on
        # every chunk, so this depends on byte progress, not on timing.
        def on_status(done, _total, _elapsed):
            if done >= int(total * PAUSE_AT_FRACTION):
                stop.set()

        paused = False
        try:
            _run_download_guarded(dict(url=url, dest=dest, conns=4,
                                       stop_event=stop, status_cb=on_status))
        except engine.Paused:
            paused = True
        assert paused, "expected engine.Paused once stop_event was set"

        # The completion gate must not have published the final file early.
        assert not os.path.exists(dest), (
            "final file must not appear before the completion gate passes")

        # The pause checkpoint persisted segments: reload like a resume
        # would and check progress + tiling on the real paused state.
        sidecar = engine.load_sidecar(dest)
        assert sidecar is not None, "pause did not persist a sidecar"
        assert sidecar.get("url") == url and sidecar.get("total") == total
        paused_segs = [engine.Segment.from_dict(d)
                       for d in sidecar["segments"]]
        assert any(s.cursor > s.start for s in paused_segs), (
            "paused sidecar recorded no download progress")
        assert_roundtrip_fidelity(paused_segs, "paused sidecar")
        assert_exact_tiling(paused_segs, total, "paused sidecar")

        # Deterministic work stealing against the real paused segments.
        rng = random.Random(SEED)
        lock = threading.Lock()
        for _ in range(RESUME_STEALS):
            engine.steal_work(paused_segs, lock)
        assert_exact_tiling(paused_segs, total, "paused sidecar after steals")

        # Resume to completion (fresh stop_event, no pause this time).
        stop.clear()
        out = _run_download_guarded(dict(url=url, dest=dest, conns=4))
        assert out["size"] == total, (
            f"downloaded size {out['size']} != source size {total}")
        assert out["sha256"] == PAYLOAD_SHA256, "engine-reported hash mismatch"
        assert os.path.getsize(dest) == total
        with open(dest, "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == PAYLOAD_SHA256, (
                "final file hash does not match the source payload")
