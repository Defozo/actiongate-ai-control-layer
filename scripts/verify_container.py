"""Fixed test suite entrypoint. Missing components and skipped required tests fail."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_EVIDENCE_PRODUCERS = {
    "scripts/acceptance_report.py", "scripts/benchmark.py", "scripts/benchmark.ps1", "scripts/benchmark.sh",
    "scripts/check_release.py", "scripts/clean_install.py", "scripts/coverage_report.py", "scripts/deployed_source.py",
    "scripts/gpu_concurrency_probe.py", "scripts/gpu_inventory.py", "scripts/gpu_preflight.py", "scripts/isolation.py",
    "scripts/preflight.py", "scripts/preflight_container.py", "scripts/release.py", "scripts/runtime_bootstrap.py",
    "scripts/runtime_configuration.py", "scripts/runtime_configuration_probe.py", "scripts/runtime_evidence.py", "scripts/runtime_hardware.py",
    "scripts/scan_release_secrets.py", "scripts/verify.ps1", "scripts/verify.sh", "scripts/verify_clients.py",
    "scripts/verify_container.py", "scripts/verify_policy_replay.py", "scripts/verify_ui_workflow.cjs",
    "scripts/verify_dashboard_events.cjs", "scripts/sse_reconnect_probe.cjs", "scripts/verify_live_browser.cjs",
    "deploy/agent_probe.py", "tests/control_registry.json",
}


def source_fingerprint(root, selected_tests):
    """Identify executed application/test code without mutable policy or evidence."""
    paths = set((root/"backend").rglob("*.py"))
    paths.update(root/name for name in selected_tests)
    paths.update(root/name for name in REQUIRED_EVIDENCE_PRODUCERS)
    paths.update(root/name for name in ("tests/conftest.py", "scripts/verify_container.py", "scripts/coverage_report.py"))
    tested_scripts = {"tests/test_acceptance_report.py": ("scripts/acceptance_report.py",),
        "tests/test_bootstrap_artifacts.py": ("scripts/runtime_bootstrap.py",),
        "tests/test_release.py": ("scripts/release.py", "scripts/check_release.py", "scripts/scan_release_secrets.py", "scripts/runtime_bootstrap.py")}
    paths.update(root/script for test, scripts in tested_scripts.items() if test in selected_tests for script in scripts)
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths) if path.is_file()}


def live_provider_readiness():
    """Inspect the isolated connector without receiving its provider credential."""
    import httpx
    try:
        response = httpx.get(os.getenv("CLOUD_CONNECTOR_URL", "http://cloud-connector:8030").rstrip("/")+"/health/ready",
            timeout=6, follow_redirects=False, trust_env=False)
        value = response.json()
    except (httpx.HTTPError, ValueError):
        return {"status": "not_run", "reason": "Optional cloud connector is unavailable; start the cloud profile with its psst credential."}
    if not isinstance(value, dict):
        return {"status": "failed", "reason": "Cloud connector readiness response has an invalid schema."}
    if value.get("configured") is False:
        return {"status": "not_run", "reason": "Optional cloud connector has no complete credential configuration."}
    if response.status_code != 200 or value.get("configured") is not True or value.get("price_ready") is not True:
        return {"status": "failed", "reason": "Cloud connector did not confirm readiness and a valid reviewed price."}
    return {"status": "ready"}


def write_live_preflight_report(output, readiness):
    import html
    not_run = readiness["status"] == "not_run"
    report = {"suite": "live-provider", **readiness, "created_at": datetime.now(timezone.utc).isoformat(),
        "total": 1, "passed": 0, "failed": 0 if not_run else 1, "skipped": 1 if not_run else 0,
        "mode": "optional connector readiness only; no paid inference was attempted", "duration_seconds": readiness.get("duration_seconds")}
    (output / "live-provider.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    suite = ET.Element("testsuite", name="live-provider", tests="1", failures=str(report["failed"]), skipped=str(report["skipped"]))
    case = ET.SubElement(suite, "testcase", name="optional_provider_readiness")
    ET.SubElement(case, "skipped" if not_run else "failure", message=report["reason"])
    ET.ElementTree(suite).write(output / "live-provider.xml", encoding="utf-8", xml_declaration=True)
    (output / "live-provider.html").write_text("<!doctype html><meta charset=utf-8><title>ActionGate live provider</title><h1>"
        +html.escape(report["status"])+"</h1><p>"+html.escape(report["reason"])+"</p><p>No paid inference was attempted.</p>", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 2 if not_run else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["contract", "all-local", "live-provider"], default="all-local")
    args = parser.parse_args()
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(ROOT/"backend"), PYTHONDONTWRITEBYTECODE="1", HYPOTHESIS_STORAGE_DIRECTORY="/tmp/actiongate-hypothesis")
    env["ACTIONGATE_TEST_OPA_URL"] = env.get("OPA_URL", "http://opa-a:8181")
    env["ACTIONGATE_SUITE"] = args.suite
    if args.suite == "live-provider":
        readiness_start = time.monotonic()
        readiness = live_provider_readiness()
        readiness["duration_seconds"] = round(time.monotonic()-readiness_start, 2)
        if readiness["status"] != "ready":
            return write_live_preflight_report(out, readiness)
        # Explicitly selecting this optional suite authorizes its bounded smoke.
        # The key remains in cloud-connector; this flag is not a credential.
        env["ACTIONGATE_LIVE_PROVIDER"] = "1"
    files = ["tests/test_controls.py", "tests/test_boundary_fuzz.py", "tests/test_mcp_gateway_contract.py", "tests/test_ledger.py", "tests/test_ledger_stateful.py", "tests/test_broker.py", "tests/test_api.py", "tests/test_recovery.py", "tests/test_cloud.py"]
    if args.suite == "all-local":
        files += ["tests/test_local.py"]
    if args.suite != "live-provider":
        files += ["tests/test_runtime_contract.py", "tests/test_bootstrap_artifacts.py", "tests/test_replay.py", "tests/test_audit_integrity.py", "tests/test_failure_recovery.py", "tests/test_telemetry.py", "tests/test_verify_entrypoint.py", "tests/test_model_artifacts.py", "tests/test_job_recovery.py", "tests/test_interactive_lab.py", "tests/test_policy_cache.py", "tests/test_semantic_evidence.py", "tests/test_acceptance_report.py", "tests/test_semantic_cache.py", "tests/test_release.py"]
    if args.suite == "all-local":
        files += ["tests/test_publication.py"]
    if args.suite == "live-provider":
        files = ["tests/test_cloud.py"]
    missing = [file for file in sorted(set(files) | REQUIRED_EVIDENCE_PRODUCERS) if not (ROOT/file).is_file()]
    if missing:
        raise RuntimeError("Required suite files are missing: " + ", ".join(missing))
    reference_runtime = None
    if args.suite == "all-local":
        reference_runtime = json.loads((out/"runtime-hardware.json").read_text(encoding="utf-8"))
        from acceptance_report import physical_runtime_check, producer_source_check
        artifact = reference_runtime.get("model_artifacts", {}).get("local-guard", {})
        physical, _ = physical_runtime_check(reference_runtime, "CPU", artifact)
        measured_sources, _ = producer_source_check(reference_runtime, ROOT, {"scripts/runtime_hardware.py", "scripts/runtime_evidence.py"})
        current_manifest = json.loads((ROOT/"models/model-manifest.json").read_text())
        if not physical or not measured_sources or artifact.get("manifest") != current_manifest:
            raise RuntimeError("Reference all-local requires measured, source-bound CPU worker placement before evaluation")
    started = time.monotonic()
    source_before = source_fingerprint(ROOT, files)
    command = [sys.executable, "-m", "pytest", *files, "-q", "-p", "no:cacheprovider", "--junitxml="+str(out/f"{args.suite}.xml")]
    if args.suite == "all-local":
        # Do not expose the sealed independent holdout after an earlier failure.
        # A green run still executes the complete required suite with zero skips.
        command.append("--maxfail=1")
    command += ["-m", "live_provider" if args.suite == "live-provider" else "not live_provider"]
    result = subprocess.run(command, cwd=ROOT, env=env)
    source_after = source_fingerprint(ROOT, files)
    source_stable = source_before == source_after
    tree = ET.parse(out/f"{args.suite}.xml")
    cases = tree.findall(".//testcase")
    skipped = sum(c.find("skipped") is not None for c in cases)
    failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
    passed = len(cases)-skipped-failed
    report = {"suite": args.suite, "status": "passed" if result.returncode == 0 and not skipped and source_stable else "failed",
        "created_at": datetime.now(timezone.utc).isoformat(), "total": len(cases), "passed": passed, "failed": failed,
        "skipped": skipped, "duration_seconds": round(time.monotonic()-started, 2), "hardware": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        "mode": "real PostgreSQL/OPA/HTTP; controlled inference in contract; real models in local cases",
        "reference_runtime": reference_runtime,
        "source_files": source_before, "source_stable_during_run": source_stable,
        "source_changed_during_run": sorted(name for name in set(source_before)|set(source_after) if source_before.get(name) != source_after.get(name)),
        "tests": [{"name": c.attrib.get("classname", "")+"."+c.attrib["name"], "duration_seconds": c.attrib.get("time"),
            "status": "failed" if c.find("failure") is not None or c.find("error") is not None else "skipped" if c.find("skipped") is not None else "passed"} for c in cases]}
    if args.suite != "live-provider":
        from coverage_report import build
        coverage = build(report, ROOT)
        report["control_coverage"] = {"covered": coverage["covered"], "total": coverage["total"]}
        if args.suite == "all-local" and coverage["covered"] != coverage["total"]:
            report["status"] = "failed"
    (out/f"{args.suite}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    html = "<!doctype html><meta charset=utf-8><title>ActionGate acceptance</title><style>body{font:16px system-ui;max-width:1100px;margin:3em auto;background:#101719;color:#e8eeec}td,th{padding:8px;text-align:left;border-bottom:1px solid #33443c}h1{color:#84e8b4}</style>"
    import html as escaping
    html += f"<h1>ActionGate {args.suite}: {report['status']}</h1><p>{passed} passed, {failed} failed, {skipped} skipped. {report['duration_seconds']} seconds.</p><table><tr><th>Test</th><th>Status</th><th>Seconds</th></tr>"
    for case in report["tests"]:
        html += "<tr>"+"".join("<td>"+escaping.escape(str(case[k]))+"</td>" for k in ("name", "status", "duration_seconds"))+"</tr>"
    (out/f"{args.suite}.html").write_text(html+"</table>", encoding="utf-8")
    print(json.dumps({k: v for k,v in report.items() if k != "tests"}, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
