"""Item 12: cancel/pause semantics.

- terminal "canceled" state in STATES that _run never overwrites
- cancel() removes the .part file and sidecar; canceled jobs cannot resume
- resume() right after pause() waits for the old worker thread to exit
  (join with timeout) -- never silently no-ops while the old thread runs
"""
import os
import threading
import time

from opendm import engine
from opendm.queue import QueueManager, STATES


def _make_qm(tmp_path):
    return QueueManager(str(tmp_path / "downloads"),
                        state_file=str(tmp_path / "q.json"))


class BlockingDownload:
    """Stub engine.download: blocks until stop_event, then raises Paused."""

    def __init__(self):
        self.calls = 0
        self.max_concurrent = 0
        self._active = 0
        self._lock = threading.Lock()

    def __call__(self, url, dest=None, conns=4, status_cb=None, stop_event=None,
                 parts_cb=None, headers=None, proxy=None, **kw):
        with self._lock:
            self.calls += 1
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            while not (stop_event and stop_event.is_set()):
                time.sleep(0.01)
            raise engine.Paused()
        finally:
            with self._lock:
                self._active -= 1


class ErrorOnStopDownload:
    """Stub engine.download: raises a non-Paused error once stopped.

    Simulates the race where cancel() deletes the .part file out from
    under a running worker, which then fails with an OSError-like error.
    """

    def __call__(self, url, dest=None, conns=4, status_cb=None, stop_event=None,
                 parts_cb=None, headers=None, proxy=None, **kw):
        while not (stop_event and stop_event.is_set()):
            time.sleep(0.01)
        raise RuntimeError("boom: part file vanished")


def _wait_for(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_canceled_in_states_and_persisted(tmp_path):
    assert "canceled" in STATES
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/f.bin")
    j.cancel()
    assert j.state == "canceled"
    qm._save()
    qm2 = QueueManager(str(tmp_path / "downloads"),
                       state_file=str(tmp_path / "q.json"))
    assert qm2.by_id(j.id).state == "canceled"


def test_cancel_removes_part_and_sidecar(tmp_path):
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/f.bin")
    part = j.dest + ".opendm.part"
    assert os.path.exists(part)  # reserved at add() time (item 11)
    sidecar = j.dest + ".opendm"
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write("{}")
    j.cancel()
    assert j.state == "canceled"
    assert not os.path.exists(part)
    assert not os.path.exists(sidecar)


def test_cancel_while_downloading_sets_canceled_not_paused(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "download", BlockingDownload())
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/f.bin")
    j.start()
    assert _wait_for(lambda: j._thread is not None and j._thread.is_alive())
    j.cancel()
    j._thread.join(timeout=5)
    assert not j._thread.is_alive()
    assert j.state == "canceled"
    assert j.error == "canceled"


def test_cancel_after_pause_sets_canceled(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "download", BlockingDownload())
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/f.bin")
    j.start()
    assert _wait_for(lambda: j._thread is not None and j._thread.is_alive())
    j.pause()
    j.cancel()
    # the in-flight worker may still be exiting; wait for it to settle
    assert _wait_for(lambda: not j._thread.is_alive())
    assert j.state == "canceled"


def test_worker_error_after_cancel_still_canceled(monkeypatch, tmp_path):
    # cancel() deletes the .part file; if the running worker then fails
    # with a non-Paused error, the terminal state must still be "canceled".
    monkeypatch.setattr(engine, "download", ErrorOnStopDownload())
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/f.bin")
    j.start()
    assert _wait_for(lambda: j._thread is not None and j._thread.is_alive())
    # give the stub a moment to enter its wait loop
    time.sleep(0.05)
    j.cancel()
    j._thread.join(timeout=5)
    assert j.state == "canceled"


def test_resume_on_canceled_is_noop(monkeypatch, tmp_path):
    stub = BlockingDownload()
    monkeypatch.setattr(engine, "download", stub)
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/f.bin")
    j.cancel()
    j.resume()
    assert j.state == "canceled"
    assert stub.calls == 0
    assert j._thread is None or not j._thread.is_alive()


def test_resume_after_pause_waits_for_old_thread(monkeypatch, tmp_path):
    stub = BlockingDownload()
    monkeypatch.setattr(engine, "download", stub)
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/f.bin")
    j.start()
    assert _wait_for(lambda: stub.calls == 1)
    old_thread = j._thread
    assert old_thread.is_alive()

    j.pause()
    j.resume()  # must wait for the old worker, then start a fresh one

    assert not old_thread.is_alive(), \
        "resume() returned while the old worker thread was still running"
    assert j._thread is not old_thread
    assert j._thread.is_alive()
    assert j.state == "downloading"
    assert stub.max_concurrent == 1, "two worker threads ran concurrently"

    # cleanup: stop the new worker
    j.pause()
    j._thread.join(timeout=5)
    assert stub.calls == 2
