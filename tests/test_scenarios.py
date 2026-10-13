"""Audit scenarios: no-range, multi-hop redirect, 10 concurrent downloads."""
import concurrent.futures
import hashlib
import http.server
import os
import re
import threading
from opendm import engine

PAYLOAD = os.urandom(200_000)
REF_HASH = hashlib.sha256(PAYLOAD).hexdigest()


class ScenarioHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        if self.path == "/norange":
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()

    def do_GET(self):
        if self.path == "/r1":
            self.send_response(301)
            self.send_header("Location", "/r2")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/r2":
            self.send_response(302)
            self.send_header("Location", "/data")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path in ("/data", "/norange"):
            rng = self.headers.get("Range")
            if rng and self.path != "/norange":
                m = re.match(r"bytes=(\d+)-(\d*)", rng)
                s = int(m.group(1))
                e = int(m.group(2) or len(PAYLOAD) - 1)
                e = min(e, len(PAYLOAD) - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {s}-{e}/{len(PAYLOAD)}")
                self.send_header("Content-Length", str(e - s + 1))
                self.end_headers()
                self.wfile.write(PAYLOAD[s:e + 1])
            else:
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.end_headers()
                self.wfile.write(PAYLOAD)

    def log_message(self, *a):
        pass


def _start_srv():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ScenarioHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def test_scenario_no_range(tmp_path):
    base = _start_srv()
    dest = tmp_path / "norange.bin"
    res = engine.download(f"{base}/norange", dest=str(dest), conns=4)
    assert res["sha256"] == REF_HASH
    assert res["connections"] == 1


def test_scenario_redirect_chain(tmp_path):
    base = _start_srv()
    dest = tmp_path / "redir.bin"
    res = engine.download(f"{base}/r1", dest=str(dest), conns=2)
    assert res["sha256"] == REF_HASH


def test_scenario_10_concurrent(tmp_path):
    base = _start_srv()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(engine.download, f"{base}/data",
                          dest=str(tmp_path / f"c{i}.bin"), conns=4)
                for i in range(10)]
        hashes = [f.result()["sha256"] for f in futs]
    assert all(h == REF_HASH for h in hashes)
