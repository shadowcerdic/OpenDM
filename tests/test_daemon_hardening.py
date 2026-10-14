"""Hardening tests for opendm.daemon (spec item 16).

Each test is written to FAIL against the pre-fix daemon and PASS after it:
  (a) an idle raw socket must not block a second request (threaded server)
  (b) oversized request body -> 413
  (c) malformed/out-of-range numeric field -> 400, server stays alive
  (d) /api/v1/status reports opendm.__version__
  (e) wrong token -> 401 and hmac.compare_digest is used (code inspection)
Plus: Connection: close on OPTIONS + static routes, WS numeric validation,
      and a clear error (mentioning the port) on bind failure.
"""
import asyncio
import inspect
import json
import os
import select
import socket
import threading
import time
import urllib.request
import urllib.error

import pytest
import websockets

from opendm import daemon
from opendm import __version__

# Pre-fix daemon.py has no ThreadedHTTPServer; fall back to the old class so
# the tests still execute (and fail) instead of erroring at import time.
ServerClass = getattr(daemon, "ThreadedHTTPServer", None) or daemon.HTTPServer
MAX_BODY = getattr(daemon, "MAX_BODY_BYTES", 1024 * 1024)


@pytest.fixture(scope="module")
def hardening_server(tmp_path_factory):
    cfg_dir = tmp_path_factory.mktemp("cfg16")
    download_dir = tmp_path_factory.mktemp("dl16")
    daemon.DEST_DIR = str(download_dir)
    daemon.QM = daemon.QueueManager(str(download_dir),
                                    state_file=str(cfg_dir / "q.json"))

    httpd = ServerClass(("127.0.0.1", 0), daemon.HardenedApiHandler)
    http_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()

    loop = asyncio.new_event_loop()
    ws_port = None

    def run_ws():
        asyncio.set_event_loop(loop)

        async def main():
            nonlocal ws_port
            server = await websockets.serve(daemon.ws_handler, "127.0.0.1", 0)
            ws_port = server.sockets[0].getsockname()[1]
            await server.wait_closed()

        loop.run_until_complete(main())

    wt = threading.Thread(target=run_ws, daemon=True)
    wt.start()
    for _ in range(100):
        if ws_port is not None:
            break
        time.sleep(0.05)

    yield {"httpd": httpd, "http_port": http_port, "ws_port": ws_port}
    httpd.shutdown()
    httpd.server_close()


def _request(port, path, method="GET", headers=None, body=None, timeout=10):
    url = f"http://127.0.0.1:{port}{path}"
    hdrs = dict(headers or {})
    hdrs.setdefault("Host", f"127.0.0.1:{port}")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _auth():
    return {"X-OpenDM-Token": daemon.TOKEN}


# (a) concurrency ------------------------------------------------------------
def test_idle_socket_does_not_block_other_requests(hardening_server):
    port = hardening_server["http_port"]
    idle = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        code, _, body = _request(port, "/api/v1/status",
                                 headers=_auth(), timeout=8)
        assert code == 200
        assert json.loads(body)["ok"] is True
    finally:
        idle.close()


# (b) body cap ---------------------------------------------------------------
def _recv_all(s):
    """Read until EOF/RST/timeout; tolerate the server RST-ing an over-cap body."""
    resp = b""
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            resp += chunk
    except (socket.timeout, ConnectionResetError, BrokenPipeError,
            ConnectionAbortedError):
        pass
    return resp


def test_oversized_body_rejected_413(hardening_server):
    port = hardening_server["http_port"]
    s = socket.create_connection(("127.0.0.1", port), timeout=20)
    try:
        body_len = MAX_BODY + 1  # 1 MiB + 1 byte, declared via Content-Length
        s.sendall(
            f"POST /api/v1/download HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"X-OpenDM-Token: {daemon.TOKEN}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {body_len}\r\n"
            f"Connection: close\r\n\r\n".encode()
        )
        # The daemon rejects on the headers alone (it never reads an
        # over-cap body), so the 413 is already on its way. Read it BEFORE
        # sending any body bytes: on Windows, sending the body first races
        # with the server's abortive close and surfaces as WSAECONNABORTED
        # (WinError 10053) instead of a readable 413.
        r, _, _ = select.select([s], [], [], 5.0)
        assert r, "server did not reject the oversized Content-Length"
        resp = _recv_all(s)
    finally:
        s.close()
    status_line = resp.split(b"\r\n", 1)[0]
    assert b"413" in status_line, status_line
    assert b"MiB" in resp or b"too large" in resp


def test_chunked_oversized_body_rejected_413(hardening_server):
    port = hardening_server["http_port"]
    s = socket.create_connection(("127.0.0.1", port), timeout=20)
    try:
        s.sendall(
            f"POST /api/v1/download HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"X-OpenDM-Token: {daemon.TOKEN}\r\n"
            f"Transfer-Encoding: chunked\r\n"
            f"Content-Type: application/octet-stream\r\n"
            f"Connection: close\r\n\r\n".encode()
        )
        try:
            s.sendall(b"y" * (MAX_BODY + 100))
        except (BrokenPipeError, ConnectionResetError):
            pass
        resp = _recv_all(s)
    finally:
        s.close()
    status_line = resp.split(b"\r\n", 1)[0]
    assert b"413" in status_line, status_line


