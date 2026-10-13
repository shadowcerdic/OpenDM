#!/usr/bin/env python3
"""OpenDM Native Messaging Host for Chrome / Chromium browsers.

Communicates over stdin/stdout with 32-bit length prefixed JSON messages.
Allows the Chrome extension to securely bootstrap the local session token
without transmitting it over unauthenticated HTTP or embedding it in public code.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from opendm.security import load_or_create_config, config_dir

# On Windows, stdin and stdout default to text mode (\r\n translation will corrupt 32-bit length headers)
if sys.platform == "win32":
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def read_message() -> dict | None:
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    message_length = struct.unpack("@I", raw_length)[0]
    raw_data = sys.stdin.buffer.read(message_length)
    if len(raw_data) < message_length:
        return None
    return json.loads(raw_data.decode("utf-8"))


def send_message(message: dict):
    data = json.dumps(message).encode("utf-8")
    length = struct.pack("@I", len(data))
    sys.stdout.buffer.write(length)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main():
    while True:
        try:
            msg = read_message()
            if msg is None:
                break
            cmd = msg.get("cmd")
            if cmd == "get_token":
                cfg = load_or_create_config()
                token = cfg.get("token")
                port = cfg.get("http_port", 10010)
                send_message({
                    "ok": True,
                    "token": token,
                    "http_port": port,
                })
            elif cmd == "ping":
                send_message({"ok": True, "pong": True})
            else:
                send_message({"ok": False, "error": f"Unknown command: {cmd}"})
        except Exception as ex:
            send_message({"ok": False, "error": str(ex)})
            break


if __name__ == "__main__":
    main()
