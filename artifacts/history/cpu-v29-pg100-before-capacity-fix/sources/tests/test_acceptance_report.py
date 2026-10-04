"""Acceptance cannot convert stale evidence, unknowns or absent load into PASS."""
import copy
import importlib.util
import hashlib
from itertools import product
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("acceptance_report", Path(__file__).resolve().parents[1]/"scripts/acceptance_report.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def quality_fixture():
    cases, records = [], []
    for language, expected, index in product(("EN", "PL"), ("attack", "benign"), range(10)):
        identity = f"{language}-{expected}-{index}"
        cases.append({"id": identity, "language": language, "expected": expected})
        records.append({**cases[-1], "prediction": expected, "complete": True,
            "verdict": "benign" if expected == "benign" else "suspicious",
            "risk_level": 0 if expected == "benign" else 2, "deterministic_decision": "allow", "model_digest": "sha256:current"})
    guard = {"prompt_version": "test-current", "sha256": "current-hash"}
    model = {"path": "models/model-manifest.json", "sha256": "manifest-hash",
        "manifest": {"digest": "sha256:current", "tokenizer_sha256": "current-tokenizer", "seed": 42}}
    semantic = {"prompt_version": "test-current", "max_output_tokens": 256, "deadline_seconds": 180}
    return {"records": records, "corpus_file": "semantic_holdout_v2.json", "corpus_sha256": "corpus-hash",
        "mode": "real-local-model", "guard_artifact": guard, "model_artifact": copy.deepcopy(model),
        "semantic_configuration": copy.deepcopy(semantic), "semantic_cache_bypassed": True}, cases, guard, model, semantic


def test_acceptance_recomputes_unknown_and_does_not_reward_fail_closed():
    report, cases, guard, model, semantic = quality_fixture()
    assert acceptance.quality_check(report, cases, "corpus-hash", guard, model, semantic)[0]
    report["records"][0].update(complete=False, verdict="unknown", prediction="attack")
    passed, details = acceptance.quality_check(report, cases, "corpus-hash", guard, model, semantic)
    assert not passed
    assert details["unknown"] == 1
    assert details["languages"]["EN"]["tp"] == 9
    assert details["languages"]["EN"]["recall"] == .9
    assert not details["prediction_contract_valid"]


def test_acceptance_rejects_previous_guard_or_holdout_and_duplicate_case():
    report, cases, guard, model, semantic = quality_fixture()
    for field, value in (("corpus_file", "semantic_holdout.json"), ("corpus_sha256", "other"), ("guard_artifact", {"sha256": "old"})):
        altered = copy.deepcopy(report)
        altered[field] = value
        assert not acceptance.quality_check(altered, cases, "corpus-hash", guard, model, semantic)[0]
    report["records"][-1] = report["records"][0]
    assert not acceptance.quality_check(report, cases, "corpus-hash", guard, model, semantic)[0]


def test_acceptance_enforces_per_language_false_positive_limit():
    report, cases, guard, model, semantic = quality_fixture()
    benign = next(row for row in report["records"] if row["expected"] == "benign")
    benign.update(prediction="attack", risk_level=2, verdict="suspicious")
    passed, details = acceptance.quality_check(report, cases, "corpus-hash", guard, model, semantic)
    assert not passed
    assert details["languages"]["EN"]["false_positive_rate"] == .1


@pytest.mark.parametrize("changed", ["model_digest", "tokenizer", "manifest_envelope", "semantic_configuration", "record_digest", "missing_record_digest", "cached_evaluation"])
def test_acceptance_rejects_stale_model_tokenizer_settings_or_cache(changed):
    report, cases, guard, model, semantic = quality_fixture()
    if changed == "model_digest":
        report["model_artifact"]["manifest"]["digest"] = "sha256:previous"
    elif changed == "tokenizer":
        report["model_artifact"]["manifest"]["tokenizer_sha256"] = "previous-tokenizer"
    elif changed == "manifest_envelope":
        report["model_artifact"]["sha256"] = "previous-envelope"
    elif changed == "semantic_configuration":
        report["semantic_configuration"]["max_output_tokens"] = 512
    elif changed in ("record_digest", "missing_record_digest"):
        report["records"][0]["model_digest"] = "sha256:previous" if changed == "record_digest" else None
    else:
        report["semantic_cache_bypassed"] = False
    assert not acceptance.quality_check(report, cases, "corpus-hash", guard, model, semantic)[0]


def benchmark_fixture():
    rows = []
    for cache, mode, size, clients, temperature in product((False, True), ("baseline", "deterministic", "full"), (1, 4, 16, 64), (1, 10, 50), ("cold", "warm")):
        rows.append({"control_cache_enabled": cache, "semantic_cache_enabled": cache, "mode": mode, "input_kib": size, "concurrency": clients,
            "temperature": temperature, "samples": [{"status": "completed", "latency_ms": 10 if mode == "baseline" else 40} for _ in range(clients)],
            "requests": clients, "completed": clients, "model_preparation": [{"confirmed": True, "mode": "cold_model_load"}],
            "generation": 1, "gateways": {name: {"cpu_seconds": .2, "rss_before_bytes": 100, "rss_after_bytes": 120} for name in ("a", "b")},
            "runner_cpu_seconds": .1, "runner_rss_bytes": 100,
            "accounting": {"total_tokens": 100 if mode == "full" else 0, "guard_slot_seconds": 1 if mode == "full" else 0}})
    return {"suite": "benchmark", "measurement_method_version": 2, "status": "completed", "rows": rows, "repetitions": 1, "original_policy_restored": True}


def test_long_context_evidence_requires_complete_current_detection_and_zero_effects():
    _, _, guard, model, semantic = quality_fixture()
    names = ("exact_64kib_input", "deterministic_controls_allow", "normal_broker_blocks",
        "complete_actual_semantic_detection", "all_source_windows_scanned", "attack_detected_away_from_edges",
        "separate_goal_action_review", "zero_business_effects", "known_positive_usage_settled", "generation_unchanged")
    report = {"passed": True, "suite": "semantic-long-attack", "mode": "real-local-model", "payload_bytes": 65536,
        "checks": dict.fromkeys(names, True), "guard_artifact": guard, "model_artifact": model, "semantic_configuration": semantic,
        "input_estimate": {"content_windows": 7, "windows": 8},
        "actual_scan": {"complete": True, "verdict": "suspicious", "risk_level": 2, "model_digest": "sha256:current",
            "windows": [{} for _ in range(7)], "goal_action": {}, "inspection_calls": 8},
        "operation": {"status": "blocked", "rule_ids": ["semantic.risk"]},
        "reservations": [{"status": "settled", "usage": {"total_tokens": 123}}]}
    assert acceptance.long_context_check(report, guard, model, semantic)[0]
    for section, key, value in (("checks", "zero_business_effects", False), ("actual_scan", "complete", False),
            ("actual_scan", "model_digest", "sha256:old"), ("actual_scan", "goal_action", None),
            ("operation", "status", "completed")):
        altered = copy.deepcopy(report)
        altered[section][key] = value
        assert not acceptance.long_context_check(altered, guard, model, semantic)[0]
    report["reservations"][0]["status"] = "usage_unknown"
    assert not acceptance.long_context_check(report, guard, model, semantic)[0]


def test_acceptance_requires_every_measured_matrix_cell_and_cold_confirmation():
    report = benchmark_fixture()
    passed, details = acceptance.benchmark_check(report)
    assert passed and details["targets_met"] and details["requests"] == 2928
    assert not acceptance.benchmark_check({**report, "measurement_method_version": 1})[0]
    shortened = copy.deepcopy(report)
    shortened["rows"].pop()
    assert not acceptance.benchmark_check(shortened)[0]
    full = next(row for row in report["rows"] if row["mode"] == "full" and row["temperature"] == "cold")
    full["model_preparation"] = [{"status": "unsupported"}]
    assert not acceptance.benchmark_check(report)[0]


def test_acceptance_distinguishes_completed_matrix_from_missed_performance_target():
    report = benchmark_fixture()
    row = next(row for row in report["rows"] if row["mode"] == "deterministic" and row["control_cache_enabled"] and row["input_kib"] == 4 and row["concurrency"] == 10)
    row["samples"][0]["latency_ms"] = 150
    passed, details = acceptance.benchmark_check(report)
    assert passed and not details["targets_met"]
    row["samples"][0]["status"] = "transport_error"
    row["completed"] -= 1
    assert not acceptance.benchmark_check(report)[0]


def test_benchmark_source_binding_rejects_changed_backend_worker_and_measurement_code(tmp_path):
    names = ("backend/actiongate/broker.py", "scripts/benchmark.py", "scripts/acceptance_report.py", "scripts/runtime_hardware.py", "scripts/runtime_evidence.py", "runtime/worker.py", "runtime/requirements.lock", "models/model-manifest.json")
    report = {"measurement_source_stable": True, "measurement_source_files": {}}
    for name in names:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("measured source")
        report["measurement_source_files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert acceptance.benchmark_source_check(report, tmp_path)[0]
    for name in names:
        path = tmp_path/name
        path.write_text("later unmeasured change")
        passed, details = acceptance.benchmark_source_check(report, tmp_path)
        assert not passed and name in details["stale_files"]
        path.write_text("measured source")
    report["measurement_source_stable"] = False
    assert not acceptance.benchmark_source_check(report, tmp_path)[0]


def test_evidence_producer_binding_rejects_changed_missing_and_unstable_sources(tmp_path):
    script = tmp_path/"scripts/verify_clients.py"
    script.parent.mkdir()
    script.write_text("executed producer")
    name = "scripts/verify_clients.py"
    report = {"producer_sources": {name: hashlib.sha256(script.read_bytes()).hexdigest()}, "producer_source_stable": True}
    assert acceptance.producer_source_check(report, tmp_path, {name})[0]
    assert not acceptance.producer_source_check(report, tmp_path, {name, "scripts/missing.py"})[0]
    report["producer_source_stable"] = False
    assert not acceptance.producer_source_check(report, tmp_path, {name})[0]
    report["producer_source_stable"] = True
    script.write_text("changed after measurement")
    assert not acceptance.producer_source_check(report, tmp_path, {name})[0]


def physical_fixture():
    model = {"sha256": "manifest-hash", "manifest": {"model": "approved", "digest": "sha256:abc", "runtime": "ollama:1.0.0", "tokenizer_sha256": "tokenizer-hash"}}
    workers = [{"role": role, "name": "reference-"+role+"-worker-1", "container_id": role+"-container", "image_id": "sha256:image",
        "ready": {**model["manifest"], "model_manifest_sha256": model["sha256"]}, "device_requests": [],
        "resident_models": [{"digest": "abc", "size_vram": 0}]} for role in ("guard", "business")]
    report = {"passed": True, "project": "reference", "profile": "CPU", "workers": workers}
    deployed = {row["role"]+"-worker": {"container_id": row["container_id"], "image_id": row["image_id"]} for row in workers}
    return report, model, deployed


@pytest.mark.parametrize("change", ["gpu_resident", "gpu_access", "different_container", "unloaded_guard", "missing_placement", "old_model"])
def test_cpu_proof_rejects_gpu_fallback_missing_residency_and_other_deployment(change):
    report, model, deployed = physical_fixture()
    assert acceptance.physical_runtime_check(report, "CPU", model, deployed_workers=deployed)[0]
    worker = report["workers"][0]
    if change == "gpu_resident":
        worker["resident_models"][0]["size_vram"] = 1000
    elif change == "gpu_access":
        worker["device_requests"] = [{"Capabilities": [["gpu"]]}]
    elif change == "different_container":
        worker["container_id"] = "different"
    elif change == "unloaded_guard":
        worker["resident_models"] = []
    elif change == "missing_placement":
        worker["resident_models"][0].pop("size_vram")
    else:
        worker["ready"]["model_manifest_sha256"] = "old-manifest"
    assert not acceptance.physical_runtime_check(report, "CPU", model, deployed_workers=deployed)[0]


def test_clean_cpu_may_omit_business_inference_but_gpu_requires_actual_both_residencies():
    report, model, _ = physical_fixture()
    report["workers"][1]["resident_models"] = []
    assert acceptance.physical_runtime_check(report, "CPU", model, require_business=False)[0]
    assert not acceptance.physical_runtime_check(report, "CPU", model)[0]
    report["profile"] = "GPU"
    for worker in report["workers"]:
        worker["device_requests"] = [{"Capabilities": [["gpu"]]}]
        worker["resident_models"] = [{"digest": "abc", "size_vram": 1000}]
    assert acceptance.physical_runtime_check(report, "GPU", model)[0]
    report["workers"][1]["resident_models"][0]["size_vram"] = 0
    assert not acceptance.physical_runtime_check(report, "GPU", model)[0]


def test_acceptance_requires_real_semantic_work_and_legal_single_client_completion():
    report = benchmark_fixture()
    row = next(row for row in report["rows"] if row["mode"] == "full" and row["concurrency"] == 1)
    row["samples"][0]["status"] = "blocked"
    row["completed"] = 0
    passed, details = acceptance.benchmark_check(report)
    assert not passed and not details["full_mode_single_client_cohorts_completed"]
    report = benchmark_fixture()
    for row in report["rows"]:
        row["accounting"].update(total_tokens=0, guard_slot_seconds=0)
    passed, details = acceptance.benchmark_check(report)
    assert not passed and not details["real_uncached_guard_usage_present"]


def test_deployed_source_proof_rejects_later_ui_or_recipe_changes(tmp_path):
    files = {"host_build_inputs": ["Dockerfile", "runtime/Dockerfile", "runtime/requirements.lock", "compose.yaml",
        "deploy/compose.gpu.yaml", "scripts/image_manifest.mjs", "pyproject.toml", "uv.lock"],
        "host_backend": ["backend/actiongate/app.py"], "host_ui_inputs": ["ui/src/App.tsx", "ui/package.json"]}
    report = {"passed": True, "checks": {"replicas_same_generation_and_artifacts": True}}
    for section, names in files.items():
        report[section] = {}
        for name in names:
            path = tmp_path/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source at build")
            report[section][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for role in ("gateway-a", "gateway-b"):
        report["checks"].update({role+suffix: True for suffix in ("_backend_matches", "_ui_build_inputs_match", "_ui_outputs_match", "_recipes_match")})
    for role in ("guard-worker", "business-worker"):
        report["checks"].update({role+suffix: True for suffix in ("_source_and_recipe_match", "_matches_signed_artifact")})
    (tmp_path/"runtime/worker.py").write_text("source at build")
    report["workers"] = {role: {"files": {remote: hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()
        for remote, name in {"/app/worker.py": "runtime/worker.py", "/app/build-inputs/runtime/Dockerfile": "runtime/Dockerfile",
            "/app/build-inputs/runtime/requirements.lock": "runtime/requirements.lock"}.items()}}
        for role in ("guard-worker", "business-worker")}
    assert acceptance.deployed_source_check(report, tmp_path)[0]
    (tmp_path/"ui/src/App.tsx").write_text("new source not in deployed image")
    passed, detail = acceptance.deployed_source_check(report, tmp_path)
    assert not passed and "ui/src/App.tsx" in detail["stale_files"]
    (tmp_path/"ui/src/App.tsx").write_text("source at build")
    (tmp_path/"ui/src/New.tsx").write_text("new file not in image")
    assert not acceptance.deployed_source_check(report, tmp_path)[0]
    (tmp_path/"ui/src/New.tsx").unlink()
    (tmp_path/"runtime/worker.py").write_text("new worker implementation")
    passed, detail = acceptance.deployed_source_check(report, tmp_path)
    assert not passed and "guard-worker:runtime/worker.py" in detail["stale_files"]



@pytest.fixture
def benchmark_module():
    spec = importlib.util.spec_from_file_location("benchmark_metrics_contract", Path(__file__).resolve().parents[1]/"scripts/benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_benchmark_reads_authenticated_process_metrics_from_both_api_replicas(benchmark_module):
    import httpx
    observed = []
    def respond(request):
        observed.append((request.url.host, request.url.path, request.headers.get("authorization")))
        cpu = "0.0" if request.url.host == "gateway-a" else "3.25"
        return httpx.Response(200, text="# HELP process_cpu_seconds_total Total CPU time\nprocess_cpu_seconds_total "+cpu+"\nprocess_resident_memory_bytes 123456\n")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), headers={"Authorization": "Bearer synthetic-admin"}) as client:
        values = {name: await benchmark_module.metrics(client, "http://"+name+":8000/") for name in ("gateway-a", "gateway-b")}
    assert observed == [(name, "/api/metrics", "Bearer synthetic-admin") for name in ("gateway-a", "gateway-b")]
    assert values["gateway-a"] == {"process_cpu_seconds_total": 0.0, "process_resident_memory_bytes": 123456.0}
    assert values["gateway-b"]["process_cpu_seconds_total"] == 3.25
    measured = benchmark_module.gateway_measurements(values, copy.deepcopy(values))
    assert set(measured) == {"gateway-a", "gateway-b"}
    assert all(value == {"cpu_seconds": 0.0, "rss_before_bytes": 123456.0, "rss_after_bytes": 123456.0} for value in measured.values())


@pytest.mark.parametrize("status", [302, 307, 401, 403, 404, 500, 503])
async def test_benchmark_metrics_reject_http_errors_without_following_redirects(benchmark_module, status):
    import httpx
    observed = []
    def respond(request):
        observed.append(request.url)
        return httpx.Response(status, headers={"Location": "http://unexpected.invalid/"}, text="process_cpu_seconds_total 1\nprocess_resident_memory_bytes 100\n")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await benchmark_module.metrics(client, "http://gateway-a:8000")
    assert len(observed) == 1 and observed[0].host == "gateway-a"


@pytest.mark.parametrize("name", ["process_cpu_seconds_total", "process_resident_memory_bytes"])
@pytest.mark.parametrize("value", [None, "NaN", "+Inf", "-Inf", "-1", "true", "garbage"], ids=["missing", "nan", "positive-infinity", "negative-infinity", "negative", "boolean", "malformed"])
async def test_benchmark_metrics_reject_missing_nonfinite_or_invalid_process_measurements(benchmark_module, name, value):
    import httpx
    values = {"process_cpu_seconds_total": "1", "process_resident_memory_bytes": "100"}
    if value is None:
        values.pop(name)
    else:
        values[name] = value
    payload = "".join(key+" "+entry+"\n" for key, entry in values.items())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=payload))) as client:
        with pytest.raises(ValueError):
            await benchmark_module.metrics(client, "http://gateway-a:8000")


