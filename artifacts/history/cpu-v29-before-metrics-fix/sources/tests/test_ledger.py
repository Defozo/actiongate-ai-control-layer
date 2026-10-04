from concurrent.futures import ThreadPoolExecutor
import copy
import pytest
from hypothesis import given, settings, strategies as st
from sqlalchemy import select
from actiongate import ledger, policies
from actiongate.db import BudgetAccount, Reservation, State, transaction, uid


@pytest.fixture
def workflow_run(platform):
    from actiongate.broker import create_run
    from actiongate.contracts import RunRequest
    from actiongate.db import Principal
    # Small-limit tests own a namespace, so prior model usage is never erased
    # or mistaken for an admission race when the suite is run repeatedly.
    actor = {"sub": "ledger:"+uid(), "tenant": "synthetic-ledger-"+uid(), "role": "analyst"}
    with transaction() as db:
        db.add(Principal(id=actor["sub"], tenant=actor["tenant"], role=actor["role"]))
    return create_run(RunRequest(document_ids=[]), actor)


def root_account(root_id, unit="tokens"):
    with transaction() as db:
        x = db.get(BudgetAccount, f"root:{root_id}:{unit}")
        return x.spent, x.reserved, x.limit


@pytest.mark.integration
def test_exact_boundary_and_unknown_are_not_free(workflow_run):
    cfg = copy.deepcopy(policies.snapshot()["configuration"])
    cfg["budgets"]["run_total_tokens"] = 100
    rid = ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=100)
    with pytest.raises(ledger.BudgetDenied):
        ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=1)
    assert ledger.settle(rid, None) == "usage_unknown"
    assert root_account(workflow_run["id"]) == (0, 100, 100)
    ledger.settle(rid, {"total_tokens": 70})
    ledger.settle(rid, {"total_tokens": 70})
    assert root_account(workflow_run["id"]) == (70, 0, 100)


@pytest.mark.integration
def test_fifty_parallel_reservations_do_not_overspend(workflow_run):
    cfg = copy.deepcopy(policies.snapshot()["configuration"])
    cfg["budgets"]["run_total_tokens"] = 100
    def attempt(_):
        try:
            return ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=10)
        except ledger.BudgetDenied:
            return None
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(50)))
    accepted = [r for r in results if r]
    assert len(accepted) == 10
    assert root_account(workflow_run["id"]) == (0, 100, 100)
    for reservation in accepted:
        ledger.settle(reservation, confirmed_not_started=True)
    assert root_account(workflow_run["id"]) == (0, 0, 100)


@pytest.mark.integration
def test_guard_spend_is_also_parent_spend(workflow_run):
    cfg = policies.snapshot()["configuration"]
    rid = ledger.reserve(workflow_run["id"], uid(), "guard.input", cfg, tokens=500)
    ledger.settle(rid, {"total_tokens": 250})
    with transaction() as db:
        root = db.get(BudgetAccount, f"root:{workflow_run['id']}:tokens")
        guard = db.get(BudgetAccount, f"guard:{workflow_run['id']}:tokens")
        assert root.spent == guard.spent == 250


@pytest.mark.integration
def test_actual_usage_overrun_is_preserved_and_kills_new_work(workflow_run):
    cfg = policies.snapshot()["configuration"]
    rid = ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=10)
    try:
        ledger.settle(rid, {"total_tokens": 11})
        assert root_account(workflow_run["id"])[0] == 11
        with pytest.raises(ledger.BudgetDenied):
            ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=1)
    finally:
        with transaction() as db:
            db.get(State, 1).kill_switch = False


@pytest.mark.integration
def test_reduction_does_not_erase_existing_obligations(workflow_run):
    cfg = copy.deepcopy(policies.snapshot()["configuration"])
    cfg["budgets"]["run_total_tokens"] = 100
    rid = ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=90)
    cfg["budgets"]["run_total_tokens"] = 50
    with pytest.raises(ledger.BudgetDenied):
        ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=1)
    assert root_account(workflow_run["id"])[1] == 90
    ledger.settle(rid, {"total_tokens": 80})
    assert root_account(workflow_run["id"])[0] == 80


