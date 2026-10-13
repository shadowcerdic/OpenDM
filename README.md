# OpenDM v2.0 — High-Performance HTTP Download Accelerator

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/shadowcerdic/OpenDM?color=38bdf8&style=flat-square)](https://github.com/shadowcerdic/OpenDM/releases)
[![GitHub all releases download counter](https://img.shields.io/github/downloads/shadowcerdic/OpenDM/total?color=22c55e&style=flat-square)](https://github.com/shadowcerdic/OpenDM/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg?style=flat-square)](LICENSE)

An open-source, multi-connection HTTP download accelerator featuring dynamic in-flight range partitioning, lock-free pre-allocated disk I/O, and a modern Fluent PySide6 interface.

---

## ⚡ Direct Download (Windows)

No Python installation required. Standalone pre-packaged binaries for Windows (64-bit):

- 📦 **[Download OpenDM Standalone Installer (OpenDM-Setup.exe)](https://github.com/shadowcerdic/OpenDM/releases/latest/download/OpenDM-Setup.exe)**  
  *(Includes Python Embedded runtime, desktop shortcuts, background system tray support, and Chrome Native Messaging registration)*
- 🧩 **[Download Chrome Extension (OpenDM-Chrome-Extension.zip)](https://github.com/shadowcerdic/OpenDM/releases/latest/download/OpenDM-Chrome-Extension.zip)**  
  *(Manifest v3 extension for automatic browser interception and right-click download)*
- 🔍 **[SHA256 Checksums](https://github.com/shadowcerdic/OpenDM/releases/latest/download/SHA256SUMS.txt)**

---

## Architecture Overview

```
+-------------------------------------------------------------------------+
|                         OpenDM Desktop Client                           |
|  +--------------------+   +------------------------------------------+  |
|  | Modern Fluent GUI  |   | Core Engine (v2.0)                       |  |
|  | - PySide6 Dark UI  |   | - Lock-Free Pre-Allocation (truncate)    |  |
|  | - Slices Visualizer|   | - Thread-safe seek(cursor) writes        |  |
|  | - Speed Limiter    |   | - Dynamic Work Stealing (In-flight split)|  |
|  | - SOCKS5/HTTP Proxy|   | - Atomic Sidecar State (<file>.opendm)   |  |
|  | - Download Prompt  |   | - Protocol 200 Guard & If-Range ETag     |  |
|  +---------+----------+   +--------------------+---------------------+  |
|            |                                   |                        |
|            v                                   v                        |
|  +--------------------+               +------------------+              |
|  | Queue Manager      |               | Target File I/O  |              |
|  | - Atomic queue.json|               | (Zero assemble)  |              |
|  +---------+----------+               +------------------+              |
+------------|-----------------------------------|------------------------+
             |                                   |
             v                                   |
+-----------------------------+                  |
| Local Hardened Daemon       |                  |
| - 127.0.0.1:10010 HTTP      |                  |
| - Origin & Host validation  |                  |
| - X-OpenDM-Token Auth       |                  |
+--------------^--------------+                  |
               |                                 |
     Native Messaging (stdio)                    |
               |                                 |
+--------------v--------------+                  |
| Chrome MV3 Extension        |                  |
| - Native Token Bootstrap    |                  |
| - Context Menus             |                  |
| - Auto-catch onCreated      |                  |
+-----------------------------+                  |
```

### Key Engineering Features

1. **Pre-allocation & Lock-Free Parallel Disk I/O:**
   - Eliminates intermediate `.part` files and costly post-download linear `assemble()` operations.
   - Target files are pre-allocated using `truncate(total_size)` upon start.
   - Worker threads open independent file descriptors in `r+b` mode and write directly to their assigned byte ranges via `seek(cursor)`.
   - Sidecar metadata (`<filename>.opendm`) tracks `{start, cursor, end}` per segment and server `ETag` / `Last-Modified` headers atomically.

2. **Dynamic In-Flight Work Stealing:**
   - If a fast connection finishes its segment while another connection is slowed down by network latency or socket stalling, the idle worker actively steals the second half of the slowest segment.
   - Boundaries are dynamically split in-flight aligned to `PAGE_SIZE` (4096 bytes) without interrupting active sockets.

3. **Protocol Safety & Corruption Guards:**
   - **Mid-segment 200 OK Guard:** If a server responds with `200 OK` instead of `206 Partial Content` on a mid-file range (`start > 0`), the worker immediately aborts rather than writing byte-0 stream data into a mid-file offset.
   - **Resume Integrity:** Remote `ETag` and `Last-Modified` are validated on resume via `If-Range` headers to detect remote file updates before byte contamination can occur.

4. **Security & Browser Integration:**
   - Hardened local daemon with `Origin` and `Host` enforcement against DNS Rebinding and CSRF.
   - Chrome Native Messaging Host (`org.opendm.native_host`) bridges the extension securely over `stdin`/`stdout`, bootstrapping the session token without exposing secrets over HTTP or embedding them in source code.

5. **Desktop Experience:**
   - Built with PySide6 and styled in a clean Fluent Slate Dark aesthetic.
   - Real-time connection slices visualizer showing live segment transfers and work-stealing splits.
   - Interactive download prompts with asynchronous pre-flight metadata resolution (RFC 5987 / Content-Disposition parsing).
   - Dynamic global speed limiter and HTTP / SOCKS5 proxy configuration.

---

## Directory Layout

- `opendm/` — Core Python package
  - `engine.py` — High-performance pre-allocated multi-connection engine with work stealing
  - `queue.py` — Download queue manager, categorized states, and atomic persistence
  - `gui.py` — Modern PySide6 desktop interface, tray integration, prompt dialogs
  - `daemon.py` — Hardened local HTTP & WebSocket daemon
  - `native_host.py` — Chrome Native Messaging Host wrapper for secure token bootstrapping
  - `security.py` — Cryptographic token generation, Origin/Host validation
  - `sanitize.py` — RFC 5987 filename parsing and OS path sanitization
- `extension/` — Chrome Manifest v3 browser extension with Native Messaging support
- `tests/` — Automated test suite covering parallel I/O, work stealing, protocol guards, and security
- `installer.iss` — Standalone Inno Setup Windows installer script

---

## Installation & Running

### Requirements
- Python 3.10+
- Dependencies: `PySide6`, `websockets`, `pytest`

### Setup
```bash
# Clone the repository
git clone https://github.com/shadowcerdic/OpenDM.git
cd OpenDM

# Install dependencies
pip install PySide6 websockets pytest

# Run the desktop application
python -m opendm.gui

# Run automated tests
pytest tests/ -v
```

---

## License

GPL-3.0-or-later (see `LICENSE`). Clean-room open-source systems engineering implementation.
