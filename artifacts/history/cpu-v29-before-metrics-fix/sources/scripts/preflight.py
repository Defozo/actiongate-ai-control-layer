"""Run real infer/usage/tool-call/watchdog proof, persist honest evidence."""
from pathlib import Path
import json
import os
import subprocess
import sys
from runtime_bootstrap import compose
from runtime_evidence import physical_runtime, producer_sources

root = Path(__file__).resolve().parents[1]
target = root/"artifacts/runtime-preflight.json"
target.parent.mkdir(exist_ok=True)
sources = producer_sources('scripts/preflight.py', 'scripts/preflight_container.py', 'scripts/runtime_bootstrap.py', 'scripts/runtime_evidence.py')
try:
    result = compose(["exec", "-T", "gateway-a", "python", "-"], input=(root/"scripts/preflight_container.py").read_text(encoding="utf-8"), stdout=subprocess.PIPE, text=True)
except subprocess.CalledProcessError as exc:
    target.write_text(exc.stdout or '{"passed":false,"error":"Preflight process failed"}', encoding="utf-8")
    print(exc.stderr or "Functional preflight failed. See artifacts/runtime-preflight.json", file=sys.stderr)
    raise SystemExit(1)
report = json.loads(result.stdout)
report['physical_runtime'] = physical_runtime(os.getenv('COMPOSE_PROJECT_NAME', 'actiongate'), report['model_artifacts'])
report['runtime_profile'] = (report['physical_runtime']['profile'] or 'unverified').lower()
report['producer_sources'] = sources
report['producer_source_stable'] = sources == producer_sources(*sources)
report['passed'] = report['passed'] and report['physical_runtime']['passed'] and report['producer_source_stable']
target.write_text(json.dumps(report, indent=2)+'\n', encoding="utf-8")
if not report['passed']:
    raise SystemExit('Physical runtime or producer source verification failed')
print(f"Functional preflight passed. Evidence: {target}")
