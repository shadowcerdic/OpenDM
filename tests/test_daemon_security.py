"""Integration tests for the running daemon security controls."""
import asyncio
import json
import os
import threading
import time
import urllib.request
import urllib.error
import pytest
import websockets

from opendm.daemon import HardenedApiHandler, HTTPServer, ws_handler, CONFIG, TOKEN


@pytest.fixture(scope="module")
def daemon_server(tmp_path_factory):
    # run test daemon on dynamic ports
    cfg_dir = tmp_path_factory.mktemp("cfg")
    download_dir = tmp_path_factory.mktemp("downloads")
    from opendm import daemon
    daemon.DEST_DIR = str(download_dir)
    daemon.QM = daemon.QueueManager(str(download_dir), state_file=cfg_dir / "q.json")

    httpd = HTTPServer(("127.0.0.1", 0), HardenedApiHandler)
    http_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # run ws
    loop = asyncio.new_event_loop()
    ws_port = None

    def run_ws():
        asyncio.set_event_loop(loop)
        async def main():
            nonlocal ws_port
            server = await websockets.serve(ws_handler, "127.0.0.1", 0)
            ws_port = server.sockets[0].getsockname()[1]
            await server.wait_closed()
        loop.run_until_complete(main())

    t = threading.Thread(target=run_ws, daemon=True)
    t.start()
    for _ in range(50):
        if ws_port is not None:
            break
        time.sleep(0.05)

    return {"http_port": http_port, "ws_port": ws_port, "download_dir": str(download_dir)}


def _request(port, path, method="GET", headers=None, body=None):
    url = f"http://127.0.0.1:{port}{path}"
    hdrs = headers or {}
    if "Host" not in hdrs:
        hdrs["Host"] = f"127.0.0.1:{port}"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_daemon_without_token_rejected(daemon_server):
    port = daemon_server["http_port"]
    code, _, body = _request(port, "/api/v1/status")
    assert code == 401
    assert b"Missing or invalid X-OpenDM-Token" in body


def test_daemon_with_valid_token_accepted(daemon_server):
    port = daemon_server["http_port"]
    code, _, body = _request(port, "/api/v1/status", headers={"X-OpenDM-Token": TOKEN})
    assert code == 200
    data = json.loads(body)
    assert data["ok"] is True
    assert data["service"] == "opendm"


def test_daemon_forged_origin_rejected(daemon_server):
    port = daemon_server["http_port"]
    code, hdrs, body = _request(port, "/api/v1/status",
                                headers={"X-OpenDM-Token": TOKEN, "Origin": "https://evil.example"})
    assert code == 403
    assert b"Origin not allowed" in body


def test_daemon_extension_origin_accepted(daemon_server):
    port = daemon_server["http_port"]
    code, hdrs, body = _request(port, "/api/v1/status",
                                headers={"X-OpenDM-Token": TOKEN, "Origin": "chrome-extension://myextensionid"})
    assert code == 200
    assert hdrs.get("Access-Control-Allow-Origin") == "chrome-extension://myextensionid"


def test_daemon_forged_host_rejected(daemon_server):
    port = daemon_server["http_port"]
    code, _, body = _request(port, "/api/v1/status",
                                headers={"X-OpenDM-Token": TOKEN, "Host": "attacker.example:10010"})
    assert code == 400
    assert b"rebinding rejected" in body


def test_daemon_ignores_request_dest_field(daemon_server):
    port = daemon_server["http_port"]
    # Request attempts to escape into /tmp/escaped_payload.bin
    payload = json.dumps({
        "url": "http://127.0.0.1:1/nonexistent.bin",
        "dest": "/tmp/escaped_payload.bin",
        "dest_dir": "/tmp",
        "path": "/etc/evil",
        "connections": 1,
    }).encode()
    code, _, body = _request(port, "/api/v1/download", method="POST",
                             headers={"X-OpenDM-Token": TOKEN, "Content-Type": "application/json"},
                             body=payload)
    assert code == 200
    data = json.loads(body)
    # The saved_to_dir field confirms the daemon stored it in the server-configured folder
    assert data["saved_to_dir"] == daemon_server["download_dir"]
    assert not os.path.exists("/tmp/escaped_payload.bin")


@pytest.mark.asyncio
async def test_websocket_forged_origin_rejected(daemon_server):
    ws_port = daemon_server["ws_port"]
    uri = f"ws://127.0.0.1:{ws_port}"
    try:
        async with websockets.connect(uri, origin="https://evil.example") as ws:
            # Should be closed by server immediately or fail handshake
            await ws.recv()
            assert False, "expected connection rejection"
    except (websockets.exceptions.InvalidStatusCode,
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.InvalidHandshake):
        pass  # expected rejection


@pytest.mark.asyncio
async def test_websocket_auth_handshake(daemon_server):
    ws_port = daemon_server["ws_port"]
    uri = f"ws://127.0.0.1:{ws_port}"
    # Valid origin (or absent) + auth message with token
    async with websockets.connect(uri, origin="chrome-extension://localext") as ws:
        await ws.send(json.dumps({"cmd": "auth", "token": TOKEN}))
        res = json.loads(await ws.recv())
        assert res["type"] == "auth_ok"
        await ws.send(json.dumps({"cmd": "query"}))
        q = json.loads(await ws.recv())
        assert q["type"] == "jobs_status"
