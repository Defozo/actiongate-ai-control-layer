"""Aggregate existing third-matrix evidence without rerunning any workload."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "scripts"))
from acceptance_report import benchmark_check, benchmark_source_check

def read(name):
    return json.loads((root / name).read_bytes())

benchmark = read("artifacts/benchmark.json")
diagnostic = read("artifacts/benchmark-pg200-keepalive30-diagnostics.json")
runtime = read("artifacts/benchmark-pg200-keepalive30-runtime-audit.json")
semantic = read("artifacts/benchmark-pg200-keepalive30-semantic-capacity.json")
resources = read("artifacts/benchmark-pg200-keepalive30-resources.json")
db = diagnostic["database"]
ops = db["noncompleted_or_unreported_operations"]
blocked = [op for op in ops if op["status"] == "blocked"]
zero = sum(all(r["usage"].get("total_tokens") == 0 and r["usage"].get("usage_unknown") is False for r in op["reservations"]) for op in blocked)
measurement, detail = benchmark_check(benchmark)
source, source_detail = benchmark_source_check(benchmark, root)
checks = {
    "complete_valid_matrix": measurement,
    "current_unchanged_source": source,
    "all_fresh_roots_and_operations_accounted": db["fresh_workflow_count"] == db["operation_count"] == db["sample_operation_id_count"] == 2928 and db["sample_operation_ids_unique"] and not db["roots_without_operation"],
    "exact_completed_effects_and_receipts": not db["duplicate_report_names"] and not db["completed_missing_or_duplicate_effect"] and not db["local_receipt_mismatches"],
    "no_unsettled_or_uncertain_cost": not db["unsettled_reservations"] and not db["publication_uncertain_roots"] and not any(db["remaining_reserved_by_unit"].values()),
    "budget_invariants": not db["negative_accounts"] and not db["overcommitted_accounts"],
    "capacity_failures_before_dispatch": runtime["capacity_failure_audit"]["all_have_released_guard_output"] and runtime["capacity_failure_audit"]["none_have_execution_reservation"],
    "output_blocks_have_known_zero_inspection_and_one_legal_effect": runtime["output_blocked"]["all_output_inference_known_zero"] and runtime["output_blocked"]["all_local_effect_exact_one"] and all(not op["result_retained"] for op in ops if op["status"] == "output_blocked"),
    "no_runtime_restart_oom_or_filtered_errors": runtime["read_error"]["gateway_restarts_zero"] and runtime["read_error"]["gateway_oom_kills_zero"] and runtime["postgres_memory_failcnt_all_zero"] and runtime["postgres_oom_kill_all_zero"] and not any(runtime["gateway_error_log_counts"].values()),
    "postgres_observer_complete": resources["status"] == "passed" and resources["producer_source_stable"] and not resources["errors"],
}
report = {"mode": "actual-full-matrix-post-run-audit", "status": "passed" if all(checks.values()) else "failed", "checked_at": datetime.now(timezone.utc).isoformat(), "project": "actiongate-gpu-0b92f97d", "checks": checks,
    "measurement": detail, "source": source_detail, "operation_statuses": db["operation_statuses"],
    "effect_count": db["local_report_objects"], "capacity_before_dispatch": runtime["capacity_failure_audit"],
    "input_incomplete": {"count": len(blocked), "known_zero_input_inference": zero, "partial_known_inference": len(blocked)-zero,
        "scope": "All persisted input verdicts are incomplete unknown, not completed malicious classifications. No effect or execution reservation exists for these blocked operations. The stored metadata does not preserve each RuntimeFailure reason, so the 19 partial inspections are not assigned an unobserved exact failure cause."},
    "output_incomplete": {"count": len(semantic["output_blocked"]), "guard_phase_ms": [op["phase_timings_ms"]["guard_ms"] for op in semantic["output_blocked"]],
        "scope": "All three input judgments were complete benign cache hits. After the legal write, the reserved output inspection consumed zero tokens and waited approximately the configured 10-second queue deadline. The result was withheld; these are not model false positives or duplicate effects."},
    "resources": {key: value for key, value in resources.items() if key not in ("samples", "scope")},
    "performance_targets_met": detail["targets_met"], "limitations": ["The initial 50 ms target is not met; it remains separately failed.", "One cohort per matrix point yields empirical percentiles, not population confidence intervals.", "The earlier ReadError was not reproduced in this run; its exact original TCP cause remains unproven."],
    "evidence": {}}
names = ["artifacts/benchmark.json", "artifacts/benchmark.html", "artifacts/benchmark-pg200-keepalive30-diagnostics.json", "artifacts/benchmark-pg200-keepalive30-runtime-audit.json", "artifacts/benchmark-pg200-keepalive30-semantic-capacity.json", "artifacts/benchmark-pg200-keepalive30-resources.json", "artifacts/benchmark-reference/pg200-keepalive30-runtime-hardware.json", "compose.yaml"]
target = root / "artifacts/benchmark-reference/producers"
target.mkdir(parents=True, exist_ok=True)
for name in ("observe_pg.py", "diagnose_pg200_keepalive30.py", "runtime_audit_pg200_keepalive30.py", "semantic_capacity_pg200_keepalive30.py", "final_benchmark_audit.py"):
    shutil.copyfile(Path(__file__).parent / name, target / name)
    names.append((target / name).relative_to(root).as_posix())
for name in names:
    raw = (root / name).read_bytes()
    report["evidence"][name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
(root / "artifacts/benchmark-final-audit.json").write_text(json.dumps(report, indent=2)+"\n")
print(json.dumps({"status": report["status"], "checks": checks, "input_incomplete": report["input_incomplete"], "output_incomplete": report["output_incomplete"]}, indent=2))
