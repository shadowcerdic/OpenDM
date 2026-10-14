"""Item 15(b): real Job completion-callback mechanism.

- Job.add_on_complete(fn) / Job.on_complete list replaces the never-called
  post_finish attribute.
- _run invokes each callback after a successful completion
  (state == "completed"), inside a try/except that never breaks _run.
- post_finish is kept as a deprecated shim (gui.py still sets it; it is
  owned by the gui worker and must not be edited here).

Failing-first tests for the pre-fix tree.
"""
import warnings
from unittest import mock

import pytest

from opendm import engine
from opendm.queue import Job, QueueManager


def _make_qm(tmp_path):
    return QueueManager(str(tmp_path / "downloads"),
                        state_file=str(tmp_path / "q.json"))


def _stub_download_ok(monkeypatch):
    def fake(url, **kw):
        return {"sha256": "x" * 64, "size": 1234}
    monkeypatch.setattr(engine, "download", fake)


def _stub_download_err(monkeypatch):
    def fake(url, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(engine, "download", fake)


def test_on_complete_fires_on_success(monkeypatch, tmp_path):
    """add_on_complete callbacks fire after a successful completion."""
    qm = _make_qm(tmp_path)
    _stub_download_ok(monkeypatch)
    job = qm.add("http://127.0.0.1/f.bin")
    seen = []
    job.add_on_complete(seen.append)
    job._run()  # synchronous
    assert job.state == "completed"
    assert seen == [job], "callback must receive the completed job"


def test_on_complete_not_fired_on_error(monkeypatch, tmp_path):
    """Callbacks do not fire when the job ends in error."""
    qm = _make_qm(tmp_path)
    _stub_download_err(monkeypatch)
    job = qm.add("http://127.0.0.1/f.bin")
    seen = []
    job.add_on_complete(seen.append)
    job._run()  # synchronous
    assert job.state == "error"
    assert seen == [], "callback must not fire on error"


def test_on_complete_not_fired_on_cancel(monkeypatch, tmp_path):
    """Callbacks do not fire when cancel wins the race with a success."""
    qm = _make_qm(tmp_path)
    _stub_download_ok(monkeypatch)
    job = qm.add("http://127.0.0.1/f.bin")
    seen = []
    job.add_on_complete(seen.append)
    job._cancel_requested = True  # simulate cancel racing a successful return
    job._run()  # synchronous
    assert job.state == "canceled"
    assert seen == [], "callback must not fire on cancel"


def test_raising_callback_does_not_break_run(monkeypatch, tmp_path):
    """One raising callback must not break _run or later callbacks."""
    qm = _make_qm(tmp_path)
    _stub_download_ok(monkeypatch)
    job = qm.add("http://127.0.0.1/f.bin")

    order = []

    def bad(job):
        order.append("bad")
        raise ValueError("callback blew up")

    def good(job):
        order.append("good")

    job.add_on_complete(bad)
    job.add_on_complete(good)
    job._run()  # synchronous; must not raise
    assert job.state == "completed"
    assert order == ["bad", "good"], "later callbacks must still run"


def test_post_finish_deprecated_shim(monkeypatch, tmp_path):
    """Setting post_finish warns (deprecated) but still fires on completion.

    gui.py still sets job.post_finish (owned by the gui worker; must not be
    edited here), so the shim keeps it working while nudging migration to
    add_on_complete().
    """
    qm = _make_qm(tmp_path)
    _stub_download_ok(monkeypatch)
    job = qm.add("http://127.0.0.1/f.bin")

    assert getattr(job, "post_finish", None) is None  # gui.py reads before set

    fired = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        job.post_finish = lambda: fired.append(True)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), \
        "setting post_finish must emit a DeprecationWarning"

    job._run()  # synchronous
    assert job.state == "completed"
    assert fired == [True], "deprecated post_finish must fire on completion"
