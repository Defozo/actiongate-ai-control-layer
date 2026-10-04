"""Live complete-generation publication. Runs after real-model quality cases."""
import asyncio
import copy
import json
import os
from pathlib import Path
import time
import httpx
import pytest
import yaml
from sqlalchemy import select
from actiongate import policies
from actiongate.db import PolicyGeneration, transaction


async def test_partial_stage_never_reuses_generation_and_identical_update_is_noop(platform, monkeypatch):
    original = policies.snapshot()
    candidate = yaml.safe_load(original["yaml"])
    candidate["revision"] += 1
    source = yaml.safe_dump(candidate, sort_keys=False)
    original_stage = policies.stage
    prepared = []
    async def prepare_then_fail(generation, *args, **kwargs):
        await original_stage(generation, *args, **kwargs)
        prepared.append(generation)
        raise RuntimeError("Injected failure after component acknowledgements")
    monkeypatch.setattr(policies, "stage", prepare_then_fail)
    try:
        with pytest.raises(RuntimeError):
            await policies.activate(source, "synthetic_test_tenant")
        assert policies.snapshot()["generation"] == original["generation"]
        with transaction() as db:
            assert db.get(PolicyGeneration, prepared[0]).status == "rejected"
        monkeypatch.setattr(policies, "stage", original_stage)
        candidate["revision"] += 1
        source = yaml.safe_dump(candidate, sort_keys=False)
        start = time.perf_counter()
        activated = await policies.activate(source, "synthetic_test_tenant")
        assert activated["generation"] > prepared[0]
        same = await policies.activate(source, "synthetic_test_tenant")
        assert same["unchanged"] and same["generation"] == activated["generation"]
        replicas = []
        async with httpx.AsyncClient(timeout=15) as client:
            for url in (os.environ["SERVICE_BASE_URL"], os.environ["SECONDARY_BASE_URL"]):
                response = await client.get(url+"/health/ready")
                assert response.status_code == 200, response.text
                replicas.append(response.json())
        assert all(row["generation"] == activated["generation"] for row in replicas)
        seconds = time.perf_counter()-start
        report = {"status": "passed", "seconds_including_replicas_and_noop": seconds,
                  "target_seconds": 5, "target_met": seconds <= 5, "rejected_generation": prepared[0],
                  "active_generation": activated["generation"], "replicas": replicas}
        (Path(__file__).resolve().parents[1]/"artifacts/publication.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        monkeypatch.setattr(policies, "stage", original_stage)
        await policies.activate(original["yaml"], "synthetic_test_tenant")


async def test_live_feed_add_remove_changes_signed_generation_and_decision(platform):
    original = policies.snapshot()
    source = original["yaml"]
    old_rules = original["feed"]["rules"]
    rule = {"id": "regression.feed.hot", "scope": "text", "selector": "text", "operator": "equals",
            "value": "ActionGate inert hot-feed marker", "action": "block", "source": "https://example.com/synthetic-feed-fixture"}
    assert policies.plane_for(original).scan(rule["value"], tenant="synthetic_test_tenant").decision == "allow"
    try:
        envelope = await policies.publisher_call("POST", "/feed", {"rules": [*old_rules, rule], "expected_revision": original["feed"]["revision"]})
        feed = await policies.verified_feed(envelope, original["feed"]["revision"])
        # Let the real gateway watcher consume it, rather than directly activating here.
        for _ in range(80):
            current = policies.snapshot()
            if current["feed"]["revision"] == feed["revision"]:
                break
            await asyncio.sleep(.25)
        assert current["feed"]["revision"] == feed["revision"]
        assert policies.plane_for(current).scan(rule["value"], tenant="synthetic_test_tenant").decision == "block"
    finally:
        current = policies.snapshot()
        envelope = await policies.publisher_call("POST", "/feed", {"rules": old_rules, "expected_revision": current["feed"]["revision"]})
        restored = await policies.verified_feed(envelope, current["feed"]["revision"])
        await policies.activate(source, "synthetic_test_tenant", feed=restored)
    assert policies.plane_for(policies.snapshot()).scan(rule["value"], tenant="synthetic_test_tenant").decision == "allow"
