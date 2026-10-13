"""Test that engine forwards Cookie and Referer to the remote server."""
import http.server
import threading
from opendm import engine


class AuthHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        c = self.headers.get("Cookie") or ""
        ref = self.headers.get("Referer") or ""
        if "auth_session=secret123" not in c or "https://trusted.example" not in ref:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", "20")
        self.end_headers()

    def do_GET(self):
        c = self.headers.get("Cookie") or ""
        ref = self.headers.get("Referer") or ""
        if "auth_session=secret123" not in c or "https://trusted.example" not in ref:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "20")
        self.end_headers()
        self.wfile.write(b"01234567890123456789")

    def log_message(self, *a):
        pass


def test_download_with_cookies_and_referer(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AuthHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/protected.bin"
    dest = tmp_path / "protected.bin"

    # Without headers -> fails with 403
    try:
        engine.download(url, dest=str(dest), conns=1)
        assert False, "expected 403 failure without headers"
    except Exception:
        pass

    # With headers -> succeeds
    hdrs = {
        "Cookie": "auth_session=secret123",
        "Referer": "https://trusted.example/page",
    }
    res = engine.download(url, dest=str(dest), conns=1, headers=hdrs)
    assert res["size"] == 20
    assert dest.read_bytes() == b"01234567890123456789"
