"""Metadata-only post-run audit; no model invocation or database writes."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
raw = (root / "artifacts/benchmark.json").read_bytes()
report = json.loads(raw)
assert report["status"] == "completed"
params = base64.b64encode(json.dumps(list(report["workload_tenants"].values())).encode()).decode()
code = '''import base64,json
from collections import Counter
from sqlalchemy import select
from sqlalchemy.orm import Session
from actiongate.db import engine,Operation,Reservation
tenants=json.loads(base64.b64decode(PARAMS))
with engine().connect() as conn:
 conn.exec_driver_sql("SET TRANSACTION READ ONLY")
 with Session(bind=conn) as db:
  ops=list(db.scalars(select(Operation).where(Operation.tenant.in_(tenants),Operation.status.in_(["blocked","output_blocked"]))))
  reservations=list(db.scalars(select(Reservation).where(Reservation.tenant.in_(tenants))))
  blocked=[op for op in ops if op.status=="blocked"]
  result={"input_blocked_count":len(blocked),"input_verdict_counts":dict(Counter(str(op.metadata_.get("semantic",{})) for op in blocked)),
   "input_reservations_known_zero":all(r.usage.get("total_tokens")==0 and r.usage.get("usage_unknown") is False for op in blocked for r in reservations if r.operation_id==op.id),
   "output_blocked":[{"operation_id":op.id,"generation":op.policy_generation,"phase_timings_ms":op.metadata_.get("phase_timings_ms"),"input_semantic":op.metadata_.get("semantic"),"reservations":[{"kind":r.kind,"status":r.status,"usage":r.usage} for r in reservations if r.operation_id==op.id]} for op in ops if op.status=="output_blocked"]}
print(json.dumps(result))
'''.replace("PARAMS", repr(params))
result = subprocess.run(["docker", "exec", "-i", "actiongate-gpu-0b92f97d-gateway-a-1", "python", "-"], input=code, capture_output=True, text=True, timeout=60)
assert result.returncode == 0, "Read-only metadata query failed"
output = {"mode": "actual-post-benchmark-metadata-only", "benchmark_sha256": hashlib.sha256(raw).hexdigest(), "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), **json.loads(result.stdout)}
assert (root / "artifacts/benchmark.json").read_bytes() == raw
(root / "artifacts/benchmark-pg200-keepalive30-semantic-capacity.json").write_text(json.dumps(output, indent=2)+"\n")
print(json.dumps(output, indent=2))
