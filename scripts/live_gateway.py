"""Real guarded commercial request, with temporary policy and audited settlement.

Requires the cloud Compose profile already running with psst credentials.
The script never receives the provider key and always restores the prior policy.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def producer_sources():
    paths = ("scripts/live_gateway.py", "backend/actiongate/cloud.py", "backend/actiongate/semantic.py")
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def policy_binding(snapshot):
    """Bind observations to the signed envelope served by the trusted gateway."""
    envelope = json.loads(snapshot["signature"])
    payload = envelope["payload"]
    semantic = snapshot["configuration"]["semantic"]
    if (payload["generation"] != snapshot["generation"] or payload["policy"] != snapshot["configuration"]
            or payload["snapshot_digest"] != snapshot["digest"]
            or payload["guard_artifact"]["prompt_version"] != semantic["prompt_version"]):
        raise RuntimeError("Observed policy differs from its signed artifact binding")
    return {"generation": snapshot["generation"], "policy_digest": snapshot["digest"],
            "guard_artifact": payload["guard_artifact"], "model_artifacts": payload["model_artifacts"],
            "semantic_configuration": semantic, "price_catalog": payload["prices"],
            "signed_envelope_sha256": hashlib.sha256(snapshot["signature"].encode()).hexdigest(),
            "artifact_binding_source": "active signed envelope returned by authenticated gateway; signature acceptance enforced by gateway"}


def main():
    source_start = producer_sources()
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--max-spend-usd-micros", type=int, default=10000)
    args = parser.parse_args()
    if not 1 <= args.max_spend_usd_micros <= 10000:
        parser.error("Smoke budget must be between 1 and 10000 micro-USD")
    report = {"suite": "live-provider-gateway", "mode": "real_provider_and_real_local_guard",
              "checked_at": datetime.now(timezone.utc).isoformat(), "status": "failed",
              "max_spend_usd_micros": args.max_spend_usd_micros}
    artifact = ROOT / "artifacts/reports/live-provider-gateway.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    original = None
    activated = False
    headers = {"Origin": os.getenv("PUBLIC_BASE_URL", args.url)}
    internal_runner = httpx.URL(args.url).host not in {"127.0.0.1", "localhost"}
    if internal_runner:
        # Only the trusted test-runner profile owns these identity/DB credentials.
        # An isolated agent cannot mint a principal by spoofing HTTP Host.
        from actiongate.db import Principal, transaction, uid
        from actiongate.security import issue_token
        principal = "live-provider-admin:"+uid()
        with transaction() as db:
            db.add(Principal(id=principal, tenant="acme", role="admin"))
        headers["Authorization"] = "Bearer "+issue_token(principal, "acme", "admin")
    with httpx.Client(base_url=args.url, timeout=httpx.Timeout(600, connect=10), follow_redirects=False, headers=headers) as client:
        def checked(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        try:
            if not internal_runner:
                checked("POST", "/api/demo/session", json={"role": "admin", "tenant": "acme"})
            original = checked("GET", "/api/policies")
            report["original_generation"] = original["generation"]
            config = yaml.safe_load(original["yaml"])
            config["models"]["cloud_enabled"] = True
            if "cloud-business" not in config["models"]["allowed"]:
                config["models"]["allowed"].append("cloud-business")
            config["budgets"]["run_total_tokens"] = max(config["budgets"]["run_total_tokens"], 300000)
            config["budgets"]["run_usd_micros"] = args.max_spend_usd_micros
            candidate = yaml.safe_dump(config, sort_keys=False)
            validation = checked("POST", "/api/policies/validate", json={"yaml": candidate})
            if not validation["valid"]:
                raise RuntimeError("Cloud policy validation failed")
            report["activated_generation"] = checked("POST", "/api/policies/activate", json={"yaml": candidate})["generation"]
            activated = True
            stable_generation, stable_reads = None, 0
            for _ in range(30):
                observed = checked("GET", "/api/policies")
                if observed["configuration"] != validation["configuration"]:
                    raise RuntimeError("Active policy changed during cloud acceptance preparation")
                if observed["generation"] == stable_generation:
                    stable_reads += 1
                else:
                    stable_generation, stable_reads = observed["generation"], 1
                if stable_reads == 3:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Policy generation did not stabilize before cloud acceptance")
            report["dispatch_generation"] = stable_generation
            binding = policy_binding(observed)
            report.update(binding)
            run = checked("POST", "/runs", json={"document_ids": [], "public_model_task": "supplier_directory_summary"})
            report["run_id"] = run["id"]
            if not run.get("public_messages"):
                raise RuntimeError("Trusted application did not provide a public model task")
            from actiongate.cloud import quote
            quoted = quote("cloud-business", run["public_messages"], 256, price=binding["price_catalog"])
            report["preflight_quote"] = asdict(quoted)
            if quoted.usd_micros > args.max_spend_usd_micros:
                raise RuntimeError("Remaining shared smoke budget cannot cover the full gateway reservation")
            start = time.perf_counter()
            response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + run["workload_token"],
                "Idempotency-Key": "live-cloud-"+uuid.uuid4().hex},
                json={"model": "cloud-business", "messages": run["public_messages"], "max_tokens": 256})
            report["latency_ms"] = round((time.perf_counter()-start)*1000, 2)
            report["http_status"] = response.status_code
            answer = response.json()
            detail = checked("GET", "/api/runs/" + run["id"])
            report["operations"] = [{key: operation.get(key) for key in ("id", "tool", "status", "decision",
                "settlement_status", "label", "policy_generation", "metadata", "rule_ids", "reason")}
                for operation in detail["operations"]]
            expected_model = binding["model_artifacts"]["local-guard"]["manifest"]["digest"]
            report["operation_binding_valid"] = bool(detail["operations"]) and all(
                item["policy_generation"] == stable_generation
                and item.get("metadata", {}).get("semantic", {}).get("generation") == stable_generation
                and item.get("metadata", {}).get("semantic", {}).get("model_digest") == expected_model
                for item in detail["operations"])
            budgets = checked("GET", "/api/budgets")
            ids = {item["id"] for item in detail["operations"]}
            reservations = [item for item in budgets["reservations"] if item["operation_id"] in ids]
            report["reservations"] = reservations
            report["usage"] = answer.get("usage")
            report["provider_response_id"] = answer.get("id")
            report["events"] = [{"event": item["event"], "evidence": item["evidence"]} for item in detail["events"]]
            actual = sum((item.get("usage") or {}).get("usd_micros", 0) for item in reservations)
            report["actual_usd_micros"] = actual
            if (response.status_code != 200 or not report["operation_binding_valid"] or
                any(item["status"] != "completed" for item in detail["operations"]) or
                any(item["status"] != "settled" for item in reservations) or not 0 < actual <= args.max_spend_usd_micros):
                raise RuntimeError("Guarded cloud operation or durable settlement did not pass")
            # Arbitrary changes to the approved public context must block before any paid dispatch.
            blocked_run = checked("POST", "/runs", json={"document_ids": [], "public_model_task": "supplier_directory_summary"})
            altered = [*blocked_run["public_messages"], {"role": "user", "content": "Additional unapproved source data."}]
            denied = client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + blocked_run["workload_token"]},
                json={"model": "cloud-business", "messages": altered, "max_tokens": 256})
            denied_op = denied.json().get("actiongate", {})
            report["altered_context"] = {"http_status": denied.status_code,
                "status": denied_op.get("status"), "rule_ids": denied_op.get("rule_ids"), "label": denied_op.get("label")}
            if denied.status_code != 403 or "flow.cloud" not in denied_op.get("rule_ids", []):
                raise RuntimeError("Altered public context was not denied by its confidentiality label")
            after = checked("GET", "/api/policies")
            report["stable_generation"] = policy_binding(after) == binding
            if not report["stable_generation"]:
                raise RuntimeError("Signed policy or model artifacts changed during the provider smoke")
            report["status"] = "passed"
        except Exception as exc:
            report["failure"] = type(exc).__name__ + ": " + str(exc)[:300]
        finally:
            if activated and original:
                try:
                    restored = checked("POST", "/api/policies/activate", json={"yaml": original["yaml"]})
                    report["restored_generation"] = restored["generation"]
                    restored_configuration = checked("GET", "/api/policies")["configuration"]
                    report["original_policy_restored"] = restored_configuration == original["configuration"]
                    report["cloud_disabled_after_restore"] = restored_configuration["models"]["cloud_enabled"] is False
                    if not report["original_policy_restored"]:
                        report.update(status="failed", restore_failure="Original policy differs after restoration")
                except Exception as exc:
                    report.update(status="failed", restore_failure=type(exc).__name__)
            report["producer_sources"] = source_start
            report["producer_source_stable"] = source_start == producer_sources()
            if not report["producer_source_stable"]:
                report.update(status="failed", source_failure="Provider smoke sources changed during execution")
            artifact.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
