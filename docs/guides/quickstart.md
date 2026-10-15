# Quickstart: your first lease

This is the shortest path from zero to a live lease, a refused permit, and an
auditable ledger. The examples exercise Interlock's admission gate; they do
not sandbox code outside the gate or stop an action that is already running.

## 0. Install in a supported environment

Interlock requires Python 3.11 or newer, and its build backend requires
`setuptools>=68`. From a scratch directory, clone the repository, create a
virtual environment with a supported interpreter, and install the checked-out
package:

```bash
git clone https://github.com/FetchDoge/interlock.git
cd interlock
python3 -m venv .venv   # python3 must be 3.11+; use python3.11 etc. if needed
. .venv/bin/activate
python -m pip install --upgrade "setuptools>=68"
python -m pip install .
```

Every Python block below runs in order, as one session, with `python` from
this activated environment. This guide needs no pytest install.

## 1. Create the interlock

```python
from pathlib import Path

from interlock import EStopEngaged, FaultActive, Interlock

ilk = Interlock()
```

You now hold four components: `.leases` (the lease manager), `.estop`
(the one halt), `.faults` (the fault bus), and `.ledger` (the run log).

## 2. Grant a lease

```python
lease = ilk.grant(resource="docs:write", holder="demo-agent", ttl_seconds=300)
```

Authority now exists, and this example lease is short-lived. Nothing else in
the system can pass the gate for `docs:write` without this lease. Admission
checks the e-stop, lease state, and resource faults at the time of entry.

## 3. Exercise it through the gate

```python
with ilk.permit(lease, action="write", target="notes.md"):
    Path("notes.md").write_text("hello")
```

The gate checks, in order: e-stop clear, lease issued by this `Interlock`
and live, and no gating fault on `docs:write`. If any check fails, the
context manager raises instead of yielding: `EStopEngaged`,
`LeaseNotIssued`, `LeaseExpired`/`LeaseRevoked`, or `FaultActive`. The check
controls admission to the context. It is not an OS sandbox, and it cannot
interrupt an action that has already started.

## 4. Watch a fault refuse

```python
fault = ilk.faults.report(
    code="DISK_FULL", severity="major", resource="docs:write"
)

try:
    with ilk.permit(lease, action="write", target="notes.md"):
        Path("notes.md").write_text("should not be admitted")
except FaultActive:
    pass
else:
    raise AssertionError("the active fault must refuse admission")

assert fault.id is not None
ilk.faults.clear(fault.id)
with ilk.permit(lease, action="write", target="notes.md"):
    Path("notes.md").write_text("resumed")
```

The refused entry is appended to the ledger. Clearing the fault permits a
new admission; it does not retroactively authorize the refused action.

## 5. Halt everything, then release

```python
ilk.estop.engage("taking a look")
try:
    with ilk.permit(lease, action="write", target="notes.md"):
        raise AssertionError("e-stop must refuse admission")
except EStopEngaged:
    pass
ilk.estop.release()  # deliberate, never automatic
```

## 6. Stamp a claim and read the ledger

```python
claim = ilk.claim(
    text="notes.md written",
    provenance="demo",
    authority=lease.id,
    review_after_days=7,
    falsifier="notes.md missing or different",
)
print(claim.check())  # ('inferred', False)

for record in ilk.ledger.read_series("claims"):
    print(record["text"], record["_ts"])

ilk.ledger.close()
```

## Next steps

- `docs/concepts/leases.md`: why authority must decay.
- `docs/concepts/estop-and-faults.md`: the one halt and the gating bus.
- `docs/concepts/claims.md`: provenance, review dates, falsifiers.
- `docs/concepts/ledger.md`: the self-describing run log.
- `CHARTER.md`: the full design rationale and safety caveats.
