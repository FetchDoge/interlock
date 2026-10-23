"""The one halt: a global emergency stop.

A single e-stop engagement suspends all permits, everywhere, immediately.
Resuming requires explicit, deliberate release — never an automatic timeout.
"""

import threading

from interlock._errors import EStopEngaged


class EStop:
    """A latching global halt. Engagement is sticky; release is deliberate."""

    def __init__(self, gate: "InFlightGate | None" = None) -> None:
        self._engaged = False
        self._reason = ""
        self._engagements = 0
        self._gate = gate if gate is not None else InFlightGate()

    def engage(self, reason: str) -> None:
        """Engage the e-stop. All permits are refused until :meth:`release`.

        The flag is set under the in-flight gate, so no commit-time check can
        interleave with it, and the call returns only once every effect that
        was already committed (via ``Interlock.execute``) has finished. When
        ``engage`` returns, nothing is mid-effect and nothing new can start.
        Every engagement is counted (see :attr:`engagements`), including one
        that is released again before anyone looks.
        """
        with self._gate.cond:
            self._engaged = True
            self._reason = reason
            self._engagements += 1
        self._gate.drain()

    def release(self) -> None:
        """Release the e-stop. An operator decision, never a timeout."""
        self._engaged = False
        self._reason = ""

    @property
    def is_engaged(self) -> bool:
        """Whether the e-stop is currently engaged."""
        return self._engaged

    @property
    def engagements(self) -> int:
        """How many times the e-stop has ever been engaged.

        Lets a caller detect an engagement that was released again before
        it looked — a final-state check on :attr:`is_engaged` cannot.
        """
        return self._engagements

    @property
    def reason(self) -> str:
        """The reason given at the last engagement, or ``""`` when clear."""
        return self._reason

    def assert_clear(self) -> None:
        """Raise :exc:`EStopEngaged` unless the e-stop is clear."""
        if self._engaged:
            raise EStopEngaged(f"e-stop engaged: {self._reason}")


class InFlightGate:
    """Shared condition between the commit-time check and the halt paths.

    ``Interlock.execute`` checks authority and registers its effect as
    in flight under ``cond``; ``EStop.engage`` and ``LeaseManager.revoke``
    change state under ``cond`` and then wait for in-flight effects to drain.
    Effects run concurrently; only check-and-register is serialised. A halt
    raised from inside an effect does not wait on its own thread's effect,
    and concurrent halts raised from inside effects do not wait on each
    other either: a draining thread's effect is suspended inside its halt
    call, so two such halts waiting on each other could never return.
    """

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self._in_flight: dict[tuple[str, int], int] = {}
        self._halting: dict[int, int] = {}

    def enter(self, lease_id: str) -> None:
        """Register one effect for ``lease_id``. Call with ``cond`` held."""
        key = (lease_id, threading.get_ident())
        self._in_flight[key] = self._in_flight.get(key, 0) + 1

    def leave(self, lease_id: str) -> None:
        key = (lease_id, threading.get_ident())
        with self.cond:
            n = self._in_flight.get(key, 0) - 1
            if n <= 0:
                self._in_flight.pop(key, None)
            else:
                self._in_flight[key] = n
            self.cond.notify_all()

    def _busy(self, lease_id: str | None) -> bool:
        me = threading.get_ident()
        return any(
            tid != me and tid not in self._halting and (lease_id is None or lid == lease_id)
            for lid, tid in self._in_flight
        )

    def drain(self, lease_id: str | None = None) -> None:
        """Block until no other thread has an effect (for ``lease_id``) in flight.

        Effects on threads that are themselves inside a halt do not block
        the drain (see the class docstring); effects still running normally
        are waited on, and a halted thread's effect is waited on again as
        soon as its halt returns and the effect resumes.
        """
        me = threading.get_ident()
        with self.cond:
            self._halting[me] = self._halting.get(me, 0) + 1
            try:
                self.cond.wait_for(lambda: not self._busy(lease_id))
            finally:
                n = self._halting[me] - 1
                if n <= 0:
                    del self._halting[me]
                else:
                    self._halting[me] = n
                self.cond.notify_all()
