"""Run the real synthetic policy comparison without activating the candidate.

The 16 calibration cases consume local guard resources. This verifies execution,
accounting and zero business dispatch, not held-out semantic quality.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/clients/python"))
from actiongate_client import NoRedirect


def producer_sources():
    paths = ["scripts/verify_policy_replay.py", "packages/clients/python/actiongate_client.py"]
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def main() -> int:
    source_start = producer_sources()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/submission/policy-replay.json")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise SystemExit("The trusted demo identity bootstrap is restricted to local HTTP.")
    opener = build_opener(NoRedirect(), ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def request(path, payload=None):
        with opener.open(Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Origin": base}), timeout=45) as response:
            return json.load(response)

    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "status": "running", "errors": [],
              "mode": "actual-local-synthetic-policy-replay", "tenant": "synthetic_test_tenant", "target": base,
              "quality_claim": False, "candidate_activated": False}
    try:
        request("/api/demo/session", {"role": "admin", "tenant": report["tenant"]})
        before = request("/api/policies")
        signed_payload = json.loads(before["signature"])["payload"]
        report.update(policy_digest=before["digest"], guard_artifact=signed_payload["guard_artifact"],
                      model_artifacts=signed_payload["model_artifacts"], semantic_configuration=before["configuration"]["semantic"])
        candidate = yaml.safe_load(before["yaml"])
        candidate["active_profile"] = "balanced"
        candidate["profiles"]["balanced"]["semantic_block_level"] = 3
        candidate["profiles"]["balanced"]["semantic_review_level"] = 2
        body = {"yaml": yaml.safe_dump(candidate, sort_keys=False)}
        report["candidate_change"] = "Balanced risk level 2 requires review; level 3 blocks. Candidate is query-local."
        validation = request("/api/policies/validate", body)
        if validation.get("valid") is not True:
            raise RuntimeError("Candidate did not validate")
        started = request("/api/policies/compare-synthetic", body)
        report["job_id"] = started["id"]
        last_total = -1
        while True:
            job = next(item for item in request("/api/tests")["runs"] if item["id"] == started["id"])
            if job["total"] != last_total:
                print(json.dumps({"job_id": job["id"], "completed_cases": job["total"], "status": job["status"]}), flush=True)
                last_total = job["total"]
            if job["status"] != "running":
                break
            time.sleep(3)
        after = request("/api/policies")
        run_ids = [row["budget_run_id"] for row in job["results"] if row.get("budget_run_id")]
        runs = [request("/api/runs/" + run_id) for run_id in run_ids]
        accounts = [account for account in request("/api/budgets")["accounts"]
                    if any(account["id"].startswith("root:" + run_id + ":") for run_id in run_ids)]
        checks = {"completed_16": job["total"] == 16 and job["status"] == "passed",
                  "complete_guard_and_decisions": all(row.get("passed") is True for row in job["results"]),
                  "no_business_operations": bool(runs) and all(not run["operations"] for run in runs),
                  "no_connector_receipt_events": all(not any(event["event"].startswith("connector.") for event in run["events"]) for run in runs),
                  "generation_unchanged": before["generation"] == after["generation"],
                  "active_yaml_unchanged": before["yaml"] == after["yaml"],
                  "actual_guard_tokens": sum(account["spent"] for account in accounts if account["unit"] == "tokens") > 0,
                  "synthetic_budget_only": all(row.get("budget_tenant") == report["tenant"] for row in job["results"]),
                  "real_execution_mode": all(row.get("mode") == "local-synthetic-policy-replay" for row in job["results"])}
        report.update(status="passed" if all(checks.values()) else "failed", generation=before["generation"],
                      checks=checks, job=job, budget_accounts=accounts, run_evidence=runs,
                      semantic_cache_hits=sum(row.get("cache_hit") is True for row in job["results"]),
                      semantic_cache_scope="Prior complete judgments may be reused with explicit provenance; cache hits do not start new inference or spend tokens.",
                      changed_decisions=sum(row.get("changed") is True for row in job["results"]))
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(type(exc).__name__)
    report["producer_sources"] = source_start
    report["producer_source_stable"] = source_start == producer_sources()
    if not report["producer_source_stable"]:
        report["status"] = "failed"
        report["errors"].append("ProducerSourceChanged")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
