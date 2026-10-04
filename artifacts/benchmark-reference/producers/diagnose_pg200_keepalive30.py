"""Read only after the benchmark ends: actual effects, settlements and failures."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "actiongate-gpu-0b92f97d"
path = ROOT/"artifacts/benchmark.json"
raw = path.read_bytes()
benchmark = json.loads(raw)
assert benchmark["status"] != "running", "No diagnostic database reads during timed cohorts"
parameters = {"tenants": list(benchmark["workload_tenants"].values()),
    "sample_ids": [sample["operation_id"] for row in benchmark["rows"] for sample in row["samples"] if sample.get("operation_id")]}
encoded = base64.b64encode(json.dumps(parameters).encode()).decode()
code = '''import base64,json
from collections import Counter
from sqlalchemy import select
from sqlalchemy.orm import Session
from actiongate.db import engine,Operation,Reservation,DataObject,AuditEvent,RunContext,BudgetAccount,Run
from actiongate.security import decrypt
params=json.loads(base64.b64decode(PARAMETERS))
tenants=params["tenants"]
with engine().connect() as connection:
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    with Session(bind=connection) as session:
        operations=list(session.scalars(select(Operation).where(Operation.tenant.in_(tenants))))
        reservations=list(session.scalars(select(Reservation).where(Reservation.tenant.in_(tenants))))
        objects=list(session.scalars(select(DataObject).where(DataObject.tenant.in_(tenants))))
        events=list(session.scalars(select(AuditEvent).where(AuditEvent.tenant.in_(tenants)).order_by(AuditEvent.id)))
        contexts=list(session.scalars(select(RunContext).where(RunContext.tenant.in_(tenants))))
        accounts=list(session.scalars(select(BudgetAccount).where(BudgetAccount.tenant.in_(tenants))))
        runs=list(session.scalars(select(Run).where(Run.tenant.in_(tenants))))
        effect_counts=Counter(obj.name for obj in objects)
        effects={obj.name:obj for obj in objects}
        sample_ids=set(params["sample_ids"])
        interesting=[op for op in operations if op.status!="completed" or op.id not in sample_ids]
        interesting_ids={op.id for op in interesting}
        details=[]
        for op in interesting:
            details.append({"id":op.id,"run_id":op.run_id,"status":op.status,"stage":op.stage,
                "settlement_status":op.settlement_status,"rule_ids":op.rule_ids,"reason":op.reason,
                "generation":op.policy_generation,"reported_by_http_sample":op.id in sample_ids,
                "local_report_effect_count":effect_counts["report-"+op.id],"result_retained":bool(op.encrypted_result),
                "reservations":[{"id":r.id,"kind":r.kind,"status":r.status,"usage":r.usage} for r in reservations if r.operation_id==op.id],
                "audit":[{"id":e.id,"event":e.event,"evidence":{key:value for key,value in e.evidence.items() if key in
                    ("error_type","stage","settlement","decision","tool","generation","reason")}} for e in events if e.operation_id==op.id]})
        capacity={name:connection.exec_driver_sql("SHOW "+name).scalar_one() for name in
            ("max_connections","superuser_reserved_connections","reserved_connections")}
        receipt_mismatches=[]
        for op in operations:
            obj=effects.get("report-"+op.id)
            if op.status=="completed":
                receipt=decrypt(op.encrypted_result) if op.encrypted_result else None
                if not obj or not isinstance(receipt,dict) or receipt.get("id")!=obj.id or receipt.get("key")!=obj.name:
                    receipt_mismatches.append(op.id)
        result={"postgres_connection_limits":capacity,"fresh_workflow_count":len(runs),
            "sample_operation_id_count":len(params["sample_ids"]),"sample_operation_ids_unique":len(sample_ids)==len(params["sample_ids"]),
            "local_receipt_mismatches":receipt_mismatches,
            "roots_without_operation":[{"run_id":run.id,"created_at":run.created_at.isoformat()} for run in runs if run.id not in {op.run_id for op in operations}],
            "operation_count":len(operations),"operation_statuses":dict(Counter(op.status for op in operations)),
            "reservation_statuses":dict(Counter(r.status for r in reservations)),"local_report_objects":len(objects),
            "duplicate_report_names":{name:count for name,count in effect_counts.items() if count>1},
            "completed_missing_or_duplicate_effect":[op.id for op in operations if op.status=="completed" and effect_counts["report-"+op.id]!=1],
            "noncompleted_with_effect":[op.id for op in operations if op.status!="completed" and effect_counts["report-"+op.id]],
            "publication_uncertain_roots":[c.root_id for c in contexts if c.publication_uncertain],
            "unsettled_reservations":[{"id":r.id,"operation_id":r.operation_id,"kind":r.kind,"status":r.status} for r in reservations if r.status not in ("settled","released")],
            "negative_accounts":[a.id for a in accounts if min(a.spent,a.reserved)<0],
            "overcommitted_accounts":[a.id for a in accounts if a.spent+a.reserved>a.limit],
            "remaining_reserved_by_unit":{unit:sum(a.reserved for a in accounts if a.unit==unit) for unit in {a.unit for a in accounts}},
            "audit_error_types":dict(Counter(e.evidence.get("error_type") for e in events if e.evidence.get("error_type"))),
            "noncompleted_or_unreported_operations":details}
print(json.dumps(result))
'''.replace("PARAMETERS", repr(encoded))
result = subprocess.run(["docker", "exec", "-i", PROJECT+"-gateway-a-1", "python", "-"], input=code,
    text=True, capture_output=True, timeout=60)
if result.returncode:
    raise RuntimeError("Read-only matrix database diagnostic failed; no mutation performed")
report = {"mode": "actual-post-benchmark-read-only", "checked_at": datetime.now(timezone.utc).isoformat(),
    "project": PROJECT, "benchmark_sha256": hashlib.sha256(raw).hexdigest(), "database": json.loads(result.stdout)}
logs = {}
for role in ("gateway-a", "gateway-b", "postgres"):
    result = subprocess.run(["docker", "logs", "--since", benchmark["created_at"], "--timestamps", PROJECT+"-"+role+"-1"],
        text=True, capture_output=True, timeout=30)
    permitted = ("Request failed (", "too many clients", "remaining connection slots", "deadlock detected", "could not serialize")
    logs[role] = [line for line in (result.stdout+result.stderr).splitlines() if any(message in line for message in permitted)]
report["filtered_error_logs"] = logs
assert path.read_bytes() == raw
target = ROOT/"artifacts/benchmark-pg200-keepalive30-diagnostics.json"
target.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
print(json.dumps({key:value for key,value in report["database"].items() if key!="noncompleted_or_unreported_operations"}, indent=2))
