"""Integration tests for the OpenDM engine (live HTTP)."""
import hashlib
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opendm import engine

URL = os.environ.get("OPENDM_TEST_URL", "http://speedtest.tele2.net/10MB.zip")


def _reference(path):
    urllib.request.urlretrieve(URL, path)
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_download_and_hash():
    with tempfile.TemporaryDirectory() as d:
        dest = os.path.join(d, "t.bin")
        ref = _reference(os.path.join(d, "ref.bin"))
        res = engine.download(URL, dest=dest, conns=4)
        assert res["sha256"] == ref, f"{res['sha256']} != {ref}"
        assert os.path.getsize(dest) == res["size"]
        assert not any(f.endswith(".part") for f in os.listdir(
            os.path.join(d, engine.PART_DIR)))
        print("download+hash: OK")


def test_resume():
    with tempfile.TemporaryDirectory() as d:
        dest = os.path.join(d, "r.bin")
        meta = engine.probe(URL)
        ranges = engine.plan_ranges(meta["size"], 4)
        workdir = os.path.join(d, engine.PART_DIR)
        os.makedirs(workdir)
        parts = [os.path.join(workdir, f"r.bin.{i:02d}.part") for i in range(4)]
        for i, (s, e) in enumerate(ranges):
            engine._fetch_chunk(URL, s, min(s + 300_000, e), parts[i])
        res = engine.download(URL, dest=dest, conns=4)
        ref = _reference(os.path.join(d, "ref2.bin"))
        assert res["sha256"] == ref
        print("resume: OK")


def test_pause_and_resume():
    import threading
    with tempfile.TemporaryDirectory() as d:
        dest = os.path.join(d, "p.bin")
        stop = threading.Event()
        err = {}

        def run():
            try:
                engine.download(URL, dest=dest, conns=4, stop_event=stop)
            except engine.Paused:
                err["paused"] = True
        t = threading.Thread(target=run)
        t.start()
        import time; time.sleep(0.6)
        stop.set()
        t.join()
        assert err.get("paused"), "expected Paused"
        res = engine.download(URL, dest=dest, conns=4)
        ref = _reference(os.path.join(d, "ref3.bin"))
        assert res["sha256"] == ref
        print("pause+resume: OK")


if __name__ == "__main__":
    test_download_and_hash()
    test_resume()
    test_pause_and_resume()
    print("ALL TESTS PASSED")
