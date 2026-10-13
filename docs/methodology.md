# Methodology

## What this is

OpenDM is an independent implementation of generic download-manager
behaviors: multi-connection ranged downloading (HTTP Range / 206), part
files per connection, resume from part size, final assembly, and a local
API that a browser extension can call.

## How it was built (honest disclosure)

Analysis and implementation were both performed by a single automated
agent (Hermes, running REA-assisted tooling) on 2026-10-13, at the
request of the repository owner. This was **not** a team-separated
clean-room process: the same actor inspected reference binaries
(import tables, strings, protocol framing of a commercial download
manager's browser integration) and then wrote this code. No source code,
resources, or proprietary material from any product was copied; the
implementations here are original and behavior-level only. The formal
clean-room label was therefore intentionally removed from this project's
documentation.

## In scope

- The multi-connection download protocol: Range requests, part-file
  layout, resume-by-part-size, assembly.
- The extension↔app local protocol (independent JSON design).

## Out of scope (deliberately not analyzed, not implemented)

- Licensing, serial validation, or any activation/enforcement logic of
  any product.
- Kernel drivers / traffic interception of any kind.
- Any product-specific internal formats.

## Limitations

- The engine has not been tested on files > 2 GB (offsets are plain
  Python ints; unverified in practice).
- Windows/macOS have not been tested (see report).
- The GUI queue is not yet persisted (planned).
- No proxy / redirect-loop / TLS-client-certificate handling beyond
  what Python's urllib provides by default.
