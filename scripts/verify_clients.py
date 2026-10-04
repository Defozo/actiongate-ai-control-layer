"""Execute the shipped Python and TypeScript agents against the live local gateway.

This is an inference-bearing acceptance check, not a fixture. Run it only after
the required services are ready and other model acceptance work has finished.
The trusted session and run-bound credentials remain in process memory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/clients/python"))
from actiongate_client import NoRedirect


def producer_sources():
    paths = ["scripts/verify_clients.py", "packages/clients/python/agent_example.py",
             "packages/clients/python/actiongate_client.py", "packages/clients/typescript/agent-example.ts",
             "packages/clients/typescript/actiongate.ts", "packages/clients/typescript/package.json"]
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def main() -> int:
    source_start = producer_sources()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/submission/client-examples.json")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise SystemExit("The trusted demo identity bootstrap is restricted to local HTTP.")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node 24 is required; this check cannot be skipped.")
    opener = build_opener(NoRedirect(), ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        with opener.open(Request(base + path, data=body, headers={"Content-Type": "application/json", "Origin": base}), timeout=1200) as response:
            return json.load(response)

    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "mode": "actual-live-client-examples",
              "target": base, "tenant": "synthetic_test_tenant", "status": "running", "examples": [], "errors": []}
    try:
        request("/api/demo/session", {"role": "admin", "tenant": report["tenant"]})
        before = request("/api/policies")
        signed_payload = json.loads(before["signature"])["payload"]
        report.update(generation=before["generation"], policy_digest=before["digest"],
                      guard_artifact=signed_payload["guard_artifact"], model_artifacts=signed_payload["model_artifacts"],
                      semantic_configuration=before["configuration"]["semantic"])
        for language, executable, example, expected_count in (
            ("Python", sys.executable, "packages/clients/python/agent_example.py", 2),
            ("TypeScript", node, "packages/clients/typescript/agent-example.ts", 1),
        ):
            run = request("/runs", {"purpose": "supplier_review", "document_ids": ["supplier-synthetic_test_tenant-1"], "allow_publish": False})
            # Agent children get no administrator session, provider, connector or DB key.
            allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
            environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
            environment.update(ACTIONGATE_BASE_URL=base, ACTIONGATE_RUN_ID=run["id"],
                               ACTIONGATE_WORKLOAD_TOKEN=run["workload_token"],
                               ACTIONGATE_DOCUMENT_ID="supplier-synthetic_test_tenant-1", PYTHONIOENCODING="utf-8")
            result = subprocess.run([executable, str(ROOT / example)], cwd=ROOT, env=environment,
                                    capture_output=True, text=True, encoding="utf-8", timeout=650, check=False)
            details = request("/api/runs/" + run["id"])
            operations = [request("/api/operations/" + operation["id"]) for operation in details["operations"]]
            documents = [operation for operation in operations if operation["tool"] == "documents.read"]
            checks = {"example_exit_zero": result.returncode == 0, "expected_operation_count": len(operations) == expected_count,
                      "all_completed": bool(operations) and all(operation["status"] == "completed" for operation in operations),
                      "real_execution": bool(operations) and all(operation.get("metadata", {}).get("execution_mode") == "local" for operation in operations),
                      "document_receipt": len(documents) == 1 and documents[0].get("effect", {}).get("recorded") is True,
                      "expected_generation": all(operation["policy_generation"] == before["generation"] for operation in operations),
                      "exact_run": all(operation["run_id"] == run["id"] for operation in operations)}
            report["examples"].append({"language": language, "source": example, "run_id": run["id"],
                                       "exit_code": result.returncode, "checks": checks,
                                       "operations": operations, "audit": details["events"],
                                       "status": "passed" if all(checks.values()) else "failed"})
        after = request("/api/policies")
        report["stable_generation"] = before["generation"] == after["generation"] and before["digest"] == after["digest"]
        report["status"] = "passed" if report["stable_generation"] and all(item["status"] == "passed" for item in report["examples"]) else "failed"
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
    print(json.dumps({"status": report["status"], "output": str(args.output),
                      "examples": [{"language": example["language"], "status": example["status"]} for example in report["examples"]]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
