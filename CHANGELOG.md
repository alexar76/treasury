# Changelog

All notable changes to this project are documented in this file. Earlier releases: the git history.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.5] - 2026-11-11

### Fixed

- `/explain` is a dry run. It called the settling `authorize`, so it paid the bounty and recorded a decision, and the
  caller's later `/authorize` was refused as a duplicate with a different decision record. It now decides with
  `settle=False, record=False` and says `"dry_run": true`. Found by the money-flow audit of 2026-11-11
  (satellites-6).
- Requires `aimarket-momus>=0.2.1`. Its `PayoutGate.authorize` is the first to take `record=`; with 0.2.0 the dry
  run raises `TypeError`. Publish MOMUS 0.2.1 first.

## [0.1.4] - 2026-10-28

### Security

- Keyless HTTP now requires explicit `TREASURY_LOCAL_RELAY=1` trust, even for a same-user loopback socket. A multiprocess proxy or tunnel cannot be reliably distinguished from a local client by process name or listening sockets; the old heuristic missed workers whose listener belonged to a parent. The default therefore refuses both. With explicit trust, the caller socket must still belong to the server user; without the socket table (macOS/Windows), the additional `TREASURY_LOCAL_UNVERIFIED=1` opt-in is required. A configured token and production authentication always take precedence. Addresses, Host, Origin and forwarding-header restrictions still apply. Reported by Vulnetix (GCVE-110-VULN-PYPI-2026-000642).