@pytest.mark.parametrize("payload", ["process_cpu_seconds_total 1\nprocess_resident_memory_bytes 0\n",
    "process_cpu_seconds_total 1\nprocess_cpu_seconds_total 2\nprocess_resident_memory_bytes 100\n",
    "process_cpu_seconds_total 1\nprocess_resident_memory_bytes 100\nprocess_resident_memory_bytes 200\n",
    "<html>Not metrics</html>"], ids=["zero-rss", "duplicate-cpu", "duplicate-rss", "html-success-page"])
async def test_benchmark_metrics_reject_ambiguous_or_absent_measurements(benchmark_module, payload):
    import httpx
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=payload))) as client:
        with pytest.raises(ValueError):
            await benchmark_module.metrics(client, "http://gateway-a:8000")


@pytest.mark.parametrize("change", ["one-replica", "different-replica", "counter-reset", "nonfinite-delta"])
def test_benchmark_rejects_incomplete_replica_pair_or_cpu_counter_reset(benchmark_module, change):
    before = {name: {"process_cpu_seconds_total": 2.0, "process_resident_memory_bytes": 100.0} for name in ("a", "b")}
    after = copy.deepcopy(before)
    if change == "one-replica":
        before.pop("b")
        after.pop("b")
    elif change == "different-replica":
        after["c"] = after.pop("b")
    else:
        after["b"]["process_cpu_seconds_total"] = 1.0 if change == "counter-reset" else float("inf")
    with pytest.raises(ValueError):
        benchmark_module.gateway_measurements(before, after)


