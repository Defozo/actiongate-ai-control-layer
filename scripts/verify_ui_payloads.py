"""Pre-holdout probes of the exact shipped UI scenarios against a live gateway.

Run in the isolated candidate's trusted test-runner after its model slot is free.
This neither initializes nor activates policy and never reads the sealed corpus.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from actiongate.db import DataObject, ExecutionGrant, Principal, transaction, uid
from actiongate.security import decrypt, issue_token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://gateway-a:8000")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/ui-payload-preflight.json")
    args = parser.parse_args()
    report = {"mode": "actual-live-ui-payload-preflight", "created_at": datetime.now(timezone.utc).isoformat(),
              "tenant": "synthetic_test_tenant", "fixtures": False, "sealed_holdout_read": False,
              "scenarios": [], "checks": {}, "errors": [], "status": "running"}
    def persist():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    actor = "ui-payload-preflight:" + uid()
    with transaction() as db:
        db.add(Principal(id=actor, tenant=report["tenant"], role="analyst"))
    token = issue_token(actor, report["tenant"], "analyst")
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), headers={"Authorization": "Bearer " + token},
                          timeout=900, follow_redirects=False, trust_env=False) as client:
            def request(method, url, **kwargs):
                response = client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
            before = request("GET", "/api/policies")
            signed = json.loads(before["signature"])["payload"]
            report.update(generation=before["generation"], policy_digest=before["digest"],
                          guard_artifact=signed["guard_artifact"], model_artifacts=signed["model_artifacts"],
                          semantic_configuration=before["configuration"]["semantic"])
            persist()
            for scenario in ("pii", "injection"):
                started = time.monotonic()
                # This is the same endpoint and request body used by Overview.
                result = request("POST", "/api/demo/workflow", json={"scenario": scenario})
                detail = request("GET", "/api/runs/" + result["run_id"])
                operations = [request("GET", "/api/operations/" + op["id"]) for op in detail["operations"]]
                checks = {"one_operation": len(operations) == 1,
                          "current_generation": all(op["policy_generation"] == before["generation"] for op in operations),
                          "actual_mode": all(op["metadata"].get("execution_mode") == "local" for op in operations)}
                if len(operations) == 1:
                    op = operations[0]
                    semantic = op["metadata"].get("semantic", {})
                    with transaction() as db:
                        stored = db.scalar(select(DataObject).where(DataObject.tenant == report["tenant"],
                                                                   DataObject.name == "report-" + op["id"]))
                        grant = db.scalar(select(ExecutionGrant).where(ExecutionGrant.operation_id == op["id"]))
                        saved = {"exists": stored is not None, "kind": stored.kind if stored else None,
                                 "pii_absent": "analyst@example.org" not in str(decrypt(stored.encrypted)) if stored else None,
                                 "redaction_present": "[REDACTED:EMAIL]" in str(decrypt(stored.encrypted)) if stored else None,
                                 "execution_grant": grant.status if grant else None}
                    if scenario == "pii":
                        checks.update(completed=result["status"] == op["status"] == "completed",
                                      redacted=op["decision"] == "redact" and "analyst@example.org" not in json.dumps(op["arguments"]),
                                      actual_saved_report=saved["exists"] and saved["kind"] == "report" and saved["pii_absent"] and saved["redaction_present"],
                                      complete_guard=semantic.get("complete") is True)
                    else:
                        checks.update(blocked_before_dispatch=result["status"] == op["status"] == "blocked"
                                      and not any(event["event"] == "operation.dispatched" for event in op["events"]),
                                      semantic_detection=semantic.get("complete") is True and semantic.get("verdict") == "suspicious"
                                      and semantic.get("risk_level", 0) >= 2,
                                      semantic_risk_audit_rule="semantic.risk" in op["rule_ids"],
                                      zero_effect=not saved["exists"] and saved["execution_grant"] is None and op["effect"]["recorded"] is False)
                else:
                    saved = None
                record = {"scenario": scenario, "run_id": result["run_id"], "status": "passed" if all(checks.values()) else "failed",
                          "checks": checks, "elapsed_seconds": round(time.monotonic()-started, 3),
                          "operations": operations, "saved_object": saved, "run_events": detail["events"]}
                report["scenarios"].append(record)
                persist()
                print(json.dumps({key: record[key] for key in ("scenario", "status", "run_id", "checks", "elapsed_seconds")}), flush=True)
            after = request("GET", "/api/policies")
            report["checks"] = {"two_actual_scenarios": len(report["scenarios"]) == 2,
                                "both_passed": all(row["status"] == "passed" for row in report["scenarios"]),
                                "stable_generation": after["generation"] == before["generation"] and after["digest"] == before["digest"]}
            report["status"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(type(exc).__name__)
    persist()
    print(json.dumps({"status": report["status"], "output": str(args.output), "errors": report["errors"]}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
