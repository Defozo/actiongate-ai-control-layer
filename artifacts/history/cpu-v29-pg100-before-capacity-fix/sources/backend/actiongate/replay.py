"""Synthetic policy comparison without execution grants or business connectors."""
import asyncio
import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from . import broker, policies
from .contracts import RunRequest, YamlRequest
from .db import Principal, Run, TestRun, transaction, uid
from .security import identity, require_role, digest
from .settings import settings, ROOT
from .test_jobs import create_job, run_owned, persist_results, JobLost

router = APIRouter()


async def candidate_decision(config, generation, scan, verdict):
    """OPA query-local data override. No shared policy publication or mutation."""
    payload = {"tenant": "synthetic_test_tenant", "tool": "reports.save", "generation": generation,
        "deterministic": {"decision": scan.decision, "rule_ids": scan.rule_ids},
        "semantic": {key: verdict.get(key) for key in ("verdict", "risk_level", "complete")},
        "identity_ok": True, "tenant_ok": True, "grant_ok": True, "labels_ok": True,
        "budget_ok": True, "registry_ok": True, "approval_valid": False, "kill_switch": False}
    query = f"result = data.actiongate.g{int(generation)}.decision with input as " + json.dumps(payload) + " with data.actiongate_snapshots as " + json.dumps({str(generation): {"config": config}})
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.post(settings()["opa_url"] + "/v1/query", json={"query": query})
        response.raise_for_status()
        result = response.json()["result"][0]["result"]
    if result.get("generation") != generation or result.get("decision") not in ("allow", "redact", "block", "require_approval"):
        raise ValueError("Candidate policy returned an invalid result")
    return result


async def compare_synthetic(test_id, source, actor):
    records = []
    def persist(status="running"):
        persist_results(test_id, records, status)
    try:
        snap = policies.snapshot()
        candidate = policies.validate_yaml(source)
        from .controls import ControlPlane
        trusted = policies.verify_stored_snapshot(snap)
        before_plane = policies.plane_for(snap)
        candidate_plane = ControlPlane(policies.policy_directory(), config=candidate, feed=snap["feed"],
            tools=trusted["tools"], models=trusted["models"], scanner=policies.shared_scanner(), prices=trusted.get("prices"))
        principal_id = "synthetic-replay:" + uid()
        with transaction() as db:
            db.add(Principal(id=principal_id, tenant="synthetic_test_tenant", role="analyst"))
        test_actor = {"sub": principal_id, "tenant": "synthetic_test_tenant", "role": "analyst"}
        corpus = json.loads((ROOT / "tests/corpus/semantic_calibration_v1.json").read_text(encoding="utf-8"))
        same_semantics = candidate["semantic"] == snap["configuration"]["semantic"]
        for case in corpus["cases"]:
            persist()
            if policies.snapshot()["generation"] != snap["generation"]:
                raise ValueError("Active generation changed during comparison")
            info = broker.create_run(RunRequest(document_ids=[]), test_actor)
            with transaction() as db:
                run = db.get(Run, info["id"])
            verdict = await broker.semantic_scan(case["text"], run, uid(), snap, purpose=case["purpose"], effect=case["effect"])
            before_scan = before_plane.scan(case["text"], tenant="synthetic_test_tenant")
            after_scan = candidate_plane.scan(case["text"], tenant="synthetic_test_tenant")
            before = await candidate_decision(snap["configuration"], snap["generation"], before_scan, verdict)
            after = await candidate_decision(candidate, snap["generation"], after_scan, verdict) if same_semantics else {
                "decision": "insufficient_evidence", "reason": "Candidate semantic runtime differs; activate and verify the new worker before comparing its model judgments"}
            records.append({"name": case["id"], "passed": verdict.get("complete") is True and after["decision"] != "insufficient_evidence",
                "pass_criterion": "Complete guard evidence and both policy decisions; this is not a held-out quality score",
                "language": case["language"], "expected": case["expected"], "before": before, "after": after,
                "changed": before["decision"] != after["decision"], "usage": verdict.get("usage"),
                "cache_hit": verdict.get("cache_hit", False), "cache_source": verdict.get("cache_source"),
                "guard": {key: verdict.get(key) for key in ("complete", "verdict", "risk_level", "model_digest")},
                "budget_run_id": run.id, "budget_tenant": "synthetic_test_tenant", "effects_executed": 0,
                "generation": snap["generation"], "candidate_digest": digest(candidate),
                "mode": broker.EXECUTION_MODE + "-synthetic-policy-replay"})
            persist()
        persist("passed" if all(row["passed"] for row in records) else "failed")
    except JobLost:
        return
    except Exception as exc:
        records.append({"name": "comparison.incomplete", "passed": False, "error": type(exc).__name__, "effects_executed": 0})
        try:
            persist("failed")
        except JobLost:
            pass


@router.post("/api/policies/compare-synthetic")
async def start_comparison(body: YamlRequest, actor=Depends(identity)):
    require_role(actor, "admin")
    policies.validate_yaml(body.yaml)
    test_id = create_job(actor["tenant"], "synthetic-policy-replay", policies.snapshot()["configuration"])
    from .app import background
    background(run_owned(test_id, compare_synthetic, body.yaml, actor))
    return {"id": test_id, "suite": "synthetic-policy-replay", "status": "running", "effects_executed": 0,
            "budget_tenant": "synthetic_test_tenant", "cases": 16}
