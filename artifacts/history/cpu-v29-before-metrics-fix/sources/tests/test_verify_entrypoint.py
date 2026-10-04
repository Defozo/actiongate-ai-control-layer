"""Optional paid verification is explicit, and absent configuration is not PASS."""
import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path
import xml.etree.ElementTree as ET

import httpx
import pytest


def runner_module():
    path = Path(__file__).resolve().parents[1] / "scripts/verify_container.py"
    spec = importlib.util.spec_from_file_location("verify_entrypoint_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("status,body,expected", [
    (200, {"configured": True, "price_ready": True}, "ready"),
    (503, {"configured": False, "price_ready": True}, "not_run"),
    (503, {"configured": True, "price_ready": False}, "failed"),
    (200, {"configured": True}, "failed"),
])
def test_readiness_checks_only_isolated_connector(monkeypatch, status, body, expected):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status, json=body)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setenv("CLOUD_CONNECTOR_URL", "http://connector:8030")
    assert runner_module().live_provider_readiness()["status"] == expected
    assert calls == [("http://connector:8030/health/ready", {"timeout": 6, "follow_redirects": False, "trust_env": False})]


def test_optional_unavailable_is_not_run(monkeypatch):
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("unavailable")
    monkeypatch.setattr(httpx, "get", unavailable)
    assert runner_module().live_provider_readiness()["status"] == "not_run"


def test_missing_optional_configuration_writes_truthful_reports(tmp_path):
    module = runner_module()
    assert module.write_live_preflight_report(tmp_path, {"status": "not_run", "reason": "Connector not configured"}) == 2
    report = json.loads((tmp_path / "live-provider.json").read_text())
    assert report["status"] == "not_run" and report["passed"] == 0
    assert ET.parse(tmp_path / "live-provider.xml").find("testcase/skipped") is not None


def test_executed_source_fingerprint_detects_changes_without_binding_mutable_reports(tmp_path):
    module = runner_module()
    for name in ("backend/actiongate/broker.py", "tests/test_local.py", "artifacts/all-local.json", "policy/control.yaml"):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial")
    original = module.source_fingerprint(tmp_path, ["tests/test_local.py"])
    assert set(original) == {"backend/actiongate/broker.py", "tests/test_local.py"}
    (tmp_path/"artifacts/all-local.json").write_text("new report")
    (tmp_path/"policy/control.yaml").write_text("publication test")
    assert module.source_fingerprint(tmp_path, ["tests/test_local.py"]) == original
    (tmp_path/"backend/actiongate/broker.py").write_text("changed implementation")
    assert module.source_fingerprint(tmp_path, ["tests/test_local.py"]) != original


def test_source_fingerprint_covers_external_evidence_producers(tmp_path):
    module = runner_module()
    required = {"scripts/preflight.py", "scripts/clean_install.py", "scripts/runtime_hardware.py", "scripts/isolation.py",
        "scripts/runtime_configuration.py", "scripts/verify_clients.py", "scripts/verify_policy_replay.py",
        "scripts/verify_ui_workflow.cjs", "scripts/verify_dashboard_events.cjs", "scripts/verify_live_browser.cjs"}
    assert required <= module.REQUIRED_EVIDENCE_PRODUCERS
    for name in required:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("executed producer")
    before = module.source_fingerprint(tmp_path, [])
    assert set(before) == required
    (tmp_path/"scripts/clean_install.py").write_text("later change")
    assert module.source_fingerprint(tmp_path, []) != before


def test_configuration_wrapper_reexecutes_itself_with_only_project_secret_names(monkeypatch):
    script = Path(__file__).resolve().parents[1]/"scripts/runtime_configuration.py"
    monkeypatch.syspath_prepend(str(script.parent))
    spec = importlib.util.spec_from_file_location("runtime_configuration_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module.shutil, "which", lambda name: "psst")
    monkeypatch.setattr(module.subprocess, "run", lambda args: calls.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(sys, "argv", [str(script)])
    assert module.main() == 0
    assert calls == [["psst", *module.NAMES, "--", sys.executable, str(script.resolve()), "--injected"]]
