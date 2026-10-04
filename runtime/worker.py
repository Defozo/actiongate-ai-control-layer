"""Narrow local inference supervisor. No Docker socket, shell or arbitrary process API."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import psutil
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from tokenizers import Tokenizer

ROLE = os.getenv("WORKER_ROLE", "guard")
TOKEN = os.environ["WORKER_TOKEN"]
CONTEXT = int(os.getenv("CONTEXT_TOKENS", "8192"))
MAX_INPUT = int(os.getenv("MAX_INPUT_TOKENS", "4096"))
MAX_WAITING = 8
MAX_DEADLINE = 120 if ROLE == "business" else 180
MANIFEST = json.loads(Path("/app/models/model-manifest.json").read_text())
MODEL = os.getenv("OLLAMA_MODEL", MANIFEST["model"])
TOKENIZER = Tokenizer.from_file("/app/models/tokenizer.json")
TOKENIZER_SHA256 = hashlib.sha256(Path("/app/models/tokenizer.json").read_bytes()).hexdigest()
MANIFEST_SHA256 = hashlib.sha256(json.dumps(MANIFEST, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
if TOKENIZER_SHA256 != MANIFEST["tokenizer_sha256"]:
    raise RuntimeError("Tokenizer digest mismatch")
if MODEL != MANIFEST["model"] or any(type(MANIFEST.get(key)) is not type(value) or MANIFEST.get(key) != value
                                  for key, value in {"temperature": 0, "seed": 42, "think": False}.items()):
    raise RuntimeError("Manifest differs from the supported deterministic inference contract")


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str = ""
    tool_calls: list[dict] | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None


class Inference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[Message] = Field(min_length=1, max_length=64)
    max_tokens: int = Field(default=256, ge=1, le=2048)
    format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    deadline_seconds: float = Field(default=60, gt=0, le=180)
    request_id: str = Field(default="", max_length=100)
    ticket_id: str | None = None
    generation: int | None = Field(default=None, ge=1)


class Supervisor:
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.lock = asyncio.Lock()
        self.waiting = 0
        self.active = None
        self.epoch = 0
        self.runtime = None
        self.completed = 0
        self.stops: list[dict] = []
        self.quarantined = False
        self.tickets: dict[str, dict] = {}
        self.configurations: dict[int, dict] = {}
        self.recent_inference: list[dict] = []

    def record_usage(self, request, usage, outcome):
        self.recent_inference.append({"request_id": request.request_id, "generation": request.generation,
                                      "epoch": self.epoch, "outcome": outcome, "usage": dict(usage)})
        self.recent_inference = self.recent_inference[-20:]

    def resources(self):
        processes = {process.pid: process for process in [psutil.Process(os.getpid()), *self.processes()]}
        total_rss = 0
        for process in processes.values():
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                total_rss += process.memory_info().rss
        values = {"process_rss_bytes": total_rss, "process_rss_scope": "worker and Ollama descendants; shared pages may be counted more than once"}
        paths = {"cgroup_memory_current_bytes": ("memory.current", "memory/memory.usage_in_bytes"),
                 "cgroup_memory_limit_bytes": ("memory.max", "memory/memory.limit_in_bytes"),
                 "cgroup_memory_peak_bytes": ("memory.peak", "memory/memory.max_usage_in_bytes")}
        for name, candidates in paths.items():
            for relative in candidates:
                path = Path("/sys/fs/cgroup") / relative
                if path.exists():
                    value = path.read_text().strip()
                    values[name] = int(value) if value.isdigit() else None
                    break
        return values

    def purge_tickets(self):
        active_ticket = self.active.get("ticket_id") if self.active else None
        for ticket_id, ticket in list(self.tickets.items()):
            if ticket_id != active_ticket and (ticket["epoch"] != self.epoch or ticket["expires_monotonic"] <= time.monotonic()):
                del self.tickets[ticket_id]

    async def start(self):
        env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434", OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_MAX_QUEUE="1", OLLAMA_NO_CLOUD="1", HOME="/tmp/ollama", LLAMA_ARG_CACHE_RAM="512")
        # Child gets no authentication secret; model logs never contain user content.
        env.pop("WORKER_TOKEN", None)
        self.process = subprocess.Popen(["/usr/bin/ollama", "serve"], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.epoch += 1
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(120):
                if self.process.poll() is not None:
                    raise RuntimeError("Ollama failed to start")
                try:
                    response = await client.get("http://127.0.0.1:11434/api/tags")
                    response.raise_for_status()
                    models = response.json()["models"]
                    match = next((m for m in models if m["name"] == MODEL), None)
                    if not match or match["digest"].removeprefix("sha256:") != MANIFEST["digest"].removeprefix("sha256:"):
                        raise RuntimeError("Required pinned model missing or digest mismatch")
                    version_response = await client.get("http://127.0.0.1:11434/api/version")
                    version_response.raise_for_status()
                    self.runtime = "ollama:" + version_response.json()["version"]
                    if self.runtime != MANIFEST["runtime"]:
                        raise RuntimeError("Runtime version differs from the pinned model manifest")
                    return
                except httpx.HTTPError:
                    await asyncio.sleep(.25)
        raise RuntimeError("Ollama startup deadline")

    def processes(self):
        if not self.process:
            return []
        try:
            parent = psutil.Process(self.process.pid)
            return [parent, *parent.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []

    def cpu(self):
        total = 0.0
        for process in self.processes():
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                value = process.cpu_times()
                total += value.user + value.system
        return total

    async def stop(self, reason: str):
        began = time.monotonic()
        old_pid = self.process.pid if self.process else None
        descendants = self.processes()
        # Kill each discovered descendant plus the process group. Poll live descendants,
        # including runners which change session; slot remains owned until confirmation.
        for process in reversed(descendants):
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()
        if old_pid:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(old_pid, signal.SIGKILL)
        for _ in range(100):
            alive = []
            for process in descendants:
                with contextlib.suppress(psutil.NoSuchProcess):
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        alive.append(process.pid)
            if self.process:
                self.process.poll()
            if not alive:
                event = {"reason": reason, "old_pid": old_pid, "confirmed": True, "stop_seconds": round(time.monotonic()-began, 6), "epoch": self.epoch, "process_count": len(descendants)}
                self.stops.append(event)
                self.stops = self.stops[-100:]
                self.process = None
                return event
            await asyncio.sleep(.05)
        self.quarantined = True
        self.stops.append({"reason": reason, "old_pid": old_pid, "confirmed": False, "epoch": self.epoch})
        raise RuntimeError("Worker stop unconfirmed; slot quarantined")


supervisor = Supervisor()


@asynccontextmanager
async def lifespan(app):
    await supervisor.start()
    yield
    await supervisor.stop("shutdown")


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


def authorize(authorization: str | None):
    if not authorization or not hmac.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Worker identity required")


@app.get("/health/ready")
async def ready():
    okay = not supervisor.quarantined and supervisor.process is not None and supervisor.process.poll() is None
    if not okay:
        raise HTTPException(503, "Worker unavailable")
    return {"ready": True, "role": ROLE, "model": MODEL, "digest": MANIFEST["digest"], "epoch": supervisor.epoch,
            "tokenizer_sha256": TOKENIZER_SHA256, "model_manifest_sha256": MANIFEST_SHA256, "runtime": supervisor.runtime,
            "context_tokens": CONTEXT, "max_input_tokens": MAX_INPUT, "prepared_generations": sorted(supervisor.configurations)}


@app.get("/status")
async def status(authorization: str | None = Header(default=None)):
    authorize(authorization)
    supervisor.purge_tickets()
    return {"role": ROLE, "active": supervisor.active, "waiting": supervisor.waiting, "reserved_output_jobs": len(supervisor.tickets), "prepared_generations": sorted(supervisor.configurations), "epoch": supervisor.epoch, "completed": supervisor.completed, "quarantined": supervisor.quarantined, "stops": supervisor.stops, "recent_inference": supervisor.recent_inference, "resources": supervisor.resources(), "processes": [{"pid": p.pid, "name": p.name()} for p in supervisor.processes()]}


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(ge=1)
    config: dict[str, Any]


def selected_configuration(generation):
    if generation is None:
        # Authenticated diagnostics use the physical deployment ceilings. Every
        # production broker request passes its signed control generation.
        return {"max_waiting_jobs": MAX_WAITING, "queue_wait_seconds": 10,
                "deadline_seconds": MAX_DEADLINE, "context_tokens": CONTEXT,
                "max_input_tokens": MAX_INPUT, "max_output_tokens": 2048, "max_windows": 128}
    value = supervisor.configurations.get(generation)
    if not value:
        raise HTTPException(409, "Control generation was not prepared by this worker")
    return value["effective"]


@app.post("/configuration")
async def configure(request: Configuration, authorization: str | None = Header(default=None)):
    authorize(authorization)
    try:
        if set(request.config) != {"semantic", "local_resources"}:
            raise ValueError("Expected semantic and local_resources configuration")
        semantic, local = request.config["semantic"], request.config["local_resources"]
        if any(type(local[key]) is not int or local[key] != 1 for key in ("business_slots", "guard_slots")) or local["require_confirmed_stop"] is not True:
            raise ValueError("Deployment requires one supervised slot and confirmed stops")
        effective = {"max_waiting_jobs": local["max_waiting_jobs"], "queue_wait_seconds": local["queue_wait_seconds"],
            "deadline_seconds": semantic["deadline_seconds"] if ROLE == "guard" else local["business_call_deadline_seconds"],
            "context_tokens": semantic["context_tokens"] if ROLE == "guard" else CONTEXT,
            "max_input_tokens": semantic["max_input_tokens_per_call"] if ROLE == "guard" else MAX_INPUT,
            "max_output_tokens": semantic["max_output_tokens"] if ROLE == "guard" else 2048, "max_windows": semantic["max_windows"]}
        ceilings = {"max_waiting_jobs": 128, "queue_wait_seconds": 120, "deadline_seconds": MAX_DEADLINE,
            "context_tokens": CONTEXT, "max_input_tokens": MAX_INPUT, "max_output_tokens": 2048, "max_windows": 128}
        for key, value in effective.items():
            if type(value) is not int or value < (0 if key == "max_waiting_jobs" else 1) or value > ceilings[key]:
                raise ValueError("Unsupported resource bound: " + key)
        if effective["max_input_tokens"] + effective["max_output_tokens"] > effective["context_tokens"]:
            raise ValueError("Input and output reservations exceed the deployed context")
        canonical = json.dumps(request.config, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        previous = supervisor.configurations.get(request.generation)
        if previous and previous["digest"] != digest:
            raise HTTPException(409, "A prepared control generation is immutable")
        supervisor.configurations[request.generation] = {"digest": digest, "effective": effective}
        # Already queued requests keep their selected immutable object locally.
        for old in sorted(supervisor.configurations)[:-64]:
            del supervisor.configurations[old]
        return {"prepared": True, "generation": request.generation, "configuration_digest": digest,
                "role": ROLE, "model_digest": MANIFEST["digest"], "model_manifest_sha256": MANIFEST_SHA256,
                "tokenizer_sha256": TOKENIZER_SHA256, "effective": effective}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    windows: int = Field(ge=1, le=128)
    generation: int | None = Field(default=None, ge=1)


@app.post("/tickets")
async def reserve_ticket(request: TicketRequest, authorization: str | None = Header(default=None)):
    import uuid
    authorize(authorization)
    if ROLE != "guard" or supervisor.quarantined:
        raise HTTPException(503, "Guard reservation unavailable")
    supervisor.purge_tickets()
    config = selected_configuration(request.generation)
    # Long input retains the configured source-window limit and requires one
    # additional goal/action assessment over their bounded summaries.
    maximum_passes = config["max_windows"] + int(config["max_windows"] > 1)
    if request.windows > maximum_passes:
        raise HTTPException(422, "Inspection reservation exceeds prepared window limit")
    if len(supervisor.tickets)+supervisor.waiting >= config["max_waiting_jobs"]:
        raise HTTPException(429, "No output-inspection queue capacity")
    ticket_id = uuid.uuid4().hex
    # A gateway crash must not occupy the bounded output queue forever. Cover
    # every reserved scan plus the business call and queues, then expire safely.
    lease_seconds = request.windows * config["deadline_seconds"] + (request.windows + 1) * config["queue_wait_seconds"] + 150
    supervisor.tickets[ticket_id] = {"remaining": request.windows, "epoch": supervisor.epoch, "generation": request.generation,
                                   "expires_monotonic": time.monotonic() + lease_seconds}
    return {"ticket_id": ticket_id, "windows": request.windows, "epoch": supervisor.epoch, "generation": request.generation,
            "lease_seconds": lease_seconds}


@app.delete("/tickets/{ticket_id}")
async def release_ticket(ticket_id: str, authorization: str | None = Header(default=None)):
    authorize(authorization)
    supervisor.tickets.pop(ticket_id, None)
    return {"released": True}


@app.post("/benchmark/unload")
async def unload(authorization: str | None = Header(default=None)):
    """Cold-load measurement for the fixed model, only on an idle process tree."""
    authorize(authorization)
    supervisor.purge_tickets()
    if supervisor.quarantined or supervisor.lock.locked() or supervisor.waiting or supervisor.tickets:
        raise HTTPException(409, "Unload requires an idle worker without reserved inspection jobs")
    await supervisor.lock.acquire()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post("http://127.0.0.1:11434/api/generate", json={"model": MODEL, "keep_alive": 0})
            response.raise_for_status()
            # Ollama acknowledges the unload request before its asynchronous
            # runner teardown necessarily disappears from /api/ps. Keep the
            # slot locked and require observed absence within a bounded grace.
            until = time.monotonic()+5
            while True:
                loaded = await client.get("http://127.0.0.1:11434/api/ps")
                loaded.raise_for_status()
                present = any(item.get("name") == MODEL or item.get("model") == MODEL for item in loaded.json().get("models", []))
                if not present or time.monotonic() >= until:
                    break
                await asyncio.sleep(.05)
        if present:
            raise HTTPException(503, "Model unload was not confirmed")
        return {"confirmed": True, "model": MODEL, "epoch": supervisor.epoch,
                "prepared_generations": sorted(supervisor.configurations), "mode": "cold_model_load"}
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Model unload request failed") from exc
    finally:
        supervisor.lock.release()


@app.post("/infer")
async def infer(request: Inference, authorization: str | None = Header(default=None)):
    authorize(authorization)
    if supervisor.quarantined:
        raise HTTPException(503, "Worker quarantined")
    if ROLE == "guard" and request.tools:
        raise HTTPException(422, "Guard has no tools")
    supervisor.purge_tickets()
    config = selected_configuration(request.generation)
    if request.max_tokens > config["max_output_tokens"] or request.deadline_seconds > config["deadline_seconds"]:
        raise HTTPException(422, "Inference request exceeds prepared resource configuration")
    messages = [m.model_dump(exclude_none=True) for m in request.messages]
    full = json.dumps({"messages": messages, "tools": request.tools, "format": request.format}, ensure_ascii=False, separators=(",", ":"))
    token_count = len(TOKENIZER.encode(full).ids)
    # Account for every serialized field and Qwen role/template expansion using
    # the pinned tokenizer, with a conservative per-message/template allowance.
    upper_bound = token_count + 32 * len(messages) + 256
    if upper_bound > config["max_input_tokens"] or upper_bound + request.max_tokens > config["context_tokens"]:
        raise HTTPException(413, "Full context exceeds verified model limit; input not truncated")
    ticket = supervisor.tickets.get(request.ticket_id) if request.ticket_id else None
    if request.ticket_id and (not ticket or ticket["remaining"] < 1 or ticket["epoch"] != supervisor.epoch or ticket["generation"] != request.generation):
        raise HTTPException(409, "Inspection queue reservation is stale or exhausted")
    if not ticket and supervisor.waiting + len(supervisor.tickets) >= config["max_waiting_jobs"]:
        raise HTTPException(429, "Worker queue full")
    if ticket:
        ticket["remaining"] -= 1
    supervisor.waiting += 1
    try:
        await asyncio.wait_for(supervisor.lock.acquire(), timeout=config["queue_wait_seconds"])
    except TimeoutError:
        raise HTTPException(429, "Worker queue deadline")
    finally:
        supervisor.waiting -= 1
    began = time.monotonic()
    cpu_before = supervisor.cpu()
    supervisor.active = {"id": request.request_id, "ticket_id": request.ticket_id, "started_monotonic": began, "epoch": supervisor.epoch}
    try:
        # A preceding queued request can restart this process tree. A reservation
        # admitted before that restart must never silently acquire a fresh slot.
        if ticket and (ticket["epoch"] != supervisor.epoch or ticket["expires_monotonic"] <= time.monotonic()):
            raise HTTPException(409, "Inspection queue reservation expired or invalidated by worker restart")
        deadline = request.deadline_seconds
        payload = {"model": MODEL, "messages": messages, "stream": False, "think": False, "keep_alive": "30m", "options": {"temperature": 0, "seed": 42, "num_ctx": config["context_tokens"], "num_predict": request.max_tokens, "num_thread": int(os.getenv("MODEL_THREADS", "8")), "num_gpu": int(os.getenv("MODEL_GPU_LAYERS", "0"))}}
        if request.format:
            payload["format"] = request.format
        if request.tools:
            payload["tools"] = request.tools
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                response = await asyncio.wait_for(client.post("http://127.0.0.1:11434/api/chat", json=payload), timeout=deadline)
                response.raise_for_status()
                data = response.json()
            except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
                measured_cpu = max(0, supervisor.cpu()-cpu_before)
                stop = await asyncio.shield(supervisor.stop("deadline" if isinstance(exc, TimeoutError) else "transport_or_cancel"))
                usage = {"inference_slot_seconds": round(time.monotonic()-began,6), "cpu_seconds": measured_cpu, "usage_unknown": True}
                supervisor.record_usage(request, usage, "interrupted_before_final_upstream_metadata")
                await asyncio.shield(supervisor.start())
                raise HTTPException(504, {"error": "inference_interrupted", "stop": stop, "usage": usage})
        measured_usage = {"inference_slot_seconds": round(time.monotonic()-began, 6),
                          "cpu_seconds": max(0, supervisor.cpu()-cpu_before), "usage_unknown": True}
        if not isinstance(data, dict) or data.get("done") is not True or any(type(data.get(name)) is not int or data[name] < 0 for name in ("prompt_eval_count", "eval_count")):
            stop = await asyncio.shield(supervisor.stop("incomplete_result_or_usage"))
            supervisor.record_usage(request, measured_usage, "incomplete_result_or_usage")
            await asyncio.shield(supervisor.start())
            raise HTTPException(502, {"error": "Incomplete inference or missing usage", "usage": measured_usage, "stop": stop})
        measured_usage.update(prompt_tokens=data["prompt_eval_count"], completion_tokens=data["eval_count"],
                              total_tokens=data["prompt_eval_count"] + data["eval_count"], usage_unknown=False)
        # Ollama durations are nanoseconds. Retain metadata, never prompt or
        # output content, so a deadline can be investigated without guessing
        # whether model loading, prefill or decode caused the latency.
        phases = {target: round(data[source] / 1_000_000_000, 6) for source, target in
                  (("total_duration", "total_seconds"), ("load_duration", "load_seconds"),
                   ("prompt_eval_duration", "prefill_seconds"), ("eval_duration", "decode_seconds"))
                  if type(data.get(source)) is int and data[source] >= 0}
        if phases.get("prefill_seconds", 0) > 0:
            phases["prefill_tokens_per_second"] = round(data["prompt_eval_count"] / phases["prefill_seconds"], 3)
        if phases.get("decode_seconds", 0) > 0:
            phases["decode_tokens_per_second"] = round(data["eval_count"] / phases["decode_seconds"], 3)
        measured_usage["upstream_timings"] = phases
        if data["prompt_eval_count"] > config["max_input_tokens"] or data["eval_count"] > request.max_tokens:
            supervisor.quarantined = True
            stop = await supervisor.stop("token_contract_breach")
            supervisor.record_usage(request, measured_usage, "token_contract_breach")
            raise HTTPException(502, {"error": "Model violated token contract; worker quarantined", "usage": measured_usage, "stop": stop})
        if not isinstance(data.get("message"), dict):
            supervisor.record_usage(request, measured_usage, "invalid_message")
            raise HTTPException(502, {"error": "Model returned an invalid message envelope", "usage": measured_usage})
        if len(json.dumps(data["message"], ensure_ascii=False).encode("utf-8")) > 65536:
            supervisor.record_usage(request, measured_usage, "output_buffer_exceeded")
            raise HTTPException(502, {"error": "Model response exceeded the bounded output buffer", "usage": measured_usage})
        supervisor.completed += 1
        supervisor.record_usage(request, measured_usage, "completed")
        return {"message": data["message"], "done_reason": data.get("done_reason"), "model": MODEL, "model_digest": MANIFEST["digest"],
                "model_manifest_sha256": MANIFEST_SHA256, "tokenizer_sha256": TOKENIZER_SHA256,
                "worker_epoch": supervisor.epoch, "generation": request.generation, "usage": measured_usage,
                "context_preflight": {"tokenizer_tokens": token_count, "upper_bound": upper_bound, "context": config["context_tokens"]}}
    finally:
        supervisor.active = None
        supervisor.lock.release()
