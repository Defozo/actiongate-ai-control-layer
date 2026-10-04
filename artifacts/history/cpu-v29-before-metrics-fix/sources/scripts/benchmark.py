"""Measured ActionGate workload matrix. Run only after independent holdout.

Trusted test-runner task: temporarily publishes protection modes, creates fresh
synthetic principals/runs, and always restores the previous policy. No live API.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import hashlib
from itertools import product
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
import uuid

import httpx
import psutil
import yaml
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TENANT = "synthetic-benchmark-"+str(uuid.uuid4())


def measurement_sources():
    paths = set((ROOT/"backend").rglob("*.py"))
    paths.update(ROOT/name for name in ("scripts/benchmark.py", "scripts/acceptance_report.py", "scripts/runtime_hardware.py", "scripts/runtime_evidence.py", "runtime/worker.py", "runtime/requirements.lock", "models/model-manifest.json"))
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def percentile(values, percentage):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered)*percentage/100)-1)], 3)


def matched_comparisons(rows):
    baselines = {(row["control_cache_enabled"], row["input_kib"], row["concurrency"], row["temperature"]): row
                 for row in rows if row["mode"] == "baseline"}
    result = []
    for row in rows:
        if row["mode"] == "baseline":
            continue
        baseline = baselines.get((row["control_cache_enabled"], row["input_kib"], row["concurrency"], row["temperature"]))
        if not baseline:
            continue
        a, b = row["completed_p95_ms"], baseline["completed_p95_ms"]
        result.append({"mode": row["mode"], "control_cache_enabled": row["control_cache_enabled"],
            "input_kib": row["input_kib"], "concurrency": row["concurrency"],
            "temperature": row["temperature"], "baseline_completions": baseline["completed"],
            "protected_completions": row["completed"],
            "completed_p95_delta_ms": round(a-b, 3) if a is not None and b is not None else None,
            "completed_p95_ratio": round(a/b, 4) if a is not None and b else None,
            "limitation": "Successful subsets may differ under rejection; all synthetic baseline admission precedes the cohort and all result bookkeeping follows it."})
    return result


def cache_comparisons(rows):
    disabled = {(row["mode"], row["input_kib"], row["concurrency"], row["temperature"]): row
                for row in rows if not row["control_cache_enabled"]}
    result = []
    for row in rows:
        if not row["control_cache_enabled"]:
            continue
        previous = disabled.get((row["mode"], row["input_kib"], row["concurrency"], row["temperature"]))
        if not previous:
            continue
        a, b = row["completed_p95_ms"], previous["completed_p95_ms"]
        result.append({"mode": row["mode"], "input_kib": row["input_kib"], "concurrency": row["concurrency"],
            "temperature": row["temperature"], "cache_off_completions": previous["completed"],
            "cache_on_completions": row["completed"],
            "completed_p95_on_minus_off_ms": round(a-b, 3) if a is not None and b is not None else None,
            "limitation": "Baseline bypasses the control cache; timing differences there are not a cache benefit. Protected successful subsets can differ under rejection."})
    return result


def sample_text(size):
    sentence = "The supplier offers maintenance services with a five year warranty. Delivery capacity is stable. "
    # Exact ASCII byte count; suffix is data, never a production-engine case ID.
    return (sentence*((size+len(sentence)-1)//len(sentence)))[:size]


def reservation_footprint(configuration, repetitions):
    """Planning bounds, not measured use or a reason to bypass budget rejection."""
    from actiongate.semantic import SemanticGuard
    guard = SemanticGuard()
    semantic = configuration["semantic"]
    output = guard.estimate(512, semantic)
    by_size = []
    for size in (1, 4, 16, 64):
        payload = json.dumps({"content": sample_text(size*1024)}, separators=(",", ":"))
        estimate = guard.estimate(payload, semantic)
        by_size.append({"input_kib": size, "input_windows": estimate.get("content_windows", estimate["windows"]),
            "input_inspection_calls": estimate["windows"], "goal_action_checks": estimate.get("goal_action_checks", 0),
            "complete_possible": estimate["complete_possible"] and output["complete_possible"],
            "max_tokens_per_completed_request": estimate["max_tokens"]+output["max_tokens"],
            "max_slot_seconds_per_completed_request": estimate["slot_seconds"]+output["slot_seconds"]})
    requests_per_size = 2*(1+10+50)*repetitions
    return {"kind": "conservative planning bounds from the real guard estimator, including stop grace per window; measured use is reported separately", "by_size": by_size,
        "full_mode_requests_per_cache_tenant": requests_per_size*4,
        "full_mode_requests_all_cache_states": requests_per_size*8,
        "per_tenant_sum_token_reservations_if_every_request_completes": sum(row["max_tokens_per_completed_request"] for row in by_size)*requests_per_size,
        "per_tenant_sum_slot_seconds_if_every_window_uses_full_deadline_and_stop_grace": sum(row["max_slot_seconds_per_completed_request"] for row in by_size)*requests_per_size,
        "tenant_daily_token_limit": configuration["budgets"].get("tenant_daily_tokens", configuration["budgets"]["run_total_tokens"]*100),
        "tenant_daily_slot_seconds_limit": configuration["local_resources"]["run_slot_seconds"]*100,
        "limits_are_preserved": True}


def make_actor(role="analyst", tenant=TENANT):
    from actiongate.db import Principal, transaction, uid
    from actiongate.security import issue_token
    principal = "benchmark:"+uid()
    with transaction() as db:
        db.add(Principal(id=principal, tenant=tenant, role=role))
    return principal, issue_token(principal, tenant, role)


def gather_accounting(operation_ids):
    from actiongate.db import Operation, Reservation, transaction
    result = {"guard_slot_seconds": 0.0, "guard_cpu_seconds": 0.0, "total_tokens": 0,
              "actual_usd_micros": 0, "unknown_reservations": 0,
              "phase_totals_ms": {}, "phase_p95_ms": {}, "semantic_cache_hits": 0,
              "semantic_input_cache_hits": 0, "semantic_output_cache_hits": 0}
    if not operation_ids:
        return result
    with transaction() as db:
        phases = {}
        for operation in db.scalars(select(Operation).where(Operation.id.in_(operation_ids))):
            metadata = operation.metadata_ or {}
            input_hit = int((metadata.get("semantic") or {}).get("cache_hit") is True)
            output_hit = int((metadata.get("semantic_output") or {}).get("cache_hit") is True)
            result["semantic_input_cache_hits"] += input_hit
            result["semantic_output_cache_hits"] += output_hit
            result["semantic_cache_hits"] += input_hit+output_hit
            for name, value in (operation.metadata_ or {}).get("phase_timings_ms", {}).items():
                phases.setdefault(name, []).append(value)
        result["phase_totals_ms"] = {name: round(sum(values), 3) for name, values in phases.items()}
        result["phase_p95_ms"] = {name: percentile(values, 95) for name, values in phases.items()}
        for reservation in db.scalars(select(Reservation).where(Reservation.operation_id.in_(operation_ids))):
            usage = reservation.usage or {}
            if reservation.status == "usage_unknown":
                result["unknown_reservations"] += 1
            result["total_tokens"] += usage.get("total_tokens", 0)
            result["actual_usd_micros"] += usage.get("usd_micros", 0)
            if reservation.kind.startswith("guard."):
                result["guard_slot_seconds"] += usage.get("inference_slot_seconds", 0)
                result["guard_cpu_seconds"] += usage.get("cpu_seconds", 0)
    return result


def baseline_admission(run_id, actor, text, snapshot):
    """Prepare trusted measurement admission before any cohort timing starts.

    Trusted benchmark admission is explicit in the durable operation metadata.
    It is not an API or capability available to isolated agents.
    """
    from actiongate.db import Operation, Run, RunContext, transaction, uid
    from actiongate.security import digest, encrypt
    operation_id = uid()
    payload = {"content": text}
    with transaction() as db:
        run = db.get(Run, run_id)
        ctx = db.get(RunContext, run_id)
        ctx.label = 2
        db.add(Operation(id=operation_id, tenant=run.tenant, actor=actor, run_id=run_id, root_id=run_id,
            tool="reports.save", idempotency_key=operation_id, payload_hash=digest(payload), input_hash=digest(payload),
            encrypted_payload=encrypt(payload), status="dispatched", decision="allow", stage="benchmark_admission",
            policy_generation=snapshot["generation"], revocation_epoch=snapshot["revocation_epoch"],
            metadata_={"benchmark": True, "mode": "baseline_without_broker", "owner_fence": ctx.fence}))
    return {"operation_id": operation_id, "run": run}


async def baseline_dispatch(admission, text, snapshot):
    """Time only the real typed connector, with no concurrent setup bookkeeping."""
    from actiongate import broker
    start = time.perf_counter()
    result, usage = await broker.dispatch("reports.save", {"content": text}, admission["run"], admission["operation_id"], snapshot)
    elapsed = (time.perf_counter()-start)*1000
    return {"operation_id": admission["operation_id"], "status": "completed", "latency_ms": elapsed, "http_status": None,
        "_baseline_result": result}


def finalize_baseline(samples):
    """Persist synthetic bookkeeping after every timed connector call returns."""
    from actiongate.db import Operation, transaction
    from actiongate.security import encrypt
    with transaction() as db:
        operations = {operation.id: operation for operation in db.scalars(select(Operation).where(Operation.id.in_([sample["operation_id"] for sample in samples])))}
        for sample in samples:
            operation = operations[sample["operation_id"]]
            if sample["status"] == "completed":
                operation.status, operation.settlement_status = "completed", "settled"
                operation.encrypted_result = encrypt(sample.pop("_baseline_result"))
            else:
                operation.status, operation.settlement_status = "outcome_unknown", "usage_unknown"
                operation.reason = "Trusted benchmark connector outcome could not be confirmed"
            operation.metadata_ = {**operation.metadata_, "latency_ms": sample["latency_ms"],
                "phase_timings_ms": {"upstream_ms": sample["latency_ms"]}}


async def metrics(client, url):
    try:
        response = await client.get(url+"/metrics")
        values = {}
        for line in response.text.splitlines():
            if line.startswith(("process_cpu_seconds_total ", "process_resident_memory_bytes ")):
                name, value = line.split()[:2]
                values[name] = float(value)
        return values
    except Exception:
        return {}


async def cold_model(generation):
    from actiongate.runtime import WorkerClient
    worker = WorkerClient("guard")
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(worker.url+"/benchmark/unload", headers={"Authorization": "Bearer "+worker.token})
    if response.status_code == 404:
        raise RuntimeError("Worker lacks the verified model-unload operation required by the cold matrix")
    response.raise_for_status()
    result = response.json()
    if result.get("confirmed") is not True or result.get("mode") != "cold_model_load" or generation not in result.get("prepared_generations", []):
        raise RuntimeError("Cold-model preparation did not confirm model unload")
    return {"status": "confirmed", **result}


async def observe_worker(stop, observations):
    from actiongate.runtime import WorkerClient
    seen = None
    while True:
        try:
            status = await WorkerClient("guard").status()
            item = {key: status.get(key) for key in ("active", "waiting", "reserved_output_jobs", "resources")}
            records = status.get("recent_inference", [])
            if seen is None:
                seen = {record["request_id"] for record in records}
                item["new_inference"] = []
            else:
                item["new_inference"] = [record for record in records if record["request_id"] not in seen]
                seen.update(record["request_id"] for record in records)
            observations.append(item)
        except Exception:
            pass
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=.5)
        except TimeoutError:
            pass


async def main_async(args):
    from actiongate import policies
    from actiongate.security import issue_token
    admin_actor, admin_token = make_actor("admin")
    headers = {"Authorization": "Bearer "+admin_token,
               "Origin": os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8080")}
    urls = [args.url.rstrip("/"), os.getenv("SECONDARY_BASE_URL", args.url).rstrip("/")]
    tenants = {False: TENANT+"-cache-off", True: TENANT+"-cache-on"}
    focused = args.focused_deterministic
    modes = ("baseline", "deterministic") if focused else ("baseline", "deterministic", "full")
    sizes = (4,) if focused else (1, 4, 16, 64)
    concurrencies = (10,) if focused else (1, 10, 50)
    suite = "benchmark-probe" if focused else "benchmark"
    report = {"suite": suite, "status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
        "measurement_source_files": measurement_sources(),
        "measurement_method_version": 2,
        "upstream": "the same typed reports.save database connector", "namespace": TENANT,
        "environment_label": args.environment_label,
        "repetitions": args.repetitions, "planned_rows": 8 if focused else 144,
        "planned_requests": (80 if focused else 2928)*args.repetitions, "rows": [],
        "measurement_scope": "Preliminary 4 KiB / 10-client diagnostic; does not satisfy the required full matrix" if focused else "Full required matrix",
        "workload_tenants": {"cache_off": tenants[False], "cache_on": tenants[True]},
        "cache": {"control_plane": "performance.control_cache_enabled=false/true; bounded cache of compiled signed control planes, not decisions",
                  "semantic_judgments": "performance.semantic_cache_enabled=false/true toggled together; exact content, tenant, purpose/effect, restrictions, full signed generation; complete judgments only, 120-second TTL and bounded per-process memory; hits have zero new inference use",
                  "model_kv": "provider-managed; explicit per-request cache on/off unsupported"},
        "measurement_notes": ["Baseline measures direct typed connector execution. All synthetic admission is prepared before the cohort gate; all result bookkeeping occurs after every timed dispatch. Neither competes with other timed baseline requests.",
            "Gateway timings measure HTTP actions including mandatory persistence and publication; principal/run setup is outside the timer.",
            "Fresh root and principal per request prevent global rate-limit exhaustion from masquerading as model throughput.",
            "Each control-cache value uses a distinct fresh synthetic tenant with the same unmodified budgets. Baseline bypasses this cache and supplies a separately measured reference for each setting.",
            "Workflow setup may populate the compiled control cache before timed actions. Cache on/off compares ordinary reusable controls; model cold/warm does not assert a cold compiled-control cache.",
            "Percentiles are empirical nearest-rank observations. Small cold cohorts do not establish population tail latency.",
            "Database page-cache and host filesystem cache are not reset. Cold means each concurrent cohort starts after confirmed model unload; later calls within that cohort share the subsequently loaded model.",
            "Warm means the next cohort after the cold cohort; if all previous requests were rejected before inference, model warmth is not asserted.",
            "Gateway RSS/CPU are read from its process metrics. Guard CPU/slot use comes from settled reservations. Runner RSS is separate."],
        "hardware": {"platform": platform.platform(), "logical_cpu_count": os.cpu_count(),
                     "runner_memory_limit_bytes": Path('/sys/fs/cgroup/memory.max').read_text().strip() if Path('/sys/fs/cgroup/memory.max').exists() else None}}
    hardware_file = args.hardware_report if args.hardware_report.is_absolute() else ROOT/args.hardware_report
    if not hardware_file.is_file():
        raise RuntimeError("A measured hardware report for this target environment is required")
    report["hardware"]["deployment"] = json.loads(hardware_file.read_text())
    report["hardware"]["source"] = args.hardware_report.as_posix()
    report["hardware"]["source_sha256"] = hashlib.sha256(hardware_file.read_bytes()).hexdigest()
    if "gpu" in args.environment_label.lower() and str(report["hardware"]["deployment"].get("profile", report["hardware"]["deployment"].get("runtime_profile", ""))).lower() != "gpu":
        raise RuntimeError("GPU benchmark label requires its own GPU hardware report, not the reference CPU manifest")
    target = ROOT / ("artifacts/"+suite+".json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    original = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(3600, connect=10), limits=httpx.Limits(max_connections=100), headers=headers) as client:
        async def checked(method, path, **kwargs):
            response = await client.request(method, urls[0]+path,
                headers={"Authorization": "Bearer "+issue_token(admin_actor, TENANT, "admin")}, **kwargs)
            response.raise_for_status()
            return response.json()
        try:
            original = await checked("GET", "/api/policies")
            from actiongate.runtime import WorkerClient
            from actiongate.semantic import guard_artifact
            report["guard_artifact"] = guard_artifact()
            initial_snapshot = policies.snapshot()
            initial_signed = policies.verify_stored_snapshot(initial_snapshot)
            report["model_artifacts"] = initial_signed["model_artifacts"]
            report["semantic_configuration"] = initial_snapshot["configuration"]["semantic"]
            report["initial_generation"] = initial_snapshot["generation"]
            report["initial_workers"] = {role: await WorkerClient(role).ready() for role in ("guard", "business")}
            from acceptance_report import physical_runtime_check, producer_source_check
            hardware = report["hardware"]["deployment"]
            expected_profile = "GPU" if "gpu" in args.environment_label.lower() else "CPU"
            physical, _ = physical_runtime_check(hardware, expected_profile, initial_signed["model_artifacts"]["local-guard"])
            sources_valid, _ = producer_source_check(hardware, ROOT, {"scripts/runtime_hardware.py", "scripts/runtime_evidence.py"})
            if not physical or not sources_valid or any(hardware["workers"][index]["ready"].get("epoch") != report["initial_workers"][hardware["workers"][index]["role"]].get("epoch") for index in range(2)):
                raise RuntimeError("Benchmark requires current measured physical worker placement and unchanged worker epochs")
            from actiongate.controls.model_artifacts import verify_worker_artifacts
            await verify_worker_artifacts(initial_signed, report["initial_workers"])
            if any(original["generation"] not in worker.get("prepared_generations", []) for worker in report["initial_workers"].values()):
                raise RuntimeError("Target workers have not prepared the target gateway generation")
            report["reservation_footprint"] = reservation_footprint(original["configuration"], args.repetitions)
            from actiongate.ledger import balances
            report["opening_tenant_balances"] = {tenant: balances(tenant) for tenant in tenants.values()}
            for cache_enabled, mode in product((False, True), modes):
                tenant = tenants[cache_enabled]
                config = yaml.safe_load(original["yaml"])
                config["controls"]["semantic"]["enabled"] = mode == "full"
                config.setdefault("performance", {})["control_cache_enabled"] = cache_enabled
                config["performance"]["semantic_cache_enabled"] = cache_enabled
                publication = await checked("POST", "/api/policies/activate", json={"yaml": yaml.safe_dump(config, sort_keys=False)})
                snapshot = policies.snapshot()
                if snapshot["generation"] != publication["generation"] or any(snapshot["configuration"]["performance"][key] is not cache_enabled for key in ("control_cache_enabled", "semantic_cache_enabled")):
                    raise RuntimeError("Active generation does not match the intended control-cache measurement")
                for size_kib in sizes:
                    text = sample_text(size_kib*1024)
                    for concurrency in concurrencies:
                        for temperature in ("cold", "warm"):
                            preparations = []
                            client.headers["Authorization"] = "Bearer "+issue_token(admin_actor, TENANT, "admin")
                            before = {url: await metrics(client, url) for url in set(urls)}
                            cpu_before = psutil.Process().cpu_times()
                            samples = []
                            baseline_bookkeeping_seconds = 0.0
                            observed_worker = []
                            stop_observer = asyncio.Event()
                            observer = asyncio.create_task(observe_worker(stop_observer, observed_worker))
                            began = time.perf_counter()
                            for repeat in range(args.repetitions):
                                preparations.append(await cold_model(snapshot["generation"]) if mode == "full" and temperature == "cold" else {
                                    "status": "not_applicable" if mode != "full" else "warm_requested_after_preceding_cohort"})
                                prepared = []
                                admissions = {}
                                for index in range(concurrency):
                                    actor, token = await asyncio.to_thread(make_actor, tenant=tenant)
                                    response = await client.post(urls[index % len(urls)]+"/runs",
                                        headers={"Authorization": "Bearer "+token}, json={"document_ids": []})
                                    response.raise_for_status()
                                    run_id = response.json()["id"]
                                    if mode == "baseline":
                                        admissions[run_id] = await asyncio.to_thread(baseline_admission, run_id, actor, text, snapshot)
                                    prepared.append((run_id, actor, token, index))
                                gate = asyncio.Event()
                                async def execute(item):
                                    run_id, actor, token, index = item
                                    await gate.wait()
                                    started = time.perf_counter()
                                    try:
                                        if mode == "baseline":
                                            return await asyncio.to_thread(lambda: asyncio.run(baseline_dispatch(admissions[run_id], text, snapshot)))
                                        response = await client.post(urls[index % len(urls)]+"/api/actions",
                                            headers={"Authorization": "Bearer "+token}, json={"run_id": run_id,
                                                "tool": "reports.save", "arguments": {"content": text},
                                                "idempotency_key": f"bench-{run_id}-{repeat}"})
                                        value = response.json()
                                        return {"operation_id": value.get("id"), "status": value.get("status", "http_rejected"),
                                            "http_status": response.status_code, "latency_ms": (time.perf_counter()-started)*1000,
                                            "rules": value.get("rule_ids", []), "generation": value.get("policy_generation")}
                                    except Exception as exc:
                                        return {"status": "transport_error", "error": type(exc).__name__,
                                                **({"operation_id": admissions[run_id]["operation_id"]} if mode == "baseline" else {}),
                                                "latency_ms": (time.perf_counter()-started)*1000}
                                tasks = [asyncio.create_task(execute(item)) for item in prepared]
                                cohort_start = time.perf_counter()
                                gate.set()
                                results = await asyncio.gather(*tasks)
                                cohort_seconds = time.perf_counter()-cohort_start
                                if mode == "baseline":
                                    bookkeeping_start = time.perf_counter()
                                    await asyncio.to_thread(finalize_baseline, results)
                                    baseline_bookkeeping_seconds += time.perf_counter()-bookkeeping_start
                                for item in results:
                                    item["cohort_seconds"] = cohort_seconds
                                samples.extend(results)
                            stop_observer.set()
                            await observer
                            after = {url: await metrics(client, url) for url in set(urls)}
                            cpu_after = psutil.Process().cpu_times()
                            accepted = [s for s in samples if s["status"] == "completed"]
                            latencies = [sample["latency_ms"] for sample in samples]
                            measured_seconds = sum(max(s["cohort_seconds"] for s in samples[r*concurrency:(r+1)*concurrency]) for r in range(args.repetitions))
                            accounting = await asyncio.to_thread(gather_accounting, [s["operation_id"] for s in samples if s.get("operation_id")])
                            row = {"mode": mode, "control_cache_enabled": cache_enabled, "semantic_cache_enabled": cache_enabled, "tenant": tenant,
                                "input_kib": size_kib, "input_bytes": len(text.encode()),
                                "concurrency": concurrency, "temperature": temperature, "model_preparation": preparations,
                                "generation": publication["generation"], "requests": len(samples), "completed": len(accepted),
                                "rejected_or_failed": len(samples)-len(accepted), "p50_ms": percentile(latencies, 50),
                                "p95_ms": percentile(latencies, 95), "p99_ms": percentile(latencies, 99),
                                "completed_p95_ms": percentile([s["latency_ms"] for s in accepted], 95),
                                "measured_cohort_seconds": measured_seconds, "wall_seconds_including_setup": time.perf_counter()-began,
                                "baseline_bookkeeping_seconds_excluded_from_cohort": baseline_bookkeeping_seconds,
                                "throughput_requests_per_second": len(samples)/measured_seconds,
                                "throughput_completed_per_second": len(accepted)/measured_seconds,
                                "time_to_content": "direct upstream result latency" if mode == "baseline" else "fully inspected result; equal to completed response latency",
                                "gateways": {url: {"rss_before_bytes": before[url].get("process_resident_memory_bytes"),
                                    "rss_after_bytes": after[url].get("process_resident_memory_bytes"),
                                    "cpu_seconds": after[url]["process_cpu_seconds_total"]-before[url]["process_cpu_seconds_total"]
                                        if "process_cpu_seconds_total" in after[url] and "process_cpu_seconds_total" in before[url] else None} for url in before},
                                "guard_queue_observations": {"samples": len(observed_worker),
                                    "max_waiting": max((item["waiting"] for item in observed_worker if isinstance(item.get("waiting"), int)), default=None),
                                    "max_reserved_output_jobs": max((item["reserved_output_jobs"] for item in observed_worker if isinstance(item.get("reserved_output_jobs"), int)), default=None)},
                                "guard_resources": {"scope": "sampled worker resource endpoint; absent measurements remain null",
                                    "process_rss_scope": "worker and Ollama descendants; shared pages may be counted more than once",
                                    "cgroup_peak_scope": "worker cgroup lifetime, not this row alone",
                                    **{"observed_max_"+name: max(((item.get("resources") or {}).get(name) for item in observed_worker
                                        if isinstance((item.get("resources") or {}).get(name), (int, float))), default=None)
                                       for name in ("process_rss_bytes", "cgroup_memory_current_bytes", "cgroup_memory_limit_bytes", "cgroup_memory_peak_bytes")}},
                                "guard_inference_diagnostics": {"scope": "new records observed in the worker's bounded recent-inference metadata; sampled diagnostics, not a replacement for durable accounting",
                                    "records": [record for observation in observed_worker for record in observation.get("new_inference", [])]},
                                "runner_cpu_seconds": sum(cpu_after[:2])-sum(cpu_before[:2]),
                                "runner_rss_bytes": psutil.Process().memory_info().rss,
                                "accounting": accounting,
                                "cost_per_completed_task": {
                                    "scope": "all cohort costs, including rejected attempts, amortized over completed tasks",
                                    "usd_micros": accounting["actual_usd_micros"]/len(accepted) if accepted else None,
                                    "guard_slot_seconds": accounting["guard_slot_seconds"]/len(accepted) if accepted else None,
                                    "tokens": accounting["total_tokens"]/len(accepted) if accepted else None},
                                "samples": samples}
                            report["rows"].append(row)
                            target.write_text(json.dumps(report, indent=2), encoding="utf-8")
                            print(json.dumps({key: row[key] for key in ("mode", "control_cache_enabled", "input_kib", "concurrency", "temperature", "requests", "completed", "p95_ms")}), flush=True)
            report["status"] = "completed"
        except Exception as exc:
            report.update(status="failed", failure=type(exc).__name__+": "+str(exc)[:400])
            raise
        finally:
            try:
                from actiongate.ledger import balances
                report["closing_tenant_balances"] = {tenant: balances(tenant) for tenant in tenants.values()}
            except Exception as exc:
                report["closing_balances_failure"] = type(exc).__name__
            if original:
                try:
                    restored = await checked("POST", "/api/policies/activate", json={"yaml": original["yaml"]})
                    report["restored_generation"] = restored["generation"]
                    report["original_policy_restored"] = (await checked("GET", "/api/policies"))["configuration"] == original["configuration"]
                    if not report["original_policy_restored"]:
                        report.update(status="failed", restore_failure="Original policy differs after restoration")
                except Exception as exc:
                    report.update(status="failed", restore_failure=type(exc).__name__)
            report["matched_comparisons"] = matched_comparisons(report["rows"])
            report["control_cache_comparisons"] = cache_comparisons(report["rows"])
            source_after = measurement_sources()
            source_before = report["measurement_source_files"]
            report["measurement_source_stable"] = source_after == source_before
            report["measurement_source_changed"] = sorted(name for name in set(source_before) | set(source_after)
                if source_before.get(name) != source_after.get(name))
            if not report["measurement_source_stable"]:
                report.update(status="failed", source_failure="Measured implementation changed during this benchmark")
            target.write_text(json.dumps(report, indent=2), encoding="utf-8")
            rows = ["<!doctype html><meta charset=utf-8><title>ActionGate measured benchmark</title><style>body{font:14px system-ui;margin:2em}td,th{padding:.5em;border:1px solid #ccc}table{border-collapse:collapse}</style>",
                "<h1>ActionGate "+suite+": "+html.escape(report["status"])+"</h1><p>"+html.escape(report["measurement_scope"])+". Recorded observations; rejection and completion are separate. Cache toggles compiled controls and semantic judgments together. Cold/warm refers to the model. See <a href="+suite+".json>JSON</a> for hardware, scope and resource accounting.</p>",
                "<table><tr><th>Mode</th><th>Control cache</th><th>KiB</th><th>Clients</th><th>Temperature</th><th>Requests</th><th>Completed</th><th>p50 ms</th><th>p95 ms</th><th>p99 ms</th></tr>"]
            for row in report["rows"]:
                rows.append("<tr>"+"".join("<td>"+html.escape(str(row[key]))+"</td>" for key in ("mode", "control_cache_enabled", "input_kib", "concurrency", "temperature", "requests", "completed", "p50_ms", "p95_ms", "p99_ms"))+"</tr>")
            (ROOT / ("artifacts/"+suite+".html")).write_text("".join(rows)+"</table>", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="local", choices=["local"])
    parser.add_argument("--url", default=os.getenv("SERVICE_BASE_URL", "http://gateway-a:8000"))
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--environment-label", default="reference-cpu")
    parser.add_argument("--hardware-report", type=Path, default=Path("artifacts/runtime-hardware.json"))
    parser.add_argument("--focused-deterministic", action="store_true", help="Separate preliminary diagnostic; the required full matrix is unchanged")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 100:
        raise ValueError("Repetitions must be between 1 and 100")
    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=64))
        report = await main_async(args)
        if report["status"] != "completed":
            raise RuntimeError("Benchmark did not complete with its original policy restored; inspect artifacts/benchmark.json")
    asyncio.run(run())


if __name__ == "__main__":
    main()