@pytest.mark.parametrize("owner,key", [(name, field) for name in ("a", "b") for field in ("cpu_seconds", "rss_before_bytes", "rss_after_bytes")]
    + [(None, "runner_cpu_seconds"), (None, "runner_rss_bytes")])
@pytest.mark.parametrize("value", [None, True, False, float("nan"), float("inf"), float("-inf"), -1, "1", "missing"],
    ids=["null", "true", "false", "nan", "positive-infinity", "negative-infinity", "negative", "numeric-string", "missing"])
def test_acceptance_rejects_each_invalid_gateway_and_runner_resource(owner, key, value):
    report = benchmark_fixture()
    row = report["rows"][-1]
    resource = row if owner is None else row["gateways"][owner]
    if value == "missing":
        resource.pop(key)
    else:
        resource[key] = value
    passed, details = acceptance.benchmark_check(report)
    assert not passed and not details["resource_measurements_valid"]


@pytest.mark.parametrize("gateways", [None, {}, [], {"a": {}}, {"a": True, "b": {}}, {"a": {}, "b": {}, "c": {}}],
    ids=["null", "empty", "array", "one-replica", "invalid-replica", "three-replicas"])
def test_acceptance_requires_two_actual_gateway_resource_records(gateways):
    report = benchmark_fixture()
    report["rows"][0]["gateways"] = gateways
    passed, details = acceptance.benchmark_check(report)
    assert not passed and not details["resource_measurements_valid"]


def test_acceptance_accepts_zero_cpu_but_rejects_zero_rss_for_either_replica_or_runner():
    report = benchmark_fixture()
    row = report["rows"][0]
    row["runner_cpu_seconds"] = 0
    for value in row["gateways"].values():
        value["cpu_seconds"] = 0
    assert acceptance.benchmark_check(report)[0]
    for owner, key in [(name, field) for name in ("a", "b") for field in ("rss_before_bytes", "rss_after_bytes")] + [(None, "runner_rss_bytes")]:
        resource = row if owner is None else row["gateways"][owner]
        original = resource[key]
        resource[key] = 0
        assert not acceptance.benchmark_check(report)[0]
        resource[key] = original
