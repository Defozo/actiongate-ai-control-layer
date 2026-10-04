"""Run the real configuration probe and bind its exact producer source bytes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from runtime_bootstrap import compose, NAMES
from runtime_evidence import producer_sources


def main():
    if "--injected" not in sys.argv:
        executable = shutil.which("psst.cmd" if os.name == "nt" else "psst")
        return subprocess.run([executable, *NAMES, "--", sys.executable, str(Path(__file__).resolve()), "--injected"]).returncode
    root = Path(__file__).resolve().parents[1]
    names = ("scripts/runtime_configuration.py", "scripts/runtime_configuration_probe.py",
        "scripts/runtime_bootstrap.py", "scripts/runtime_evidence.py")
    sources = producer_sources(*names)
    try:
        completed = compose(["exec", "-T", "gateway-a", "python", "-"],
            input=(root/"scripts/runtime_configuration_probe.py").read_text(), capture_output=True, text=True)
        report = json.loads(completed.stdout)
    except (subprocess.CalledProcessError, ValueError):
        report = {"suite": "runtime-configuration", "mode": "real_worker_api", "checks": {}, "passed": False,
            "error": "Configuration probe did not complete successfully"}
    report["producer_sources"] = sources
    report["producer_source_stable"] = producer_sources(*names) == sources
    report["passed"] = report["passed"] and report["producer_source_stable"]
    target = root/"artifacts/runtime-configuration.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "producer_source_stable": report["producer_source_stable"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