# (c) numeric validation ------------------------------------------------------
@pytest.mark.parametrize("bad", ["banana", 0, -1, 9999, 4.5, True, None, [4]])
def test_malformed_numeric_connections_rejected_400(hardening_server, bad):
    port = hardening_server["http_port"]
    payload = json.dumps({"url": "http://127.0.0.1:1/x.bin",
                          "connections": bad}).encode()
    code, _, body = _request(port, "/api/v1/download", method="POST",
                             headers={**_auth(),
                                      "Content-Type": "application/json"},
                             body=payload)
    assert code == 400
    data = json.loads(body)
    assert data["ok"] is False
    assert "connections" in data["error"].lower()


def test_server_alive_after_bad_request(hardening_server):
    # a valid request right after the malformed ones must still be served
    port = hardening_server["http_port"]
    payload = json.dumps({"url": "http://127.0.0.1:1/x.bin",
                          "connections": 8}).encode()
    code, _, body = _request(port, "/api/v1/download", method="POST",
                             headers={**_auth(),
                                      "Content-Type": "application/json"},
                             body=payload)
    assert code == 200
    assert json.loads(body)["ok"] is True


def test_non_dict_json_body_rejected_400(hardening_server):
    port = hardening_server["http_port"]
    code, _, body = _request(port, "/api/v1/download", method="POST",
                             headers={**_auth(),
                                      "Content-Type": "application/json"},
                             body=b"[1,2,3]")
    assert code == 400


# (d) version -----------------------------------------------------------------
def test_status_reports_real_version(hardening_server):
    port = hardening_server["http_port"]
    code, _, body = _request(port, "/api/v1/status", headers=_auth())
    assert code == 200
    data = json.loads(body)
    assert data["version"] == __version__


# (e) token comparison ---------------------------------------------------------
def test_wrong_token_rejected_401(hardening_server):
    port = hardening_server["http_port"]
    code, _, _ = _request(port, "/api/v1/status",
                          headers={"X-OpenDM-Token": "wrong-token"})
    assert code == 401


def test_token_comparison_uses_compare_digest():
    src = inspect.getsource(daemon)
    assert "compare_digest" in src


# Connection: close everywhere ------------------------------------------------
def test_options_sends_connection_close(hardening_server, monkeypatch):
    port = hardening_server["http_port"]
    # Pin origins to the real extension ID so the test does not depend on the
    # developer's local daemon.json.
    monkeypatch.setattr(daemon, "ALLOWED_ORIGINS",
                        ["chrome-extension://hnhmponoadleelhnecpijhnlpefkjflc"])
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        s.sendall(
            f"OPTIONS /api/v1/status HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Origin: chrome-extension://hnhmponoadleelhnecpijhnlpefkjflc\r\n"
            f"Connection: close\r\n\r\n".encode()
        )
        resp = _recv_all(s)
    finally:
        s.close()
    head = resp.split(b"\r\n\r\n", 1)[0].lower()
    assert b"204" in head.split(b"\r\n", 1)[0]
    assert b"connection: close" in head


def test_static_route_sends_connection_close(hardening_server):
    port = hardening_server["http_port"]
    www = daemon.EXT_WWW_DIR
    os.makedirs(www, exist_ok=True)
    fp = os.path.join(www, "updates.xml")
    with open(fp, "w", encoding="utf-8") as f:
        f.write("<gupdate/>")
    try:
        code, hdrs, _ = _request(port, "/ext/updates.xml")
        assert code == 200
        assert hdrs.get("Connection") == "close"
    finally:
        os.remove(fp)


# bind failure -----------------------------------------------------------------
def test_bind_failure_raises_clear_error():
    blocker = socket.socket()
    # NOTE: no SO_REUSEADDR here on purpose. On Windows, SO_REUSEADDR lets a
    # second socket bind the same port (unlike POSIX), so setting it on the
    # blocker would let the daemon bind successfully and the expected
    # RuntimeError would never fire. A listening socket without SO_REUSEADDR
    # still blocks a second bind on every platform.
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(Exception) as ei:
            ServerClass(("127.0.0.1", port), daemon.HardenedApiHandler)
        assert str(port) in str(ei.value)
    finally:
        blocker.close()


# WebSocket numeric validation + auth ------------------------------------------
@pytest.mark.asyncio
async def test_ws_bad_connections_rejected_and_alive(hardening_server):
    ws_port = hardening_server["ws_port"]
    uri = f"ws://127.0.0.1:{ws_port}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"cmd": "auth", "token": daemon.TOKEN}))
        res = json.loads(await ws.recv())
        assert res["type"] == "auth_ok"
        await ws.send(json.dumps({"cmd": "download",
                                  "url": "http://127.0.0.1:1/x.bin",
                                  "connections": "banana"}))
        err = json.loads(await ws.recv())
        assert "error" in err
        assert "connections" in err["error"].lower()
        # connection still usable afterwards
        await ws.send(json.dumps({"cmd": "query"}))
        q = json.loads(await ws.recv())
        assert q["type"] == "jobs_status"


@pytest.mark.asyncio
async def test_ws_wrong_token_rejected(hardening_server):
    ws_port = hardening_server["ws_port"]
    uri = f"ws://127.0.0.1:{ws_port}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"cmd": "auth", "token": "wrong-token"}))
        res = json.loads(await ws.recv())
        assert res["type"] == "auth_failed"
