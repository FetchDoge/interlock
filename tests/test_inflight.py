"""In-flight e-stop and revocation: the gauntlet `revoke-during-spend` case.

Gauntlet (revoke-during-spend): the gate is checked, the e-stop engages
100 ms later while the action is still preparing (300 ms), and the effect then
ran anyway, because `permit()` only checks at entry. These tests pin the
fail-closed contract of `Interlock.execute()`: the gate is re-proven at the
commit point, atomically with starting the effect, and an e-stop or revoke
returns only once no effect is mid-flight.
"""

import threading
import time

import pytest

from interlock import EStopEngaged, Interlock, LeaseRevoked


def _series(ilk, name):
    return list(ilk.ledger.read_series(name))


def _later(delay_s, fn):
    t = threading.Timer(delay_s, fn)
    t.start()
    return t


def test_estop_during_slow_preparation_refuses_effect():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    calls = []
    t = _later(0.1, lambda: ilk.estop.engage("gauntlet"))
    time.sleep(0.3)  # slow rail: authority was fine at the start
    with pytest.raises(EStopEngaged):
        ilk.execute(lease, "pay", "merchant", lambda: calls.append("paid"))
    t.join()
    assert calls == []
    denial = _series(ilk, "denials")[-1]
    assert denial["reason"] == "estop" and denial["phase"] == "commit"


def test_revoke_during_slow_preparation_refuses_effect():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    calls = []
    t = _later(0.1, lambda: ilk.leases.revoke(lease))
    time.sleep(0.3)
    with pytest.raises(LeaseRevoked):
        ilk.execute(lease, "pay", "merchant", lambda: calls.append("paid"))
    t.join()
    assert calls == []


def test_execute_runs_effect_and_returns_its_value_when_clear():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    assert ilk.execute(lease, "pay", "merchant", lambda: 42) == 42
    assert _series(ilk, "permits")[-1]["phase"] == "commit"


def test_estop_returns_only_after_running_effect_finishes():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    order = []
    started = threading.Event()

    def effect():
        started.set()
        time.sleep(0.2)
        order.append("effect_done")

    worker = threading.Thread(target=lambda: ilk.execute(lease, "pay", "m", effect))
    worker.start()
    started.wait(1)
    ilk.estop.engage("halt")
    order.append("estop_returned")
    worker.join()
    assert order == ["effect_done", "estop_returned"]


def test_race_no_effect_starts_after_estop_returns():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    lock = threading.Lock()
    starts = []
    stop_returned_at = []

    def effect():
        with lock:
            starts.append(time.monotonic())
        time.sleep(0.001)

    def spender():
        for _ in range(200):
            try:
                ilk.execute(lease, "pay", "m", effect)
            except EStopEngaged:
                return

    threads = [threading.Thread(target=spender) for _ in range(16)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    ilk.estop.engage("race")
    stop_returned_at.append(time.monotonic())
    for t in threads:
        t.join()
    assert starts, "race never exercised"
    assert all(s < stop_returned_at[0] for s in starts)


def test_permit_block_records_violation_when_authority_lost_inside():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    with ilk.permit(lease, "pay", "m"):
        ilk.estop.engage("mid-block")
    v = _series(ilk, "violations")[-1]
    assert v["reason"] == "estop" and v["lease_id"] == lease.id


def test_halt_from_inside_an_effect_does_not_deadlock():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    ilk.execute(lease, "pay", "m", lambda: ilk.estop.engage("effect saw fraud"))
    with pytest.raises(EStopEngaged):
        ilk.execute(lease, "pay", "m", lambda: None)
