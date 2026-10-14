"""Item 11: atomic destination reservation.

Choosing a job's destination must be atomic: a class-level lock plus
exclusive creation of the .part file, checking both final and .part names,
including names of jobs loaded from queue.json at startup.
"""
import os
import threading

from opendm.queue import QueueManager


def _make_qm(tmp_path, name="q.json"):
    return QueueManager(str(tmp_path / "downloads"), state_file=str(tmp_path / name))


def test_concurrent_add_same_filename_gets_distinct_dests(tmp_path):
    qm = _make_qm(tmp_path)
    url = "http://example.com/file.bin"
    jobs, errors = [], []

    def worker():
        try:
            jobs.append(qm.add(url))
        except Exception as ex:  # noqa: BLE001 - collect, don't kill the thread
            errors.append(ex)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(jobs) == 20
    dests = [j.dest for j in jobs]
    assert len(set(dests)) == 20, f"duplicate dests: {dests}"


def test_part_file_blocks_name_reuse(tmp_path):
    qm = _make_qm(tmp_path)
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    # leftover .part from a crashed run must block the bare name
    (dest_dir / "file.bin.opendm.part").write_bytes(b"")
    j = qm.add("http://example.com/file.bin")
    assert os.path.basename(j.dest) != "file.bin"
    assert os.path.basename(j.dest) == "file (1).bin"


def test_part_file_created_exclusively_at_add(tmp_path):
    qm = _make_qm(tmp_path)
    j = qm.add("http://example.com/file.bin")
    assert os.path.exists(j.dest + ".opendm.part")


def test_loaded_job_names_are_reserved(tmp_path):
    qm1 = _make_qm(tmp_path)
    j1 = qm1.add("http://example.com/file.bin")
    first = os.path.basename(j1.dest)
    assert first == "file.bin"
    qm1._save()

    # simulate app restart: a new manager loads queue.json
    qm2 = QueueManager(str(tmp_path / "downloads"), state_file=str(tmp_path / "q.json"))
    assert len(qm2.jobs) == 1
    j2 = qm2.add("http://example.com/file.bin")
    assert os.path.basename(j2.dest) != first
