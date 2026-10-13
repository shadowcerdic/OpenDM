"""Queue persistence and restart test."""
import json
from opendm.queue import QueueManager


def test_queue_persists_across_restart(tmp_path):
    sf = tmp_path / "queue.json"
    dest = tmp_path / "downloads"

    # Process 1 creates a job and sets its state
    qm1 = QueueManager(str(dest), state_file=sf)
    j = qm1.add("http://example.com/file.zip", conns=4)
    j.total = 1000
    j.done = 500
    j.state = "paused"
    qm1._save()

    assert sf.exists()
    raw = json.loads(sf.read_text())
    assert len(raw["jobs"]) == 1
    assert raw["jobs"][0]["filename"] == "file.zip"

    # Process 2 (simulating app restart) loads the state file
    qm2 = QueueManager(str(dest), state_file=sf)
    assert len(qm2.jobs) == 1
    loaded = qm2.jobs[0]
    assert loaded.id == j.id
    assert loaded.url == "http://example.com/file.zip"
    assert loaded.state == "paused"
    assert loaded.total == 1000
    assert loaded.done == 500
