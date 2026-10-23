"""Interlock: leased authority, a global e-stop, a fault bus, and provenance.

The single entry point is :class:`Interlock`, a facade over the substrate:

- ``.leases`` — a :class:`~interlock.leases.LeaseManager` issuing time-bounded grants.
- ``.estop`` — the one global :class:`~interlock.estop.EStop`.
- ``.faults`` — the shared :class:`~interlock.faults.FaultBus`.
- ``.ledger`` — the self-describing :class:`~interlock.ledger.Ledger`.

Every permit passes the gate in order: e-stop clear, lease issued by this
facade and live, no gating fault on the lease's resource.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from interlock._errors import (
    EStopEngaged,
    FaultActive,
    InterlockError,
    LeaseExpired,
    LeaseNotIssued,
    LeaseRevoked,
)
from interlock.claims import Claim
from interlock.estop import EStop, InFlightGate
from interlock.faults import FaultBus
from interlock.interfaces import REGISTRY, InterfaceRecord, declare
from interlock.leases import Lease, LeaseManager
from interlock.ledger import Ledger

T = TypeVar("T")

__version__ = "0.1.0"

__all__ = [
    "REGISTRY",
    "Claim",
    "EStop",
    "EStopEngaged",
    "FaultActive",
    "FaultBus",
    "InterfaceRecord",
    "Interlock",
    "InterlockError",
    "Lease",
    "LeaseExpired",
    "LeaseManager",
    "LeaseNotIssued",
    "LeaseRevoked",
    "Ledger",
    "__version__",
    "declare",
]


class Interlock:
    """Facade over the substrate. Owns the four components and the permit gate."""

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        self._gate = InFlightGate()
        self.leases = LeaseManager(clock, gate=self._gate)
        self.estop = EStop(self._gate)
        self.faults = FaultBus()
        self.ledger = ledger if ledger is not None else Ledger()

    def grant(
        self,
        resource: str,
        holder: str,
        ttl_seconds: float | None = None,
        ttl: float | None = None,
    ) -> Lease:
        """Grant a lease — the only moment authority comes into existence.

        ``ttl`` is an alias for ``ttl_seconds`` (the README spelling); exactly
        one must be given.
        """
        effective = ttl_seconds if ttl_seconds is not None else ttl
        if effective is None:
            raise TypeError("grant() missing required argument: 'ttl_seconds' (or 'ttl')")
        lease = self.leases.grant(resource, holder, effective)
        self.ledger.append(
            "grants",
            {
                "lease_id": lease.id,
                "holder": holder,
                "resource": resource,
                "ttl_seconds": effective,
            },
        )
        return lease

    def _check(self, lease: Lease, action: str, target: str, phase: str) -> dict:
        """Run the gate in order; log and raise on the first failure."""
        base = {
            "lease_id": lease.id,
            "holder": lease.holder,
            "resource": lease.resource,
            "action": action,
            "target": target,
            "phase": phase,
        }
        try:
            self.estop.assert_clear()
        except EStopEngaged:
            self.ledger.append("denials", {**base, "reason": "estop"})
            raise
        if not self.leases.issued(lease):
            self.ledger.append("denials", {**base, "reason": "lease_not_issued"})
            raise LeaseNotIssued(
                f"lease {lease.id} for {lease.resource} was not issued by this Interlock"
            )
        if self.leases.is_revoked(lease):
            self.ledger.append("denials", {**base, "reason": "lease_revoked"})
            raise LeaseRevoked(f"lease {lease.id} for {lease.resource} was revoked")
        if not self.leases.is_live(lease):
            self.ledger.append("denials", {**base, "reason": "lease_expired"})
            raise LeaseExpired(f"lease {lease.id} for {lease.resource} expired")
        if self.faults.gating(lease.resource):
            self.ledger.append("denials", {**base, "reason": "fault"})
            raise FaultActive(f"gating fault active on {lease.resource}")
        return base

    @contextmanager
    def permit(self, lease: Lease, action: str, target: str) -> Iterator[Lease]:
        """Permit exactly one action on one target, checking in order:

        1. e-stop clear → else :exc:`EStopEngaged`
        2. lease issued by this facade's manager and unaltered → else
           :exc:`LeaseNotIssued`; then live (unrevoked, unexpired) → else
           :exc:`LeaseRevoked` or :exc:`LeaseExpired`
        3. no gating fault on the lease's resource → else :exc:`FaultActive`

        Permits and denials are appended to the ledger.

        ``permit()`` checks at block entry only: it cannot stop an effect the
        caller performs later inside the block. For irreversible effects use
        :meth:`execute`, which re-proves the gate at the commit point. If the
        e-stop engages or the lease is revoked while the block runs, a
        ``violations`` record is appended on exit so the gap is never silent.
        """
        base = self._check(lease, action, target, phase="entry")
        self.ledger.append("permits", base)
        try:
            yield lease
        finally:
            reason = None
            if self.estop.is_engaged:
                reason = "estop"
            elif self.leases.is_revoked(lease):
                reason = "lease_revoked"
            if reason:
                self.ledger.append("violations", {**base, "reason": reason})

    def execute(self, lease: Lease, action: str, target: str, effect: Callable[[], T]) -> T:
        """Run ``effect`` only if the gate is clear at the commit point.

        Fail-closed: the gate (e-stop, lease issued/live/unrevoked, faults) is
        checked and the effect registered as in flight atomically, under the
        same lock :meth:`EStop.engage` and :meth:`LeaseManager.revoke` take.
        Either the halt lands first and the effect never starts, or the effect
        started first and the halt call waits for it to finish. Any error in
        the check refuses the effect.
        """
        with self._gate.cond:
            base = self._check(lease, action, target, phase="commit")
            self._gate.enter(lease.id)
        try:
            self.ledger.append("permits", base)
            return effect()
        finally:
            self._gate.leave(lease.id)

    def claim(
        self,
        text: str,
        provenance: str,
        authority: str,
        review_after_days: float,
        falsifier: str,
        status: str = "inferred",
    ) -> Claim:
        """Stamp a claim and auto-append it to the ledger's ``claims`` series."""
        claim = Claim(
            text=text,
            provenance=provenance,
            authority=authority,
            review_after_days=review_after_days,
            falsifier=falsifier,
            status=status,
        )
        self.ledger.append("claims", claim.record())
        return claim
