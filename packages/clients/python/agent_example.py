"""Run with a pre-created, run-bound workload token injected by the trusted app."""
import os
from actiongate_client import ActionGate

client = ActionGate(os.getenv("ACTIONGATE_BASE_URL", "http://127.0.0.1:8080"),
                    os.environ["ACTIONGATE_WORKLOAD_TOKEN"], os.environ["ACTIONGATE_RUN_ID"])
operation = client.action("documents.read", {"document_id": os.getenv("ACTIONGATE_DOCUMENT_ID", "supplier-acme-1")})
print({key: operation.get(key) for key in ("id", "decision", "status", "rule_ids", "label", "policy_generation")})
if operation["status"] != "completed":
    raise SystemExit("Document release did not complete. Inspect the operation instead of bypassing its controls.")
report = client.action("reports.save", {"title": "Supplier review", "content": "Supplier material was reviewed for the internal analyst."})
print({key: report.get(key) for key in ("id", "decision", "status", "settlement_status")})
