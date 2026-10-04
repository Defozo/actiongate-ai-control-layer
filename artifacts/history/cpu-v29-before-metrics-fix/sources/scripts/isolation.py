from pathlib import Path
import json
import subprocess
from runtime_bootstrap import compose, NAMES
from runtime_evidence import producer_sources

if __name__ == "__main__":
    import sys
    if "--injected" not in sys.argv:
        import shutil, os
        psst = shutil.which("psst.cmd" if os.name == "nt" else "psst")
        raise SystemExit(subprocess.run([psst, *NAMES, "--", sys.executable, str(Path(__file__).resolve()), "--injected"]).returncode)
    sources = producer_sources("scripts/isolation.py", "scripts/runtime_bootstrap.py", "scripts/runtime_evidence.py", "deploy/agent_probe.py")
    result = compose(["--profile", "test", "run", "--rm", "agent-probe"], capture_output=True, text=True)
    report = json.loads(result.stdout)
    report["producer_sources"] = sources
    report["producer_source_stable"] = producer_sources(*sources) == sources
    report["passed"] = report["passed"] and report["producer_source_stable"]
    target = Path(__file__).resolve().parents[1]/"artifacts/isolation.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
