"""In-flight e-stop and revocation: the gauntlet `revoke-during-spend` case.

Gauntlet (revoke-during-spend): the gate is checked, the e-stop engages
100 ms later while the action is still preparing (300 ms), and the effect then
ran anyway, because `permit()` only checks at entry. These tests pin the
fail-closed contract of `Interlock.execute()`: the gate is re-proven at the
commit point, atomically with starting the effect, and an e-stop or revoke
returns only once no effect is mid-flight.
"""

import json
import threading
import time

import pytest

from interlock import EStopEngaged, FaultActive, Interlock, LeaseRevoked, Ledger


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
    halted = threading.Event()

    def engage():
        ilk.estop.engage("gauntlet")
        halted.set()

    t = _later(0.1, engage)
    time.sleep(0.3)  # slow rail: authority was fine at the start
    # Synchronize on the halt having landed, not on elapsed wall time:
    # a Timer has no scheduling deadline on a loaded worker.
    assert halted.wait(5), "the scheduled e-stop never landed"
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
    halted = threading.Event()

    def revoke():
        ilk.leases.revoke(lease)
        halted.set()

    t = _later(0.1, revoke)
    time.sleep(0.3)
    assert halted.wait(5), "the scheduled revocation never landed"
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


# --- Codex review regressions (PR #18) -------------------------------------


def test_concurrent_halts_from_inside_effects_do_not_deadlock():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    barrier = threading.Barrier(2)
    returned = []

    def halting(name):
        def effect():
            barrier.wait(5)
            ilk.estop.engage(f"halt from {name}")
            returned.append(name)

        return effect

    threads = [
        threading.Thread(
            target=lambda name=name: ilk.execute(lease, "pay", "m", halting(name)),
            daemon=True,
        )
        for name in ("a", "b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads), "concurrent in-effect halts deadlocked"
    assert sorted(returned) == ["a", "b"]
    assert ilk.estop.is_engaged


def test_concurrent_in_effect_revocations_do_not_deadlock():
    ilk = Interlock()
    lease_a = ilk.grant("payments", "agent-a", ttl_seconds=60)
    lease_b = ilk.grant("payments", "agent-b", ttl_seconds=60)
    barrier = threading.Barrier(2)
    done = []

    def revoking(own, other, name):
        def effect():
            barrier.wait(5)
            ilk.leases.revoke(other)
            done.append(name)

        return effect

    threads = [
        threading.Thread(
            target=lambda: ilk.execute(lease_a, "pay", "m", revoking(lease_a, lease_b, "a")),
            daemon=True,
        ),
        threading.Thread(
            target=lambda: ilk.execute(lease_b, "pay", "m", revoking(lease_b, lease_a, "b")),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads), "mutual in-effect revocations deadlocked"
    assert sorted(done) == ["a", "b"]
    assert ilk.leases.is_revoked(lease_a) and ilk.leases.is_revoked(lease_b)


def test_transient_estop_inside_permit_block_records_violation():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    with ilk.permit(lease, "pay", "m"):
        ilk.estop.engage("mid-block")
        ilk.estop.release()
    v = _series(ilk, "violations")[-1]
    assert v["reason"] == "estop" and v["lease_id"] == lease.id


def test_execute_refuses_deferred_effects():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    calls = []

    async def async_effect():
        calls.append("async")

    def gen_effect():
        yield 1
        calls.append("gen")

    with pytest.raises(TypeError):
        ilk.execute(lease, "pay", "m", async_effect)
    with pytest.raises(TypeError):
        ilk.execute(lease, "pay", "m", gen_effect)
    with pytest.raises(TypeError):
        ilk.execute(lease, "pay", "m", lambda: async_effect())
    with pytest.raises(TypeError):
        ilk.execute(lease, "pay", "m", lambda: gen_effect())
    assert calls == []
    # The refusals leave no stale in-flight entry behind.
    assert ilk.execute(lease, "pay", "m", lambda: 42) == 42


def test_fault_report_is_ordered_against_commit_registration():
    ilk = Interlock()
    lease = ilk.grant("payments", "agent", ttl_seconds=60)
    reported = threading.Event()

    def report():
        ilk.faults.report("tool_failure", "critical", "payments")
        reported.set()

    with ilk._gate.cond:
        t = threading.Thread(target=report)
        t.start()
        assert not reported.wait(0.3), "fault report interleaved with the commit gate"
    t.join(5)
    assert reported.is_set()
    with pytest.raises(FaultActive):
        ilk.execute(lease, "pay", "m", lambda: None)


def test_concurrent_execute_ledger_index_matches_records(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(str(path))
    ilk = Interlock(ledger=ledger)
    lease = ilk.grant("payments", "agent", ttl_seconds=600)
    n_threads, per_thread = 16, 25
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait(10)
        for _ in range(per_thread):
            ilk.execute(lease, "pay", "m", lambda: None)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ledger.close()
    assert len(list(ledger.read_series("permits"))) == n_threads * per_thread
    objs = [json.loads(line) for line in path.read_text().splitlines()]
    descriptors = [
        obj for obj in objs if obj.get("_type") == "series" and obj.get("series") == "permits"
    ]
    assert len(descriptors) == 1
    index = [obj for obj in objs if obj.get("_type") == "index"][-1]
    assert index["series"]["permits"]["count"] == n_threads * per_thread
