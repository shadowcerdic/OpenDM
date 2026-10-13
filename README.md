# OpenDM — a multi-connection download manager

An independent implementation inspired by observed behavior of classic
download managers (see docs/methodology.md): multi-connection ranged
downloading with per-connection part files, resume-by-part-size, final
assembly, and a local task API for browser integration.

## Layout

- `opendm/` — Python package
  - `engine.py` — multi-connection download engine (Range/206, parts, resume, assemble)
  - `queue.py` — download queue manager (categories, states, persistence)
  - `gui.py` — PySide6 desktop application
  - `daemon.py` — local HTTP+WS API for the browser extension
- `extension/` — browser extension (MV3) adding a "Download with OpenDM" menu
- `docs/` — methodology and notes
- `tests/` — engine integration + security + scenario tests (pytest)

## Run

```bash
pip install PySide6 websockets pytest
python -m opendm.gui            # desktop app
python -m opendm.daemon         # local API (127.0.0.1:10010 / 10011)
pytest tests/ -q                # run test suite
```

First daemon run writes a random API token to the user config directory
(`~/.config/opendm/daemon.json`, mode 0600). The extension reads it from its
options page. All endpoints require the `X-OpenDM-Token` header.

## License

GPL-3.0-or-later (see LICENSE). Not affiliated with, endorsed by, or derived
from any commercial download manager; all trademarks belong to their owners.