@pytest.mark.integration
@settings(max_examples=12, deadline=None)
@given(st.lists(st.tuples(st.integers(1, 40), st.booleans()), min_size=1, max_size=8))
def test_reserve_settle_release_sequences(platform, sequence):
    from actiongate.broker import create_run
    from actiongate.contracts import RunRequest
    from actiongate.db import Principal
    actor = {"sub": "property:"+uid(), "tenant": "synthetic-property-"+uid(), "role": "analyst"}
    with transaction() as db:
        db.add(Principal(id=actor["sub"], tenant=actor["tenant"], role=actor["role"]))
    run = create_run(RunRequest(document_ids=[]), actor)
    cfg = copy.deepcopy(policies.snapshot()["configuration"])
    cfg["budgets"]["run_total_tokens"] = 100
    expected = 0
    for amount, executed in sequence:
        if expected + amount > 100:
            with pytest.raises(ledger.BudgetDenied):
                ledger.reserve(run["id"], uid(), "execution", cfg, tokens=amount)
            continue
        rid = ledger.reserve(run["id"], uid(), "execution", cfg, tokens=amount)
        ledger.settle(rid, {"total_tokens": amount} if executed else None, confirmed_not_started=not executed)
        if executed:
            expected += amount
        assert root_account(run["id"]) == (expected, 0, 100)


def test_delegated_work_is_billed_to_original_application_user(workflow_run):
    from actiongate.broker import create_run
    from actiongate.contracts import RunRequest
    from actiongate.db import Run
    with transaction() as db:
        root = db.get(Run, workflow_run["id"])
    actor = {"sub": "workload:"+root.id, "role": "agent", "tenant": root.tenant, "root_run_id": root.id}
    child = create_run(RunRequest(parent_id=root.id, document_ids=[]), actor)
    reservation = ledger.reserve(child["id"], uid(), "execution", policies.snapshot()["configuration"], tokens=100)
    with transaction() as db:
        accounts = db.get(Reservation, reservation).accounts
    assert any(key.startswith(f"user:{root.tenant}:{root.actor}:") for key in accounts)
    assert not any(key.startswith(f"user:{root.tenant}:{actor['sub']}:") for key in accounts)
    ledger.settle(reservation, {"total_tokens": 40})
    assert root_account(root.id)[0] == 40


def test_rollover_preserves_and_settles_previous_period_obligation(workflow_run, monkeypatch):
    cfg = policies.snapshot()["configuration"]
    original_accounts = ledger._accounts
    def yesterday(db, run, config, kind, amounts):
        return [(key.replace(period, "2026-01-01") if scope in ("tenant", "user") else key,
                 unit, amount, limit, scope, "2026-01-01")
                for key, unit, amount, limit, scope, period in original_accounts(db, run, config, kind, amounts)]
    monkeypatch.setattr(ledger, "_accounts", yesterday)
    previous = ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=100)
    ledger.settle(previous, None)
    monkeypatch.setattr(ledger, "_accounts", original_accounts)
    today = ledger.reserve(workflow_run["id"], uid(), "execution", cfg, tokens=100)
    with transaction() as db:
        old_ids = db.get(Reservation, previous).accounts
        new_ids = db.get(Reservation, today).accounts
        assert any(key not in new_ids for key in old_ids if key.startswith("tenant:"))
    ledger.settle(previous, {"total_tokens": 70})
    with transaction() as db:
        old_account = db.get(BudgetAccount, next(key for key in old_ids if key.startswith("tenant:") and key.endswith(":tokens")))
        new_account = db.get(BudgetAccount, next(key for key in new_ids if key.startswith("tenant:") and key.endswith(":tokens")))
        assert (old_account.spent, old_account.reserved) == (70, 0)
        assert (new_account.spent, new_account.reserved) == (0, 100)
    ledger.settle(today, confirmed_not_started=True)
