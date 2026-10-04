"""Synthetic comparison uses real OPA and test-budget ledger, never connectors."""
import copy
import pytest
from sqlalchemy import select, func
from actiongate import policies, broker
from actiongate.db import ConnectorReceipt, TestRun as StoredJob, Reservation, transaction
from actiongate.replay import candidate_decision, compare_synthetic


async def test_candidate_query_changes_profile_without_publishing_or_executing(platform):
    before = policies.snapshot()
    candidate = copy.deepcopy(before["configuration"])
    candidate["active_profile"] = "strict"
    plane = policies.plane_for(before)
    scan = plane.scan("Supplier deliveries are stable.", tenant="synthetic_test_tenant")
    verdict = {"verdict": "suspicious", "risk_level": 1, "complete": True}
    balanced = await candidate_decision(before["configuration"], before["generation"], scan, verdict)
    strict = await candidate_decision(candidate, before["generation"], scan, verdict)
    assert balanced["decision"] == "require_approval"
    assert strict["decision"] == "block"
    assert policies.snapshot()["generation"] == before["generation"]


async def test_synthetic_comparison_has_separate_budget_and_zero_business_effects(actor, controlled_models, monkeypatch):
    from actiongate.test_jobs import create_job, run_owned
    test_id = create_job(actor["tenant"], "synthetic-policy-replay", policies.snapshot()["configuration"])
    with transaction() as db:
        before = db.scalar(select(func.count()).select_from(ConnectorReceipt))
    async def forbidden(*args, **kwargs):
        pytest.fail("Synthetic policy comparison invoked a business connector")
    monkeypatch.setattr(broker, "dispatch", forbidden)
    snap = policies.snapshot()
    await run_owned(test_id, compare_synthetic, snap["yaml"], actor)
    with transaction() as db:
        test = db.get(StoredJob, test_id)
        assert test.status == "passed", test.results
        assert len(test.results) == 16
        assert all(row["effects_executed"] == 0 and row["budget_tenant"] == "synthetic_test_tenant" for row in test.results)
        assert db.scalar(select(func.count()).select_from(ConnectorReceipt)) == before
        run_ids = {row["budget_run_id"] for row in test.results}
        assert len(run_ids) == 16
    assert controlled_models["business"] == 0
    assert controlled_models["guard"] == 16

