"""Origin allow-list tests for opendm.daemon (spec item 17).

The daemon must trust exactly the configured extension ID(s); the old
chrome-extension://* and moz-extension://* wildcards are removed:
  - Origin: chrome-extension://<real-id>   -> accepted (pairing behavior kept)
  - Origin: chrome-extension://*           -> rejected (403)
  - Origin: chrome-extension://<wrong-id>  -> rejected (403)
  - Origin: moz-extension://<anything>      -> rejected (403, no Firefox ID configured)
  - module default / sanitizer contain no wildcard patterns
The real ID hnhmponoadleelhnecpijhnlpefkjflc is derived from the "key" in
extension/manifest.json and also appears in opendm/org.opendm.native_host.json
and the daemon config defaults in opendm/security.py.
"""
import asyncio
import socket
import threading
import time
import urllib.request
import urllib.error

import pytest
import websockets

from opendm import daemon

# Pre-fix daemon has no DEFAULT_ALLOWED_ORIGINS; fall back to the old wildcard
# default so these tests fail (not error) against the pre-fix code.
_PRE_WILDCARD_DEFAULT = ["chrome-extension://*"]
REAL_ID = "hnhmponoadleelhnecpijhnlpefkjflc"
WRONG_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _default_origins():
    return list(getattr(daemon, "DEFAULT_ALLOWED_ORIGINS", _PRE_WILDCARD_DEFAULT))


@pytest.fixture(scope="module")
def origins_server(tmp_path_factory):
    cfg_dir = tmp_path_factory.mktemp("cfg17")
    download_dir = tmp_path_factory.mktemp("dl17")
    daemon.DEST_DIR = str(download_dir)
    daemon.QM = daemon.QueueManager(str(download_dir),
                                    state_file=str(cfg_dir / "q.json"))

    httpd = daemon.ThreadedHTTPServer(("127.0.0.1", 0), daemon.HardenedApiHandler)
    http_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()

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

    threading.Thread(target=run_ws, daemon=True).start()
    for _ in range(100):
        if ws_port is not None:
            break
        time.sleep(0.05)

    yield {"httpd": httpd, "http_port": http_port, "ws_port": ws_port}
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def exact_id_origins(monkeypatch):
    """Pin ALLOWED_ORIGINS to the daemon's own default (exact IDs only)."""
    monkeypatch.setattr(daemon, "ALLOWED_ORIGINS", _default_origins())
    return daemon.ALLOWED_ORIGINS


def _options_origin(port, origin):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        s.sendall(
            f"OPTIONS /api/v1/status HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Origin: {origin}\r\n"
            f"Connection: close\r\n\r\n".encode()
        )
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    return int(data.split(b"\r\n", 1)[0].split(b" ")[1])


def _request(port, path, headers=None):
    url = f"http://127.0.0.1:{port}{path}"
    hdrs = dict(headers or {})
    hdrs.setdefault("Host", f"127.0.0.1:{port}")
    req = urllib.request.Request(url, data=None, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# -- HTTP --------------------------------------------------------------------
def test_real_extension_id_origin_accepted(origins_server, exact_id_origins):
    port = origins_server["http_port"]
    code = _options_origin(port, f"chrome-extension://{REAL_ID}")
    assert code == 204  # preflight passes -> pairing behavior preserved


def test_literal_chrome_extension_wildcard_rejected(origins_server, exact_id_origins):
    port = origins_server["http_port"]
    code = _options_origin(port, "chrome-extension://*")
    assert code == 403


def test_wrong_chrome_extension_id_rejected(origins_server, exact_id_origins):
    port = origins_server["http_port"]
    code = _options_origin(port, f"chrome-extension://{WRONG_ID}")
    assert code == 403


def test_wrong_chrome_extension_id_rejected_on_get(origins_server, exact_id_origins):
    port = origins_server["http_port"]
    code, _, body = _request(
        port, "/api/v1/status",
        headers={"X-OpenDM-Token": daemon.TOKEN,
                 "Origin": f"chrome-extension://{WRONG_ID}"})
    assert code == 403
    assert b"Origin not allowed" in body


def test_moz_extension_origin_rejected(origins_server, exact_id_origins):
    port = origins_server["http_port"]
    code = _options_origin(port, "moz-extension://some-firefox-uuid")
    assert code == 403


# -- WebSocket ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_websocket_wrong_extension_id_rejected(origins_server, exact_id_origins):
    ws_port = origins_server["ws_port"]
    uri = f"ws://127.0.0.1:{ws_port}"
    async with websockets.connect(uri, origin=f"chrome-extension://{WRONG_ID}") as ws:
        try:
            # Post-fix the server closes with 4403 right away; pre-fix it
            # accepts the origin and waits up to 5s for the auth message.
            await asyncio.wait_for(ws.recv(), timeout=10)
        except websockets.exceptions.ConnectionClosed as e:
            # websockets>=13.1 deprecates .code; .rcvd.code is warning-free.
            close_code = e.rcvd.code if e.rcvd is not None else None
            assert close_code == 4403, f"expected 4403 close, got {close_code}"
            return
        except asyncio.TimeoutError:
            pass
        pytest.fail("wrong-id extension origin was accepted by the WS server")


# -- defaults ----------------------------------------------------------------
def test_default_origins_contain_no_wildcards():
    default = _default_origins()
    assert default, "default allowed_origins must not be empty"
    assert not any("*" in o for o in default), f"wildcard remains in {default}"
    assert f"chrome-extension://{REAL_ID}" in default


def test_stale_config_wildcards_are_sanitized():
    sanitize = getattr(daemon, "_sanitize_origins", None)
    if sanitize is None:
        pytest.fail("daemon._sanitize_origins missing: stale wildcards not filtered")
    cleaned = sanitize([
        f"chrome-extension://{REAL_ID}",
        "chrome-extension://*",
        "moz-extension://*",
    ])
    assert cleaned == [f"chrome-extension://{REAL_ID}"]
    # Empty / all-wildcard config falls back to the exact ID(s) only
    assert sanitize(["chrome-extension://*", "moz-extension://*"]) == _default_origins()
