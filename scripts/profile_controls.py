"""Read-only component timing; never a substitute for the full HTTP benchmark."""
import asyncio
import json
from pathlib import Path
import ssl
import time

import certifi
import httpx
from sqlalchemy import text

from actiongate import policies
from actiongate.controls import OPAClient
from actiongate.db import transaction
from actiongate.settings import settings


async def main():
    snapshot = policies.snapshot()
    plane = policies.plane_for(snapshot)
    sample = ("The supplier offers maintenance services with a five year warranty. " * 64)[:4096]
    payload = {"content": sample}
    output = {"saved": True, "id": "synthetic-profile-document"}
    opa = OPAClient(settings()["opa_url"])
    evidence = {"tenant": "synthetic_test_tenant", "tool": "reports.save",
                "deterministic": {"decision": "allow", "rule_ids": []},
                "semantic": {"verdict": "benign", "risk_level": 0, "complete": True},
                "identity_ok": True, "tenant_ok": True, "grant_ok": True,
                "labels_ok": True, "budget_ok": True, "registry_ok": True}

    def query():
        with transaction() as db:
            return db.scalar(text("SELECT 1"))

    trust_store = ssl.create_default_context(cafile=certifi.where())
    async def opa_with_prepared_trust():
        async with httpx.AsyncClient(timeout=5, trust_env=False, verify=trust_store) as client:
            response = await client.post(settings()["opa_url"] + f"/v1/data/actiongate/g{snapshot['generation']}/decision",
                                         json={"input": {**evidence, "generation": snapshot["generation"]}})
            response.raise_for_status()
            if response.json().get("result", {}).get("decision") != "allow":
                raise RuntimeError("Read-only OPA probe did not return its expected decision")

    operations = {
        "database_transaction_select_one": query,
        "current_signed_snapshot": policies.snapshot,
        "compiled_control_plane": lambda: policies.plane_for(snapshot),
        "input_dlp_4kib": lambda: plane.scan_payload(payload, redact_fields={"/content"}),
        "output_dlp_small": lambda: plane.scan_payload(output, redact_fields=set()),
        "opa_http": lambda: opa.evaluate(evidence, snapshot["generation"]),
        "opa_http_prepared_trust_store": opa_with_prepared_trust,
    }
    results = {}
    for name, operation in operations.items():
        samples = []
        for _ in range(30):
            began = time.perf_counter()
            value = operation()
            if asyncio.iscoroutine(value):
                await value
            samples.append(round((time.perf_counter() - began) * 1000, 3))
        results[name] = {"p50_ms": sorted(samples)[14], "p95_ms": sorted(samples)[28], "samples_ms": samples}
    report = {"mode": "sequential read-only component probe", "generation": snapshot["generation"],
              "controls": snapshot["configuration"]["performance"], "results": results,
              "scope": "No operation, reservation or connector effect was created. No concurrency or end-to-end latency claim."}
    Path("/app/artifacts/control-component-profile.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({name: {key: value for key, value in row.items() if key != "samples_ms"}
                      for name, row in results.items()}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
