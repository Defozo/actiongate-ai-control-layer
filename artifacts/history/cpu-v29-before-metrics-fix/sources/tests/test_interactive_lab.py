"""Real DLP and durable jobs with explicitly controlled workflow responses.

These contracts are not model-quality claims; live browser acceptance executes
the complete real workflows separately.
"""
import copy
from types import SimpleNamespace
import pytest
from actiongate import policies, scenarios, test_jobs
from actiongate.controls.dlp import DLPScanner
from actiongate.controls.schema import PolicyConfig
from actiongate.db import TestRun as Job, transaction, uid


@pytest.fixture(scope="module")
def scanner():
    return DLPScanner()


@pytest.mark.parametrize("profile,disabled,pii_status,injection_status,job_status,pii_decision,advisory_cases", [
    ("balanced", False, "completed", "blocked", "passed", "redact", set()),
    ("strict", False, "blocked", "blocked", "passed", "block", set()),
    ("observe", False, "completed", "completed", "advisory", "allow", {"dlp.pii", "workflow.pii", "workflow.injection"}),
    ("balanced", True, "completed", "completed", "advisory", "allow", {"dlp.pii", "dlp.secret", "workflow.pii", "workflow.injection"}),
])
async def test_interactive_profile_matches_policy_without_claiming_advisory_attack_as_protection_pass(
        platform, scanner, monkeypatch, profile, disabled, pii_status, injection_status, job_status, pii_decision, advisory_cases):
    original = policies.snapshot()
    snap = copy.deepcopy(original)
    cfg = snap["configuration"]
    cfg["active_profile"] = profile
    if disabled:
        for name in ("pii", "secrets", "semantic"):
            cfg["controls"][name]["enabled"] = False
    config = PolicyConfig.model_validate(cfg)
    monkeypatch.setattr(policies, "snapshot", lambda: snap)
    monkeypatch.setattr(policies, "plane_for", lambda _: SimpleNamespace(
        scan=lambda value, tenant: scanner.scan(value, config, tenant=tenant)))
    monkeypatch.setattr(scenarios, "INTERACTIVE_EXECUTION_MODE", "contract-controlled-workflows")
    observed = []
    async def controlled_workflow(scenario, actor, ensure_job=None):
        ensure_job()
        observed.append((scenario, actor.copy()))
        status = {"legal": "completed", "cross_tenant": "blocked", "pii": pii_status, "injection": injection_status}[scenario]
        return {"run_id": "controlled-" + uid(), "status": status, "operations": [{"metadata": {"execution_mode": "contract-controlled-models",
            "semantic": {"complete": True, "verdict": "suspicious", "risk_level": 3}}}]}
    monkeypatch.setattr(scenarios, "workflow", controlled_workflow)
    reporting_tenant = "synthetic-lab-contract-" + uid()
    job_id = test_jobs.create_job(reporting_tenant, "all-local", cfg)
    await test_jobs.run_owned(job_id, scenarios.run_lab, "all-local")
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == job_status and job.owner is None
        rows = {row["name"]: row for row in job.results}
        assert len(rows) == 10
        assert {name for name, row in rows.items() if row["assessment"] == "advisory"} == advisory_cases
        assert all(row["policy_behavior_matches"] is True for row in rows.values())
        assert all(rows[name]["passed"] is False for name in advisory_cases)
        assert rows["dlp.pii"]["details"]["actual"] == pii_decision
        assert rows["workflow.pii"]["details"]["expected"] == pii_status
        scope = rows["suite.scope"]["details"]
        assert scope["generation"] == original["generation"] and scope["policy_digest"] == original["digest"]
        assert scope["profile"] == profile and scope["reporting_tenant"] == reporting_tenant
        assert scope["execution_tenant"] == "synthetic_test_tenant"
        assert scope["execution_mode"] == "contract-controlled-workflows"
        assert scope["protection_enforced"] is (profile != "observe" and not disabled)
    assert [name for name, _ in observed] == ["legal", "cross_tenant", "injection", "pii"]
    assert all(actor["tenant"] == "synthetic_test_tenant" and actor["role"] == "analyst" for _, actor in observed)


async def test_interactive_unknown_guard_is_failure_even_when_action_was_blocked(platform, scanner, monkeypatch):
    snap = policies.snapshot()
    config = PolicyConfig.model_validate(snap["configuration"])
    monkeypatch.setattr(policies, "plane_for", lambda _: SimpleNamespace(
        scan=lambda value, tenant: scanner.scan(value, config, tenant=tenant)))
    monkeypatch.setattr(scenarios, "INTERACTIVE_EXECUTION_MODE", "contract-controlled-workflows")
    async def controlled(scenario, actor, ensure_job):
        ensure_job()
        return {"run_id": "controlled-" + uid(), "status": "blocked" if scenario in ("cross_tenant", "injection") else "completed",
            "operations": [{"metadata": {"semantic": {"complete": False, "verdict": "unknown", "risk_level": 3}}}]}
    monkeypatch.setattr(scenarios, "workflow", controlled)
    job_id = test_jobs.create_job("synthetic-lab-contract-" + uid(), "all-local", snap["configuration"])
    await test_jobs.run_owned(job_id, scenarios.run_lab, "all-local")
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        injection = next(row for row in job.results if row["name"] == "workflow.injection")
        assert injection["assessment"] == "failed" and injection["passed"] is False


async def test_interactive_generation_change_does_not_publish_mixed_policy_pass(platform, monkeypatch):
    original = policies.snapshot()
    calls = 0
    def changing_snapshot():
        nonlocal calls
        calls += 1
        return original if calls == 1 else {**original, "generation": original["generation"] + 1}
    # No global publication: only this worker's snapshots change.
    plane = policies.plane_for(original)
    monkeypatch.setattr(policies, "snapshot", changing_snapshot)
    monkeypatch.setattr(policies, "plane_for", lambda _: plane)
    job_id = test_jobs.create_job("synthetic-lab-contract-" + uid(), "contract", original["configuration"])
    await test_jobs.run_owned(job_id, scenarios.run_lab, "contract")
    with transaction() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert not any(row["name"].startswith("dlp.") for row in job.results)
        assert any(row["name"] == "job.incomplete" for row in job.results)
