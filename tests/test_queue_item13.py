"""Item 13: wire proxy + shared rate limiter into Job._run.

- QueueManager(dest_dir, ..., proxy=..., speed_limit=...) owns one shared
  engine.TokenBucket built from speed_limit; set_speed_limit() changes the
  live limit via token_bucket.set_limit().
- Job._run passes the manager's proxy and shared rate limiter to
  engine.download.

engine.TokenBucket / engine.download(rate_limiter=...) belong to the engine
worker (items 2-10) and have NOT landed yet in this working tree: the
queue-side code is defensive (getattr(engine, "TokenBucket", None)) and the
tests use a fake bucket + stub download. The live-throughput test skips
until the engine side lands.
"""
import inspect
import time

import pytest

from opendm import engine
from opendm.queue import QueueManager


def _make_qm(tmp_path, **kw):
    return QueueManager(str(tmp_path / "downloads"),
                        state_file=str(tmp_path / "q.json"), **kw)


class FakeBucket:
    """Stand-in for the not-yet-landed engine.TokenBucket."""

    def __init__(self, limit):
        self.limit = limit
        self.set_calls = []

    def set_limit(self, v):
        self.set_calls.append(v)
        self.limit = v


class CaptureDownload:
    """Stub engine.download capturing its kwargs."""

    def __init__(self):
        self.kwargs = None

    def __call__(self, url, dest=None, conns=4, status_cb=None, stop_event=None,
                 parts_cb=None, headers=None, proxy=None, **kw):
        self.kwargs = {"proxy": proxy, **kw}
        return {"sha256": "x" * 64, "size": 1234}


def _wait_for(cond, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_run_passes_proxy_and_shared_limiter(monkeypatch, tmp_path):
    """Job._run forwards qm.proxy and qm.token_bucket to engine.download."""
    monkeypatch.setattr(engine, "TokenBucket", FakeBucket, raising=False)
    stub = CaptureDownload()
    monkeypatch.setattr(engine, "download", stub)

    qm = _make_qm(tmp_path, proxy="http://127.0.0.1:8080",
                  speed_limit=256 * 1024)
    assert isinstance(qm.token_bucket, FakeBucket)
    assert qm.token_bucket.limit == 256 * 1024
    assert qm.proxy == "http://127.0.0.1:8080"

    job = qm.add("http://127.0.0.1/file.bin")
    job._run()  # synchronous: exercises the download-kwarg wiring

    assert job.state == "completed"
    assert stub.kwargs is not None
    assert stub.kwargs["proxy"] == "http://127.0.0.1:8080"
    assert stub.kwargs["rate_limiter"] is qm.token_bucket


def test_set_speed_limit_updates_live_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "TokenBucket", FakeBucket, raising=False)
    qm = _make_qm(tmp_path, speed_limit=100)
    assert qm.speed_limit == 100

    qm.set_speed_limit(512 * 1024)
    assert qm.speed_limit == 512 * 1024
    assert qm.token_bucket.set_calls == [512 * 1024]
    assert qm.token_bucket.limit == 512 * 1024

    qm.set_speed_limit(0)  # 0 = unlimited
    assert qm.speed_limit == 0
    assert qm.token_bucket.set_calls == [512 * 1024, 0]


def test_unlimited_default_creates_unlimited_bucket(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "TokenBucket", FakeBucket, raising=False)
    stub = CaptureDownload()
    monkeypatch.setattr(engine, "download", stub)

    qm = _make_qm(tmp_path)  # no speed_limit -> 0 = unlimited
    assert qm.speed_limit == 0
    assert isinstance(qm.token_bucket, FakeBucket)
    assert qm.token_bucket.limit == 0

    job = qm.add("http://127.0.0.1/file.bin")
    job._run()
    assert job.state == "completed"
    assert stub.kwargs["proxy"] is None
    assert stub.kwargs["rate_limiter"] is qm.token_bucket


def test_missing_token_bucket_with_limit_raises_clear_error(monkeypatch,
                                                            tmp_path):
    """Defensive path while the engine side hasn't landed: a requested limit
    must fail loudly at download time, not silently download unlimited."""
    monkeypatch.delattr(engine, "TokenBucket", raising=False)
    stub = CaptureDownload()
    monkeypatch.setattr(engine, "download", stub)

    qm = _make_qm(tmp_path, speed_limit=256 * 1024)
    assert qm.token_bucket is None

    job = qm.add("http://127.0.0.1/file.bin")
    with pytest.raises(RuntimeError, match="TokenBucket"):
        job._run()
    assert stub.kwargs is None  # download never attempted


def test_missing_token_bucket_unlimited_still_works(monkeypatch, tmp_path):
    """No limit requested -> no bucket needed -> download proceeds."""
    monkeypatch.delattr(engine, "TokenBucket", raising=False)
    stub = CaptureDownload()
    monkeypatch.setattr(engine, "download", stub)

    qm = _make_qm(tmp_path)
    job = qm.add("http://127.0.0.1/file.bin")
    job._run()
    assert job.state == "completed"
    assert stub.kwargs is not None
    assert "rate_limiter" not in stub.kwargs  # nothing to pass


def _engine_limiter_ready():
    return (getattr(engine, "TokenBucket", None) is not None
            and "rate_limiter" in inspect.signature(engine.download).parameters)


def test_live_throughput_bounded_by_limit(tmp_path):
    """Against the local fixture, measured throughput must not exceed
    1.3x the 256 KB/s limit. Requires the engine side (TokenBucket +
    engine.download(rate_limiter=...)) to have landed."""
    if not _engine_limiter_ready():
        pytest.skip("engine.TokenBucket / download(rate_limiter=) not landed yet")

    from conftest import make_server
    limit = 256 * 1024
    with make_server() as server:
        qm = _make_qm(tmp_path, speed_limit=limit)
        job = qm.add(server.url("/file.bin"))
        job.start()
        # warm up 2s (absorbs any initial token-bucket burst), then measure
        # throughput over a ~5s window
        assert _wait_for(lambda: job.done > 0, timeout=10.0), "no data flowed"
        time.sleep(2.0)
        d1, t1 = job.done, time.time()
        time.sleep(5.0)
        d2, t2 = job.done, time.time()
        job.cancel()
        assert d2 > d1, "download stalled during measurement window"
        throughput = (d2 - d1) / max(t2 - t1, 1e-3)
        assert throughput <= 1.3 * limit, (
            f"throughput {throughput/1024:.1f} KB/s exceeds 1.3x limit "
            f"({1.3*limit/1024:.1f} KB/s)")
