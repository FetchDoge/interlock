# Interlock

**Interlock replaces ambient agent authority with leases that expire on their own clock. A global e-stop and a fault bus gate every action, and the gate fails closed.**

[![CI](https://github.com/FetchDoge/interlock/actions/workflows/ci.yml/badge.svg)](https://github.com/FetchDoge/interlock/actions/workflows/ci.yml) [![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue.svg)](pyproject.toml) [![Release](https://img.shields.io/github/v/release/FetchDoge/interlock)](https://github.com/FetchDoge/interlock/releases)

Agents act with ambient, unexpiring authority. Filesystem, credentials, and network access get granted once for the whole session and revoked only by killing the process. The 2026 agent stack has an authority problem, not a capability problem.

Mobile robots solved this a decade ago. Boston Dynamics' Spot admits intent through a narrow gate of *command plus lease plus clock*, with keepalives, a global e-stop, and a fault taxonomy built into the structure rather than written as policy. Interlock transfers that architecture to software agents as a zero-dependency Python substrate.

## What it guarantees

Inside the substrate, **no action executes without a live lease, a clear e-stop, and no gating fault.** The gate — `Interlock.execute()` at the commit point, `Interlock.permit()` at block entry — checks in this order and raises on the first failure:

1. e-stop clear, else `EStopEngaged`
2. lease issued by this `Interlock` and unaltered, else `LeaseNotIssued`; then unrevoked and unexpired, else `LeaseRevoked` or `LeaseExpired`
3. no `major` or `critical` fault on the lease's resource, else `FaultActive`

- **Authority expires.** A lease exists only from `grant()` and dies at `expires_at` unless a keepalive renews it.
- **A keepalive cannot resurrect a dead lease.** Renewal after expiry or revocation raises.
- **Only issued leases pass.** The lease manager keeps its own record of every lease it grants, and that record decides expiry and revocation. Editing a `Lease` object grants nothing.
- **Revocation is permanent.**
- **E-stop release is an operator decision, never a timeout.** One engagement refuses every permit until an explicit `release()`.
- **Every permit and every denial is recorded** in the ledger's `permits` or `denials` series, with the reason.
- **Claims carry provenance:** source, authority (the lease id), review date, and a falsifier.

## Quickstart

Interlock is not published on PyPI, so `pip install interlock` does not install this package. Install it from a clone:

```bash
git clone https://github.com/FetchDoge/interlock.git && cd interlock
pip install -e ".[test]" && pytest -q
```

```python
from interlock import Interlock

ilk = Interlock()

# GRANT: authority comes into existence only here, and it expires.
lease = ilk.grant(resource="docs:write", holder="research-agent", ttl=1800)

# EXERCISE: every action passes the gate. execute() re-proves the gate at
# the commit point, atomically with starting the effect — a halt that lands
# while the action is being prepared refuses it instead of racing it.
ilk.execute(lease, "write", "brief.md", effect)  # raises EStopEngaged, LeaseNotIssued, LeaseRevoked, LeaseExpired, or FaultActive otherwise

# A major or critical fault refuses further permits on the affected resource.
ilk.faults.report(code="TOOL_TIMEOUT", severity="major", resource="web:read")

# AUDIT: claims carry provenance, authority, review date, falsifier.
claim = ilk.claim(
    text="14 pages fetched; 2 endpoints timed out.",
    provenance="tool:web-fetch",
    authority=lease.id,
    review_after_days=7,
    falsifier="re-fetch returns different content",
)  # auto-appended to the ledger's "claims" series
```

The fastest path to a first lease is [`docs/guides/quickstart.md`](docs/guides/quickstart.md).

**90-second demonstrator:** `python3 scripts/demo_90s.py` runs the substrate end to end, narrated: grant, keepalive, permit, expiry denial, resource-scoped fault gate, global e-stop, provenance-stamped claim, and ledger audit. It exits non-zero if any step misbehaves. Expected output: [`demo/TRANSCRIPT.md`](demo/TRANSCRIPT.md).

## How it fails

| Condition | Behavior |
|---|---|
| E-stop engaged | Every gate check raises `EStopEngaged` and logs a `denials` record |
| Lease expired (no keepalive in time) | `LeaseExpired`. A late keepalive also raises |
| Lease not issued by this `Interlock` (hand-built, from another instance, or edited after issue) | `LeaseNotIssued` and a `denials` record with reason `lease_not_issued` |
| Lease revoked | `LeaseRevoked`, permanently. Resetting `lease.revoked` on the object does not restore it |
| `major`/`critical` fault on the resource | `FaultActive` until the fault is cleared. `minor` faults are recorded but do not gate |

**Known limits:**
- `permit()` checks at block entry only. An e-stop engaged *during* a `with` block does not interrupt code already running inside it; the block's exit logs a `violations` record and the next permit is refused. For an effect that must not start once authority is gone, use `execute()`: it re-proves the gate at the commit point, and an e-stop or revocation returns only after already-committed effects have finished.
- State is in-process and in-memory. Interlock gates actions routed through it and cannot stop an agent that bypasses the substrate. It is one layer of defense in depth.
- The ledger is append-only and self-describing JSONL (format descriptor, per-series schemas, closing index). Reopening a path appends a new segment and never truncates unless you pass `mode="w"`. Every record is flushed as it is written, so it survives a crash of the writing process. Only `close()` fsyncs, so a power loss can still drop the last records. It is **not** hash-chained or signed, so treat it as an audit log, not tamper evidence.

[`CHARTER.md`](CHARTER.md) has the full design rationale and safety caveats.

## Evidence

- **68 tests pass:** `pytest -q`, re-run 2026-10-15 on `main` at `68f2d6e`. CI runs the same suite on Python 3.11, 3.12, and 3.13, plus `ruff` lint and format checks and a version-consistency check.
- The quickstart above was executed on 2026-10-07: the gate refused a permit with `FaultActive` after a `major` fault.
- [`INTAKE_LEDGER.md`](INTAKE_LEDGER.md) lists every source behind the transfer, with reading depth and an explicit unverified list.

## Layout

- `src/interlock/`: `leases`, `estop`, `faults`, `claims`, `interfaces`, `ledger`. Zero runtime dependencies.
- `docs/concepts/`: concept documentation.
- `docs/guides/quickstart.md`: first lease.
- `CHARTER.md`: design rationale and safety caveats.

## Status

v0.1.0 ([release](https://github.com/FetchDoge/interlock/releases/tag/v0.1.0), 2026-09-27): core substrate with tests. See [`CHANGELOG.md`](CHANGELOG.md).

Built by Marcus Richards, directing AI coding agents.

## License

Copyright 2026 Marcus Richards.
Licensed under the Apache License 2.0. See LICENSE.
