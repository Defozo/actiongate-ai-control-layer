"""Reproducible local preparation and psst-only secret injection."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["ACTIONGATE_AUTH_KEY", "ACTIONGATE_ENCRYPTION_KEY", "ACTIONGATE_SPOOL_KEY", "ACTIONGATE_AUDIT_KEY", "ACTIONGATE_DB_PASSWORD", "ACTIONGATE_DB_OWNER_PASSWORD", "ACTIONGATE_CONNECTOR_KEY", "ACTIONGATE_GUARD_KEY", "ACTIONGATE_BUSINESS_KEY", "ACTIONGATE_POLICY_SIGNING_KEY", "ACTIONGATE_FEED_SIGNING_KEY"]


def psst(*args, **kwargs):
    executable = shutil.which("psst.cmd" if os.name == "nt" else "psst")
    if not executable:
        raise RuntimeError("psst is required; install/unlock your own vault first")
    return subprocess.run([executable, *args], check=True, text=True, **kwargs)


def provision():
    listing = json.loads(psst("list", "--json", capture_output=True).stdout)
    existing = {entry["name"] for entry in listing["secrets"]}
    for name in NAMES:
        if name in existing:
            print(f"Keeping existing psst entry {name}")
            continue
        value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() if name.endswith(("ENCRYPTION_KEY", "SPOOL_KEY", "SIGNING_KEY")) else secrets.token_hex(32)
        psst("set", name, "--stdin", "--tag", "actiongate", input=value, capture_output=True)
        print(f"Created psst entry {name}")


def injected():
    names = [*NAMES, *(["GROQ_API_KEY"] if "cloud" in sys.argv else [])]
    missing = [name for name in names if name not in os.environ]
    if missing:
        # Specific secrets only, never psst run with a complete vault.
        executable = shutil.which("psst.cmd" if os.name == "nt" else "psst")
        result = subprocess.run([executable, *names, "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--injected"], cwd=ROOT)
        raise SystemExit(result.returncode)


def environment():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    env = dict(os.environ)
    env['ACTIONGATE_LOCAL_MODEL'] = pinned_model()
    for kind in ("POLICY", "FEED"):
        key = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(env[f"ACTIONGATE_{kind}_SIGNING_KEY"]))
        env[f"ACTIONGATE_{kind}_PUBLIC_KEY"] = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    return env


def compose(args, **kwargs):
    # Compose interpolates only per-service allowlisted variables. Signing keys
    # are used by this publisher process and never mapped into runtime services.
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, env=environment(), check=True, **kwargs)


def publish_seed():
    sys.path.insert(0, str(ROOT/"backend"))
    from actiongate.controls import sign_document
    source = ROOT/"feeds/demo-rules.json"
    if not source.exists():
        return
    data = json.loads(source.read_text(encoding="utf-8"))
    if "payload" in data:
        data = data["payload"]
    now = datetime.now(timezone.utc)
    data["issued_at"] = now.isoformat()
    data["expires_at"] = (now+timedelta(hours=23, minutes=59)).isoformat()
    envelope = sign_document(data, os.environ["ACTIONGATE_FEED_SIGNING_KEY"], "demo-feed-v1")
    (ROOT/"feeds/feed.json").write_text(json.dumps(envelope, indent=2), encoding="utf-8")


def pinned_model():
    manifest = json.loads((ROOT/'models/model-manifest.json').read_text())
    model = manifest.get('model', '')
    if (not re.fullmatch(r'[A-Za-z0-9._-]+:[A-Za-z0-9._-]+', model)
            or model.split(':')[0] in {'.', '..'}
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', manifest.get('digest', ''))):
        raise RuntimeError('A bounded, pinned release model manifest is required')
    return model


def prepare_tokenizer():
    folder = ROOT/"models"
    folder.mkdir(exist_ok=True)
    manifest_path = folder/"model-manifest.json"
    # The release manifest is authoritative. Bootstrap never silently advances
    # tokenizer/model revisions or rewrites a previously pinned digest.
    previous = json.loads(manifest_path.read_text())
    commit = previous['tokenizer_commit']
    url = previous['tokenizer_source']
    if not re.fullmatch(r'https://huggingface\.co/Qwen/[A-Za-z0-9._-]+/resolve/'+re.escape(commit)+r'/tokenizer\.json', url) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise RuntimeError('Tokenizer source must use the pinned official repository revision')
    expected = previous['tokenizer_sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise RuntimeError('Tokenizer digest is not pinned')
    target = folder/"tokenizer.json"
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise RuntimeError("Tokenizer artifact digest mismatch")
    target.write_bytes(payload)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "compose":
        if "--injected" not in sys.argv:
            injected()
        compose([arg for arg in sys.argv[2:] if arg != "--injected"])
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["bootstrap", "start", "deploy", "refresh", "doctor", "compose", "secrets", "tokenizer", "publish-seed"])
    parser.add_argument("--profile", default="local", choices=["local", "cloud"])
    parser.add_argument("--injected", action="store_true")
    args, remaining = parser.parse_known_args()
    if args.action == "tokenizer":
        prepare_tokenizer()
        return
    if args.action in {"bootstrap", "secrets"} and not args.injected:
        provision()
        if args.action == "secrets":
            return
    if not args.injected:
        injected()
    if args.action == "bootstrap":
        prepare_tokenizer()
        compose(["--profile", "prepare", "run", "--rm", "model-prepare"])
        # Read the exact manifest pulled into the dedicated Docker volume.
        model_name, model_tag = pinned_model().split(':')
        result = compose(["--profile", "prepare", "run", "--rm", "--entrypoint", "/bin/cat", "model-prepare",
            f"/root/.ollama/models/manifests/registry.ollama.ai/library/{model_name}/{model_tag}"], capture_output=True)
        raw = result.stdout
        if isinstance(raw, str):
            raw = raw.encode()
        manifest_path = ROOT/"models/model-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        actual_digest = "sha256:"+hashlib.sha256(raw).hexdigest()
        if manifest["digest"] != actual_digest:
            raise RuntimeError("Prepared model differs from pinned release manifest")
        publish_seed()
        compose(["pull", "postgres", "opa-a", "opa-b", "edge"])
        compose(["--profile", "test", "build"])
        print("Prepared images, pinned model, tokenizer and local identities. Start runs without downloads.")
    elif args.action in {"start", "deploy"}:
        if args.action == "deploy":
            compose(["build", "gateway-a", "test-runner", "agent-probe", "guard-worker"])
        compose([*(["--profile", "cloud"] if args.profile == "cloud" else []), "up", "-d", "--no-build", "--pull", "never"])
    elif args.action == "refresh":
        # Code-only refresh retains supervised model processes and their jobs.
        compose(["build", "gateway-a", "test-runner", "agent-probe"])
        compose([*(["--profile", "cloud"] if args.profile == "cloud" else []), "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                 "gateway-a", "gateway-b", "demo-tools", "feed-server", *(["cloud-connector"] if args.profile == "cloud" else [])])
    elif args.action == "doctor":
        compose(["ps"])
        subprocess.run([sys.executable, str(ROOT/"scripts/preflight.py")], cwd=ROOT, env=environment(), check=True)
    elif args.action == "publish-seed":
        publish_seed()
    elif args.action == "compose":
        compose(remaining)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ActionGate preparation failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
