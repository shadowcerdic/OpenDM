# OpenDM Report

**Date:** 2026-10-13 · **Author:** Hermes Agent (task commissioned by the repo owner)

## Scope

**In scope:** the multi-connection download protocol (Range/resume/part
files/assembly) and the browser-extension↔app local protocol.

**Out of scope (not analyzed, not implemented):** product licensing,
serials, activation logic of any kind, and kernel-level drivers or
traffic interception.

## What was done

1. **Tooling:** the `rea-agents` CLI (6.4.0) was installed and exercised
   on reference material (a commercial download manager's shipped files)
   for behavior-level understanding of ranged downloading and extension
   integration. Ghidra 12.1.4 was driven through REA on the analysis
   machine for two helper binaries (function inventories and one
   decompiled COM entry point).
2. **Rebuild:** OpenDM was written from scratch as an independent
   implementation: `engine.py` (probe/range planning/part
   files/resume/assembly/SHA-256), `queue.py` (jobs, categories),
   `gui.py` (PySide6 app), `daemon.py` (local HTTP+WS API),
   `extension/` (MV3).
3. **Verification:** engine tests (hash match vs reference, resume,
   pause/resume), live GUI run under X11 with three simultaneous
   downloads, plus a later security/robustness audit (token auth,
   origin checks, filename sanitization, persistence, header support —
   see tests/).

See `docs/methodology.md` for the honest single-agent disclosure and
the limitations section.
