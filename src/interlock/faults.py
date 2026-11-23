"""A shared fault bus: failures as structured, severity-graded records.

Tool failures, timeouts, and contradictions are reported as faults with a
severity of minor, major, or critical. An uncleared fault of major or
critical severity *gates* its resource: new permits on that resource are
refused until the fault is cleared.

When the bus is wired to an in-flight gate (as :class:`~interlock.Interlock`
does), reports and clears take the gate's lock, so a gating change is
ordered against the commit-time check-and-register in
:meth:`Interlock.execute`: a report that returns before an effect commits
is always seen by that effect's check, and one that lands after the check
waits until registration has happened — it can never slip between them.
"""

import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from interlock.estop import InFlightGate

SEVERITIES = ("minor", "major", "critical")
_GATING_SEVERITIES = ("major", "critical")


@dataclass
class Fault:
    """A structured failure report. ``cleared`` flips only via :meth:`FaultBus.clear`."""

    id: int
    code: str
    severity: str
    resource: str
    detail: str = ""
    ts: float = field(default_factory=time.time)
    cleared: bool = False


class FaultBus:
    """Collects faults and answers whether a resource is gated."""

    def __init__(self, gate: "InFlightGate | None" = None) -> None:
        self._faults: list[Fault] = []
        self._ids = count(1)
        self._gate = gate

    def _ordered(self):
        """The gate's lock when wired, so state changes order against commits."""
        return self._gate.cond if self._gate is not None else nullcontext()

    def report(self, code: str, severity: str, resource: str, detail: str = "") -> Fault:
        """Report a fault. Severity must be minor, major, or critical."""
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")
        with self._ordered():
            fault = Fault(
                id=next(self._ids),
                code=code,
                severity=severity,
                resource=resource,
                detail=detail,
            )
            self._faults.append(fault)
            return fault

    def clear(self, fault_id: int) -> Fault:
        """Mark a fault cleared. Clearing is recorded, never erased."""
        with self._ordered():
            for fault in self._faults:
                if fault.id == fault_id:
                    fault.cleared = True
                    return fault
        raise KeyError(f"no fault with id {fault_id}")

    def active(self, resource: str | None = None) -> list[Fault]:
        """Uncleared faults, optionally restricted to one resource."""
        return [
            fault
            for fault in self._faults
            if not fault.cleared and (resource is None or fault.resource == resource)
        ]

    def gating(self, resource: str) -> bool:
        """True when an uncleared major or critical fault exists on the resource."""
        return any(fault.severity in _GATING_SEVERITIES for fault in self.active(resource))
