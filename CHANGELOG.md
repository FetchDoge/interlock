# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/). The single source of truth for
the current version is `interlock.__version__` (mirrored into `VERSION` by
`scripts/release.sh`).

## [Unreleased]
### Added
- `Interlock.execute(lease, action, target, effect)`: the gate is re-proven at the commit
  point, atomically with registering the effect as in flight. `EStop.engage()` and
  `LeaseManager.revoke()` are ordered against it and return only after committed effects
  finish, closing the check-then-act race where an e-stop or revocation landing during a
  slow preparation still let the effect run (gauntlet `revoke-during-spend`). `permit()`
  keeps its entry-only check and now appends a `violations` record if authority is lost
  while the block runs. Ledger `permits`/`denials` records gain a `phase` field (`entry`
  or `commit`). Regression tests: `tests/test_inflight.py`.

### Fixed
- Codex review fixes on the commit-point gate (PR #18), each with a regression test in
  `tests/test_inflight.py` unless noted:
  - Gating fault reports are ordered against commit registration: `FaultBus` takes the
    in-flight gate's lock on `report()`/`clear()`, so a major/critical fault can no
    longer land between `execute()`'s check and its registration and be missed.
  - Concurrent halts raised from inside effects no longer deadlock: a draining halt
    does not wait on effects whose threads are themselves inside a halt. Applies to
    `estop.engage()` and to mutual in-effect `leases.revoke()` calls.
  - `Ledger` appends and `close()` are serialised with an internal lock, so concurrent
    `execute()` calls can no longer write duplicate series descriptors or leave the
    closing index undercounting records.
  - `permit()` records a `violations` entry when the e-stop is engaged and released
    again inside the block; the exit check compares `EStop.engagements` counts instead
    of only the final `is_engaged` state.
  - `execute()` refuses deferred effects: coroutine/generator callables raise
    `TypeError` before the gate runs, and a deferred result from a plain callable is
    closed unstarted. Previously the callable merely created the deferred object, the
    in-flight registration was dropped immediately, and the body could run after a
    halt had returned.
  - `execute()` establishes its in-flight cleanup guard before registration, so an
    asynchronous exception in the gap after `enter()` cannot leak a stale in-flight
    entry that blocks later halts forever (structural fix; the interleaving needs an
    async exception in a two-step window and has no deterministic regression test).
  - The two scheduled-halt tests in `tests/test_inflight.py` synchronize on an event
    instead of assuming a `threading.Timer` fired within a fixed sleep.
  - README and the quickstart guide route irreversible effects through `execute()`
    instead of `permit()` blocks.
- `Ledger(path)` no longer erases an existing audit log. A path opens in append mode by
  default, so reopening a ledger keeps every earlier run and starts a new self-describing
  segment. `Ledger(path, mode="w")` truncates deliberately; any other mode raises
  `ValueError`. Regression tests: `tests/test_ledger_reopen.py`.
- The ledger flushes every line as it writes it. A record now survives the writing process
  crashing before `close()`; before, records sat in the process buffer and died with it.
  `close()` fsyncs a file the ledger opened itself. Regression tests:
  `tests/test_ledger_durability.py`.
- `Interlock.permit()` rejects any lease it didn't issue. Before, a hand-built `Lease` with a
  far-future `expires_at` passed the gate. `LeaseManager` now keeps its own record of every
  lease it grants, and that record decides expiry and revocation. A hand-built lease, a
  lease from another manager, or a lease whose resource, holder, TTL, or grant time was
  edited after issue raises `LeaseNotIssued`, and the denial is logged with reason
  `lease_not_issued`. Editing `expires_at` or clearing `revoked` on a `Lease` object no
  longer extends or restores authority. `keepalive()` rejects unissued leases. Regression
  tests: `tests/test_lease_issuance.py`.

### Added
- `interlock.LeaseNotIssued`, a subclass of `InterlockError`, plus `LeaseManager.issued()`
  and `LeaseManager.is_revoked()`.
- 90-second demonstrator (`scripts/demo_90s.py`, transcript in `demo/TRANSCRIPT.md`): a narrated
  end-to-end run of grant, keepalive, permit, expiry denial, resource-scoped fault gate,
  global e-stop, provenance-stamped claim, and ledger audit. Exits non-zero on any unexpected
  behavior.

## [0.1.0] — 2026-09-27
### Added
- `interlock.leases`: time-bounded authority grants with keepalive renewal and expiry.
- `interlock.estop`: global halt — one engagement suspends all permits; release is explicit.
- `interlock.faults`: fault bus with severity; gating faults refuse permits on affected resources.
- `interlock.claims`: provenance-stamped assertions (provenance, authority, review date, falsifier).
- `interlock.interfaces`: handoff records stating guarantees, constraints, ownership, failure behavior.
- `interlock.ledger`: self-describing run ledger (format descriptor, per-series schemas, closing index).
- Concept documentation in `docs/concepts/`, quickstart in `docs/guides/`.
- CI (Python 3.11–3.13, pytest, ruff), issue/PR templates, release script.
