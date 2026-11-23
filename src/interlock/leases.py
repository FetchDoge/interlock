"""Time-bounded authority grants with keepalive renewal.

A lease is the only form authority takes in Interlock: a named holder, a
named resource, and a time-to-live. The grant is the single moment authority
comes into existence; without keepalive renewal the lease expires, and an
unexercised grant simply decays. The clock is injectable so tests can drive
time deterministically.

The manager keeps its own record of every lease it issued. That record, not
the caller's ``Lease`` object, is the source of truth for expiry and
revocation: a hand-built ``Lease``, a lease from another manager, or a lease
whose fields were edited after issue is not recognised as issued.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from interlock._errors import LeaseExpired, LeaseNotIssued, LeaseRevoked


@dataclass
class Lease:
    """A grant of authority over a named resource, valid until ``expires_at``."""

    id: str
    resource: str
    holder: str
    ttl_seconds: float
    granted_at: float
    expires_at: float
    last_keepalive: float
    revoked: bool = False


@dataclass
class _Issued:
    """The manager's authoritative record of one issued lease."""

    resource: str
    holder: str
    ttl_seconds: float
    granted_at: float
    expires_at: float
    revoked: bool = False


class LeaseManager:
    """Issues, renews, revokes, and checks leases against an injectable clock."""

    def __init__(self, clock: Callable[[], float] | None = None, gate=None) -> None:
        self._clock = clock if clock is not None else time.monotonic
        self._issued: dict[str, _Issued] = {}
        self._gate = gate

    def now(self) -> float:
        """Current time according to the manager's clock."""
        return self._clock()

    def grant(self, resource: str, holder: str, ttl_seconds: float) -> Lease:
        """Create a new lease. This is the only moment authority comes into existence."""
        now = self.now()
        lease = Lease(
            id=uuid4().hex,
            resource=resource,
            holder=holder,
            ttl_seconds=ttl_seconds,
            granted_at=now,
            expires_at=now + ttl_seconds,
            last_keepalive=now,
        )
        self._issued[lease.id] = _Issued(
            resource=resource,
            holder=holder,
            ttl_seconds=ttl_seconds,
            granted_at=now,
            expires_at=lease.expires_at,
        )
        return lease

    def _record(self, lease: Lease) -> _Issued | None:
        rec = self._issued.get(lease.id)
        if rec is None:
            return None
        if (rec.resource, rec.holder, rec.ttl_seconds, rec.granted_at) != (
            lease.resource,
            lease.holder,
            lease.ttl_seconds,
            lease.granted_at,
        ):
            return None  # same id, altered identity: not the lease that was issued
        return rec

    def issued(self, lease: Lease) -> bool:
        """True when this manager issued ``lease`` and its identity fields are unaltered."""
        return self._record(lease) is not None

    def is_revoked(self, lease: Lease) -> bool:
        """True when ``lease`` was revoked, by the manager's record or the object's flag."""
        rec = self._record(lease)
        return lease.revoked or (rec is not None and rec.revoked)

    def keepalive(self, lease: Lease) -> Lease:
        """Renew a lease: ``expires_at`` becomes now plus the original TTL.

        Raises :exc:`LeaseNotIssued` if this manager never issued the lease,
        :exc:`LeaseRevoked` if the lease was revoked, and :exc:`LeaseExpired`
        if the renewal arrives after expiry. A renewal cannot resurrect a dead
        lease. Expiry is judged against the manager's record, not the object.
        """
        rec = self._record(lease)
        if rec is None:
            raise LeaseNotIssued(f"lease {lease.id} for {lease.resource} was not issued here")
        if lease.revoked or rec.revoked:
            raise LeaseRevoked(f"lease {lease.id} for {lease.resource} was revoked")
        now = self.now()
        if now > rec.expires_at:
            raise LeaseExpired(f"lease {lease.id} for {lease.resource} expired at {rec.expires_at}")
        rec.expires_at = now + rec.ttl_seconds
        lease.last_keepalive = now
        lease.expires_at = rec.expires_at
        return lease

    def revoke(self, lease: Lease) -> None:
        """Revoke a lease immediately. Revocation is permanent.

        The manager records the revocation, so resetting ``lease.revoked`` on
        the object does not restore authority. With an in-flight gate (as
        wired by ``Interlock``), the revocation is ordered against commit-time
        checks and the call returns only after this lease's committed effects
        have finished.
        """
        if self._gate is None:
            self._mark_revoked(lease)
            return
        with self._gate.cond:
            self._mark_revoked(lease)
        self._gate.drain(lease.id)

    def _mark_revoked(self, lease: Lease) -> None:
        lease.revoked = True
        rec = self._record(lease)
        if rec is not None:
            rec.revoked = True

    def is_live(self, lease: Lease) -> bool:
        """True when this manager issued the lease and it is unrevoked and unexpired."""
        rec = self._record(lease)
        if rec is None:
            return False
        return not (lease.revoked or rec.revoked) and self.now() <= rec.expires_at
