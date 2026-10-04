"""Verify the authorized gateway-only keep-alive change, without inference.

Run with --recreate to selectively replace the four gateways using existing
images, volumes and psst entries. Exact producer bytes accompany the report.
No environment values except the public origin and keep-alive setting are
reported. HTTP probes use one TCP socket with automatic reopening disabled.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from runtime_bootstrap import NAMES, environment

PROJECTS = {
    "actiongate": (8088, ["compose.yaml"]),
    "actiongate-gpu-0b92f97d": (18089, ["compose.yaml", ".state/clean-install/0b92f97d/compose.override.yaml", "deploy/compose.gpu.yaml"]),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def inspect(name):
    return json.loads(run("docker", "inspect", name))[0]


def safe_metadata(value):
    env = dict(item.split("=", 1) for item in value["Config"]["Env"])
    return {"container_id": value["Id"], "image_id": value["Image"],
            "command": value["Config"]["Cmd"],
            "safe_environment": {key: env.get(key) for key in ("PUBLIC_BASE_URL", "UVICORN_TIMEOUT_KEEP_ALIVE")},
            "mounts": [{key: mount.get(key) for key in ("Type", "Name", "Source", "Destination", "RW")}
                       for mount in value["Mounts"]]}


PROBE = r'''
import http.client, importlib.metadata, json, socket, time
from uvicorn.main import main
ctx = main.make_context('uvicorn', ['actiongate.app:app', '--host', '0.0.0.0', '--port', '8000', '--no-access-log'])
conn = http.client.HTTPConnection('127.0.0.1', 8000, timeout=12)
conn.connect()
conn.auto_open = 0
original = conn.sock
local, peer, fileno = original.getsockname(), original.getpeername(), original.fileno()
def request():
    conn.request('GET', '/health/ready', headers={'Connection':'keep-alive'})
    response = conn.getresponse()
    body = json.loads(response.read())
    return {'status':response.status,'generation':body.get('generation'),'ready':body.get('ready'),
            'same_socket':conn.sock is original,'will_close':response.will_close}
first = request()
started = time.monotonic()
time.sleep(6.05)
idle = time.monotonic()-started
second = request()
binding_same = conn.sock is original and original.getsockname()==local and original.getpeername()==peer and original.fileno()==fileno
conn.close()
print(json.dumps({'uvicorn_version':importlib.metadata.version('uvicorn'),
    'effective_cli_timeout_keep_alive':ctx.params['timeout_keep_alive'],
    'auto_open':0,'idle_seconds':idle,'local_address':local,'peer_address':peer,
    'socket_fileno':fileno,'same_tcp_socket_after_both_responses':binding_same,
    'first':first,'second':second}))
'''


def probe(name):
    return name, json.loads(run("docker", "exec", name, "python", "-c", PROBE))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true", required=True)
    parser.add_argument("--injected", action="store_true")
    args = parser.parse_args()
    if not args.injected:
        executable = shutil.which("psst.cmd" if os.name == "nt" else "psst")
        return subprocess.run([executable, *NAMES, "--", sys.executable, str(Path(__file__).resolve()),
                               "--recreate", "--injected"], cwd=ROOT).returncode
    sources = json.loads((ROOT / "artifacts/history/cpu-v29-pg100-before-capacity-fix/all-local.json").read_text())["source_files"]
    changed = [name for name, expected in sources.items() if sha(ROOT / name) != expected]
    if changed or len(sources) != 95:
        raise RuntimeError("Frozen source verification failed")
    compose_hash = sha(ROOT / "compose.yaml")
    hardware_paths = ("artifacts/runtime-hardware.json", "artifacts/gpu-reference/runtime-hardware.json")
    hardware = {name: sha(ROOT / name) for name in hardware_paths}
    names = [project + "-" + role + "-1" for project in PROJECTS for role in ("gateway-a", "gateway-b")]
    before = {name: inspect(name) for name in names}
    worker_names = [project + "-" + role + "-1" for project in PROJECTS for role in ("guard-worker", "business-worker")]
    workers = {name: inspect(name)["Id"] for name in worker_names}
    for project, (port, files) in PROJECTS.items():
        env = environment()
        env["ACTIONGATE_PORT"] = str(port)
        command = ["docker", "compose", "-p", project]
        for path in files:
            command.extend(("-f", str(ROOT / path)))
        command += ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "gateway-a", "gateway-b"]
        run(*command, cwd=ROOT, env=env)
        print(json.dumps({"project":project,"gateway_recreate":"done"}), flush=True)
    for name in names:
        deadline = time.monotonic() + 100
        while True:
            status = inspect(name)["State"].get("Health", {}).get("Status")
            if status == "healthy":
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Gateway did not become healthy")
            time.sleep(2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        probes = dict(pool.map(probe, names))
    after = {name: inspect(name) for name in names}
    checks = {"all_95_frozen_sources_unchanged": all(sha(ROOT / name) == expected for name, expected in sources.items()),
              "compose_unchanged_during_verification": compose_hash == sha(ROOT / "compose.yaml"),
              "hardware_reports_not_modified": all(sha(ROOT / name) == digest for name, digest in hardware.items()),
              "worker_containers_unchanged": all(inspect(name)["Id"] == ident for name, ident in workers.items())}
    for name in names:
        old, new = before[name], after[name]
        old_env = dict(item.split("=", 1) for item in old["Config"]["Env"])
        new_env = dict(item.split("=", 1) for item in new["Config"]["Env"])
        old_env.pop("UVICORN_TIMEOUT_KEEP_ALIVE", None)
        new_env.pop("UVICORN_TIMEOUT_KEEP_ALIVE", None)
        value = probes[name]
        checks[name + "_only_requested_environment_changed"] = old_env == new_env
        checks[name + "_same_image_and_mounts"] = old["Image"] == new["Image"] and old["Mounts"] == new["Mounts"]
        checks[name + "_recreated"] = old["Id"] != new["Id"]
        checks[name + "_effective_keepalive_30"] = value["effective_cli_timeout_keep_alive"] == 30 and value["uvicorn_version"] == "0.54.0"
        checks[name + "_same_tcp_after_idle_6_seconds"] = (value["idle_seconds"] >= 6 and value["same_tcp_socket_after_both_responses"]
            and all(value[phase]["status"] == 200 and value[phase]["same_socket"] and not value[phase]["will_close"] for phase in ("first", "second")))
    report = {"schema_version":1,"generated_at":datetime.now(timezone.utc).isoformat(),
              "scope":"Gateway-only keep-alive safety margin; original isolated ReadError cause remains unproven. No inference performed.",
              "compose_sha256":compose_hash,"producer":"artifacts/verify_keepalive_runtime.py", "producer_sha256":sha(Path(__file__)),
              "previous_acceptance":"artifacts/history/cpu-v29-pg100-before-capacity-fix/all-local.json", "frozen_source_count":len(sources),
              "hardware_report_sha256_unchanged":hardware,"workers_unchanged":workers,
              "before":{name:safe_metadata(value) for name,value in before.items()},
              "after":{name:safe_metadata(value) for name,value in after.items()},"tcp_probes":probes,"checks":checks,"passed":all(checks.values())}
    (ROOT / "artifacts/keepalive-runtime-verification.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"checks":checks,"compose_sha256":compose_hash}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
