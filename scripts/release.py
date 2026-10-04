"""Build an attributable dependency inventory and a secret-free submission archive.

Uses exact uv/npm lockfile entries, installed distribution metadata and preserved
upstream license texts. --refresh-licenses explicitly fetches missing PyPI
metadata and pinned runtime/model licenses; ordinary reruns work offline.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
import urllib.parse
import urllib.request
import uuid
import zipfile


LICENSE_SOURCES = [
    ("opa-1.15.0", "Apache-2.0", "https://raw.githubusercontent.com/open-policy-agent/opa/v1.15.0/LICENSE"),
    ("postgresql-17.11", "PostgreSQL", "https://raw.githubusercontent.com/postgres/postgres/REL_17_11/COPYRIGHT"),
    ("nginx-1.30.5", "BSD-2-Clause", "https://raw.githubusercontent.com/nginx/nginx/release-1.30.5/LICENSE"),
    ("CPython-3.13.15", "PSF-2.0", "https://raw.githubusercontent.com/python/cpython/v3.13.15/LICENSE"),
    ("Node.js-24.18.0", "MIT AND third-party licenses", "https://raw.githubusercontent.com/nodejs/node/v24.18.0/LICENSE"),
]

MODEL_LICENSE_PINS = {
    "qwen3:4b-instruct-2507-q4_K_M": ("Qwen3-4B-Instruct-2507", "cdbee75f17c01a7cc42f958dc650907174af0554"),
    "qwen3.5:4b": ("Qwen3.5-4B", "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"),
    "qwen3:8b-q4_K_M": ("Qwen3-8B", "b968826d9c46dd6066d109eabc6255188de91218"),
}


def model_license_sources(model):
    """Select notices from the actual approved model, tokenizer and runtime pins."""
    if model.get("model") not in MODEL_LICENSE_PINS or model.get("license") != "Apache-2.0":
        raise RuntimeError("The prepared model requires an explicitly reviewed license source")
    name, commit = MODEL_LICENSE_PINS[model["model"]]
    tokenizer = re.fullmatch(r"https://huggingface.co/Qwen/([A-Za-z0-9.-]+)/resolve/([0-9a-f]{40})/tokenizer.json", model.get("tokenizer_source", ""))
    runtime = re.fullmatch(r"ollama:([0-9]+\.[0-9]+\.[0-9]+)", model.get("runtime", ""))
    if tokenizer is None or tokenizer[2] != model.get("tokenizer_commit") or runtime is None:
        raise RuntimeError("Model notices require a pinned tokenizer and released runtime version")
    return [(name, "Apache-2.0", f"https://huggingface.co/Qwen/{name}/resolve/{commit}/LICENSE"),
        (tokenizer[1]+"-tokenizer", "Apache-2.0", model["tokenizer_source"].removesuffix("tokenizer.json")+"LICENSE"),
        ("ollama-"+runtime[1], "MIT", f"https://raw.githubusercontent.com/ollama/ollama/v{runtime[1]}/LICENSE")]

# Optional GPU backend binaries observed in the pinned Ollama image. Exact
# component versions also match NVIDIA's versioned redistributable catalogs.
GPU_LIBRARY_PINS = {
    "ollama:0.18.2": [
        {"name": "libcudart", "version": "12.8.90", "toolkit": "12.8.1"},
        {"name": "libcublas/libcublasLt", "version": "12.8.4.1", "toolkit": "12.8.1"}],
    "ollama:0.32.0": [
        {"name": "libcudart", "version": "12.8.90", "toolkit": "12.8.2"},
        {"name": "libcublas/libcublasLt", "version": "12.8.5.5", "toolkit": "12.8.2"}],
}


def gpu_libraries(model):
    if model.get("runtime") not in GPU_LIBRARY_PINS:
        raise RuntimeError("The optional GPU runtime requires a reviewed exact library inventory")
    return GPU_LIBRARY_PINS[model["runtime"]]

SOURCE_DIRECTORIES = {"backend", "runtime", "deploy", "demo", "packages", "migrations", "policy", "feeds", "models", "scripts", "tests", "docs", "ui"}
ROOT_SOURCE_FILES = {"README.md", "TEAM.json", "THIRD_PARTY_NOTICES.md", "compose.yaml", "Dockerfile", "pyproject.toml", "uv.lock", ".env.example", ".dockerignore", ".gitignore", ".gitattributes"}
# Preserve the selected specification and its local reference closure. This is
# an explicit file allowlist, not permission to package scratch/download state
# or an unrelated future proposal placed in the same directory.
SELECTED_SPEC_ROOT = "official-2026-10-03"
SELECTED_SPEC_FILES = {f"{SELECTED_SPEC_ROOT}/{name}" for name in (
    "PLAN.md", "TASK.json", "MATERIALS.md", "SERVICES.md", "WYBOR.md",
    "materials/manifest.json", "materials/task.json", "proposals/README.md",
    "proposals/B/materials/manifest.json", "proposals/C/SUMMARIES.md")}
SELECTED_SPEC_FILES.update(f"{SELECTED_SPEC_ROOT}/proposals/{proposal}/{name}"
    for proposal in ("A", "B", "C", "D") for name in ("PLAN.md", "TASK.json", "MATERIALS.md", "SERVICES.md"))
SELECTED_SPEC_FILES.update(f"{SELECTED_SPEC_ROOT}/{prefix}materials/{name}{suffix}"
    for prefix in ("", "proposals/A/", "proposals/B/", "proposals/C/", "proposals/D/")
    for name in ("31a3fb1537ac1d02.pdf", "786a9bb4a858f98d.pdf") for suffix in ("", ".txt"))


def canonical_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")


def download(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ActionGate-license-inventory/1"}), timeout=30) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise RuntimeError("License metadata exceeds download limit")
    return data


def license_value(metadata):
    expression = metadata.get("License-Expression") or metadata.get("license_expression")
    if expression:
        return expression
    classifiers = metadata.get_all("Classifier", []) if hasattr(metadata, "get_all") else metadata.get("classifiers", [])
    names = [value.split(" :: ")[-1] for value in classifiers if value.startswith("License ::") and "OSI Approved" != value.split(" :: ")[-1]]
    aliases = {"MIT License": "MIT", "Apache Software License": "Apache-2.0", "BSD License": "BSD (see preserved license)",
        "Python Software Foundation License": "PSF-2.0", "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
        "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
        "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0-or-later",
        "ISC License (ISCL)": "ISC"}
    if names:
        return " OR ".join(aliases.get(name, name) for name in names)
    raw = metadata.get("License") or metadata.get("license") or ""
    if raw and raw not in {"UNKNOWN", "Unknown"}:
        return raw.strip() if len(raw) < 160 else "License text preserved in distribution metadata"
    return "NOT_DECLARED"


def python_inventory(root, output, refresh):
    lock = tomllib.loads((root / "uv.lock").read_text())
    packages = list(lock["package"])
    runtime_lock = root / "runtime/requirements.lock"
    if runtime_lock.exists():
        current = None
        for line in runtime_lock.read_text().splitlines():
            match = re.match(r"^([a-zA-Z0-9_.-]+)==([^\s\\]+)", line)
            if match:
                current = {"name": canonical_name(match[1]), "version": match[2], "wheels": [],
                           "lockfile": "runtime/requirements.lock"}
                packages.append(current)
            elif current:
                current["wheels"].extend({"hash": item} for item in re.findall(r"--hash=(sha256:[a-f0-9]{64})", line))
    installed = {canonical_name(d.metadata["Name"]): d for d in importlib.metadata.distributions() if d.metadata.get("Name")}
    records = {}
    pending = []
    for package in packages:
        if package["name"] == "actiongate":
            continue
        key = (package["name"], package["version"])
        if key in records:
            records[key].setdefault("lockfiles", []).append(package.get("lockfile", "uv.lock"))
            continue
        name, version = key
        entry = {"name": name, "version": version, "ecosystem": "pypi", "license": "NOT_DECLARED",
                 "license_files": [], "source": f"https://pypi.org/pypi/{name}/{version}/json",
                 "lockfiles": [package.get("lockfile", "uv.lock")],
                 "hashes": sorted({a["hash"] for a in [package.get("sdist", {}), *package.get("wheels", [])] if a.get("hash")})}
        cache = output / "licenses/metadata" / f"{name}-{version}.json"
        distribution = installed.get(canonical_name(name))
        if distribution and distribution.version == version:
            entry["license"] = license_value(distribution.metadata)
            entry["evidence"] = "installed locked distribution metadata"
            for item in distribution.files or []:
                basename = Path(str(item)).name
                if not re.match(r"^(licen[cs]e|copying|notice|copyright)([._-]|$)", basename, re.I):
                    continue
                source = Path(distribution.locate_file(item))
                if not source.is_file() or source.stat().st_size > 2_000_000:
                    continue
                destination = output / "licenses/python" / f"{name}-{version}" / (hashlib.sha256(str(item).encode()).hexdigest()[:8]+"-"+basename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                entry["license_files"].append(destination.relative_to(root).as_posix())
            write_json(cache, {"license": entry["license"], "evidence": entry["evidence"], "source": entry["source"]})
        elif cache.exists():
            entry.update(json.loads(cache.read_text()))
        elif refresh:
            pending.append((entry, cache))
        records[key] = entry
    def fetch(item):
        entry, cache = item
        try:
            data = json.loads(download(entry["source"]))["info"]
            metadata = {"license": license_value(data), "evidence": "exact-version PyPI publisher metadata", "source": entry["source"]}
            write_json(cache, metadata)
            entry.update(metadata)
        except Exception as exc:
            entry["metadata_error"] = type(exc).__name__
    if pending:
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(fetch, pending))
    return list(records.values())


def npm_inventory(root, output):
    lock = json.loads((root / "ui/package-lock.json").read_text())
    records = {}
    for folder, package in lock["packages"].items():
        if not folder or "version" not in package:
            continue
        name = package.get("name") or folder.rsplit("node_modules/", 1)[-1]
        key = (name, package["version"])
        if key in records:
            continue
        entry = {"name": name, "version": package["version"], "ecosystem": "npm", "license": package.get("license", "NOT_DECLARED"),
                 "license_files": [], "evidence": "exact-version npm package-lock metadata", "dev": package.get("dev", False),
                 "source": package.get("resolved"), "integrity": package.get("integrity")}
        folder_path = root / "ui" / folder
        if folder_path.exists():
            for source in folder_path.iterdir():
                if source.is_file() and re.match(r"^(licen[cs]e|copying|notice|copyright)([._-]|$)", source.name, re.I):
                    destination = output / "licenses/npm" / (name.replace("/", "__")+"-"+package["version"]) / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    entry["license_files"].append(destination.relative_to(root).as_posix())
        records[key] = entry
    return list(records.values())


def runtime_inventory(root, output, refresh):
    records = []
    model = json.loads((root/"models/model-manifest.json").read_text())
    toolkits = sorted({library["toolkit"] for library in gpu_libraries(model)})
    gpu_sources = [("NVIDIA-CUDA-Toolkit-"+version, "NVIDIA CUDA Toolkit EULA",
        "https://docs.nvidia.com/cuda/archive/"+version+"/eula/index.html") for version in toolkits]
    for name, license_id, url in model_license_sources(model)+LICENSE_SOURCES+gpu_sources:
        destination = output / "licenses/runtime" / (name+(".html" if "/eula/" in url else ".txt"))
        record = {"name": name, "license": license_id, "source": url}
        if refresh:
            try:
                data = download(url)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            except Exception as exc:
                record["error"] = type(exc).__name__
        if destination.exists():
            record.update(license_file=destination.relative_to(root).as_posix(), sha256=sha(destination))
        else:
            record["status"] = "license_text_not_available"
        records.append(record)
    return records


def component(entry):
    name = urllib.parse.quote(entry["name"], safe="/")
    purl = f"pkg:{entry['ecosystem']}/{name}@{entry['version']}"
    result = {"type": "library", "bom-ref": purl, "name": entry["name"], "version": entry["version"], "purl": purl,
              "licenses": [{"license": {"name": entry["license"]}}],
              "properties": [{"name": "actiongate:license-evidence", "value": entry.get("evidence", "metadata unavailable")}]}
    if entry.get("source"):
        result["externalReferences"] = [{"type": "distribution", "url": entry["source"]}]
    hashes = [{"alg": "SHA-256", "content": value.removeprefix("sha256:")} for value in entry.get("hashes", []) if value.startswith("sha256:")]
    if (entry.get("integrity") or "").startswith("sha512-"):
        hashes.append({"alg": "SHA-512", "content": base64.b64decode(entry["integrity"][7:]).hex()})
    if hashes:
        result["hashes"] = hashes
    return result


def container_components(root):
    # Include exact pinned deployment/build image references without inspecting
    # environment values or credentials from Docker configuration.
    paths = {root / "compose.yaml", root / "Dockerfile"}
    paths.update(root.glob("compose*.yaml"))
    for folder in ("services", "runtime", "deploy"):
        paths.update(path for path in (root / folder).rglob("Dockerfile*") if path.is_file())
        paths.update(path for path in (root / folder).rglob("*.yaml") if path.is_file())
        paths.update(path for path in (root / folder).rglob("*.yml") if path.is_file())
    sources = [path.read_text() for path in sorted(paths)]
    references = set()
    for source in sources:
        references.update(re.findall(r"(?:image:\s*|FROM\s+)([a-zA-Z0-9_./:-]+@sha256:[a-f0-9]{64})", source))
    results = []
    for reference in sorted(references):
        name, checksum = reference.split("@sha256:")
        results.append({"type": "container", "bom-ref": "actiongate:image:"+reference,
            "name": name, "version": "sha256:"+checksum,
            "hashes": [{"alg": "SHA-256", "content": checksum}],
            "properties": [{"name": "actiongate:inventory-scope", "value": "pinned image identity; OS package licenses retained upstream"}]})
    return results


def archive_files(root):
    folders = SOURCE_DIRECTORIES | {"artifacts", SELECTED_SPEC_ROOT}
    files = ROOT_SOURCE_FILES
    excluded = {".venv", "node_modules", "__pycache__", ".pytest_cache", ".hypothesis", "private", "dist", "playwright-report", "test-results", ".state", ".build"}
    for folder, children, names in os.walk(root):
        current = Path(folder)
        children[:] = sorted(name for name in children if name not in excluded and
            (current != root or name in folders) and not (current/name).is_symlink())
        for name in sorted(names):
            path = current/name
            relative = path.relative_to(root)
            if path.is_symlink() or not (relative.parts[0] in folders or relative.as_posix() in files):
                continue
            if relative.parts[0] == SELECTED_SPEC_ROOT and relative.as_posix() not in SELECTED_SPEC_FILES:
                continue
            if path.suffix.lower() in {".pyc", ".pyo", ".key", ".pem", ".zip", ".gguf"} or name in {"release-manifest.json", "SHA256SUMS"}:
                continue
            if name.startswith(".env") and name != ".env.example":
                continue
            yield path


def git_metadata(root):
    """Revision context only. Per-file digests always identify the release bytes."""
    result = {"available": False, "commit": None, "working_tree_dirty": None, "changes": []}
    try:
        status = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root, capture_output=True, text=True, timeout=20)
        if status.returncode != 0:
            return result
        result["available"] = True
        pieces = iter(status.stdout.split("\0"))
        for item in pieces:
            if not item:
                continue
            change = {"status": item[:2], "path": item[3:]}
            if "R" in item[:2] or "C" in item[:2]:
                change["previous_path"] = next(pieces)
            result["changes"].append(change)
        result["working_tree_dirty"] = bool(result["changes"])
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5)
        if revision.returncode == 0:
            result["commit"] = revision.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def source_changes(git_state, paths, root):
    sources = {path.relative_to(root).as_posix() for path in paths if path.relative_to(root).parts[0] != "artifacts"}
    changes = []
    for change in git_state["changes"]:
        names = [change["path"], change.get("previous_path", "")]
        if any(name in sources or ("D" in change["status"] and (name in ROOT_SOURCE_FILES or name in SELECTED_SPEC_FILES or name.split("/", 1)[0] in SOURCE_DIRECTORIES)) for name in names):
            changes.append(change)
    return changes


def acceptance_binding(root):
    path = root/"artifacts/acceptance-report.json"
    if not path.exists():
        return {"status": "not_run", "fresh": False}
    report = json.loads(path.read_text(encoding="utf-8"))
    mismatches = []
    for name, expected in report.get("evidence", {}).items():
        target = (root/name).resolve()
        if not target.is_relative_to(root) or not target.is_file() or sha(target) != expected["sha256"]:
            mismatches.append(name)
    required = [check for check in report.get("checks", []) if check.get("required")]
    fresh = bool(required) and all(check.get("status") == "passed" for check in required) and not mismatches and bool(report.get("evidence"))
    return {"status": report.get("status"), "sha256": sha(path), "fresh": fresh,
            "required_verification_status": report.get("required_verification_status", report.get("status")),
            "performance_targets_met": report.get("performance_targets_met"), "status_scope": report.get("status_scope"),
            "mismatched_evidence": mismatches, "required_passed": report.get("required_passed"), "required_total": report.get("required_total")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--refresh-licenses", action="store_true")
    parser.add_argument("--archive", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "artifacts"
    team = json.loads((root / "TEAM.json").read_text(encoding="utf-8-sig"))
    if not team.get("team_name") or not team.get("members"):
        raise RuntimeError("Confirmed team attribution is required")
    entries = python_inventory(root, output, args.refresh_licenses) + npm_inventory(root, output)
    runtimes = runtime_inventory(root, output, args.refresh_licenses)
    selected_model = json.loads((root/"models/model-manifest.json").read_text())
    selected_gpu_libraries = gpu_libraries(selected_model)
    unknown = [f"{entry['name']}=={entry['version']}" for entry in entries if entry["license"] == "NOT_DECLARED"]
    inventory = {"created_at": datetime.now(timezone.utc).isoformat(), "team": team,
        "scope": "all exact resolved uv/npm/runtime lock entries, runtime licenses and model manifest; OS image packages retain their upstream notices",
        "dependencies": entries, "runtime_licenses": runtimes, "optional_gpu_libraries": selected_gpu_libraries,
        "unknown_license_metadata": unknown,
        "model": selected_model}
    write_json(output / "dependency-inventory.json", inventory)
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": "urn:uuid:"+str(uuid.uuid4()), "version": 1,
        "metadata": {"timestamp": inventory["created_at"], "component": {"type": "application", "name": "ActionGate", "version": "0.1.0"},
            "authors": [{"name": member} for member in team["members"]]}, "components": [component(entry) for entry in entries]+container_components(root)}
    for library in selected_gpu_libraries:
        bom["components"].append({"type": "library", "bom-ref": "actiongate:optional-gpu:"+library["name"]+":"+library["version"],
            "name": library["name"], "version": library["version"], "licenses": [{"license": {"name": "NVIDIA CUDA Toolkit EULA",
                "url": "https://docs.nvidia.com/cuda/archive/"+library["toolkit"]+"/eula/index.html"}}],
            "externalReferences": [{"type": "distribution", "url": "https://developer.download.nvidia.com/compute/cuda/redist/redistrib_"+library["toolkit"]+".json"}],
            "properties": [{"name": "actiongate:inventory-scope", "value": "optional GPU target; exact libraries in the pinned Ollama source image, not application dependencies or files shipped in the source ZIP"}]})
    model = inventory["model"]
    bom["components"].append({"type": "machine-learning-model", "name": model["model"], "version": model["digest"],
        "bom-ref": "actiongate:local-model:"+model["digest"], "licenses": [{"license": {"id": "Apache-2.0"}}],
        "externalReferences": [{"type": "distribution", "url": model["source"]}],
        "hashes": [{"alg": "SHA-256", "content": model["digest"].removeprefix("sha256:")} ]})
    write_json(output / "sbom.cdx.json", bom)
    lines = ["# Third-party notices", "", f"Prepared for {team['team_name']}. Team member: {', '.join(team['members'])}.", "",
        "Exact Python and JavaScript versions below come from uv.lock, runtime/requirements.lock and ui/package-lock.json. License declarations come from the installed locked distributions or exact-version publisher metadata. Preserved license and notice texts are in artifacts/licenses. This inventory distinguishes model weights from their inference runner.", "",
        "The submission archive contains application source, model/tokenizer manifests, reports and notices. Container base images and model weights are fetched from pinned upstream references by bootstrap; their existing license and OS package notices remain in the original distributions. The application-level CycloneDX inventory does not claim to enumerate every OS package inside those images.", "",
        "## Model and runtime licenses", "", "| Component | License | Primary source |", "| --- | --- | --- |"]
    for runtime in runtimes:
        lines.append(f"| {runtime['name']} | {runtime['license']} | [Upstream license]({runtime['source']}) |")
    lines += ["", f"The optional Groq adapter calls a hosted service and does not redistribute its model weights. Provider service terms apply separately. The prepared local model `{model['model']}` and its pinned tokenizer use {model['license']}; `{model['runtime']}` uses MIT. Exact model, tokenizer and runtime references come from models/model-manifest.json.", "",
        "The optional GPU image uses NVIDIA CUDA libraries governed by the CUDA Toolkit EULA and supplement. Their exact versions were observed in the pinned Ollama source image and checked against NVIDIA's versioned redistributable catalogs. The source ZIP includes references and notices, not the CUDA binaries or container image. The CPU target does not copy these CUDA backend directories.", "",
        "| Optional GPU library | Version | Toolkit / primary version evidence |", "| --- | --- | --- |"]
    for library in selected_gpu_libraries:
        lines.append(f"| {library['name']} | {library['version']} | [CUDA {library['toolkit']} catalog](https://developer.download.nvidia.com/compute/cuda/redist/redistrib_{library['toolkit']}.json) |")
    lines += ["",
        "## Locked application dependencies", "", "| Ecosystem | Package | Version | License declaration |", "| --- | --- | --- | --- |"]
    for entry in sorted(entries, key=lambda x: (x["ecosystem"], x["name"], x["version"])):
        license_text = entry["license"].replace("|", " ").replace("\n", " ")
        lines.append(f"| {entry['ecosystem']} | {entry['name']} | {entry['version']} | {license_text} |")
    lines += ["", "## Attribution and redistribution", "",
        "Preserve upstream copyright, license and NOTICE files when redistributing dependencies. Dependencies declaring LGPL, GPL, MPL or other reciprocal terms retain those terms; inspect their preserved notices and corresponding upstream source before distributing modified binaries. This application inventory does not relicense third-party software.", "",
        "Unknown declarations: " + (", ".join(unknown) if unknown else "none in the resolved application lockfiles") + ".", ""]
    (root / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")
    git_state = git_metadata(root)
    report_paths = sorted(output.glob("*.json")) + sorted((output / "reports").glob("*.json"))
    reports = {}
    for path in report_paths:
        if path.name in {"release-manifest.json", "dependency-inventory.json", "sbom.cdx.json"}:
            continue
        try:
            report = json.loads(path.read_text())
            if "status" in report or "suite" in report:
                reports[path.relative_to(root).as_posix()] = {"status": report.get("status", "not_declared"), "sha256": sha(path)}
        except (ValueError, UnicodeError):
            pass
    paths = list(archive_files(root))
    changed_sources = source_changes(git_state, paths, root)
    acceptance = acceptance_binding(root)
    verified = acceptance["status"] == "passed" and acceptance["fresh"]
    if args.archive and acceptance["status"] == "passed":
        if not acceptance["fresh"]:
            raise RuntimeError("Acceptance evidence changed after aggregation; regenerate the acceptance report before archiving")
        if not git_state["available"] or not git_state["commit"] or changed_sources:
            raise RuntimeError("Verified submission requires a local source commit with no changed build inputs; generated evidence may remain outside that commit")
    manifest = {"product": "ActionGate", "team": team, "created_at": inventory["created_at"], "git_commit": git_state["commit"],
        "source_identity": "git_commit records the source revision context; exact per-file SHA-256 hashes are authoritative for all archived bytes",
        "git_working_tree": git_state, "source_changes_after_commit": changed_sources,
        "release_state": "submission-prepared" if verified and args.archive else "verified-local" if verified else "candidate",
        "submitted_or_uploaded": False, "acceptance": acceptance, "evidence": reports,
        "files": [{"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha(path)} for path in paths]}
    write_json(output / "release-manifest.json", manifest)
    if args.archive:
        destination = output / "actiongate-submission.zip"
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in paths + [output / "release-manifest.json"]:
                archive.write(path, path.relative_to(root).as_posix())
        sums = f"{sha(destination)}  actiongate-submission.zip\n{sha(output / 'release-manifest.json')}  release-manifest.json\n{sha(output / 'sbom.cdx.json')}  sbom.cdx.json\n"
        (output / "SHA256SUMS").write_text(sums, encoding="ascii")
    print(json.dumps({"dependencies": len(entries), "unknown_license_metadata": unknown,
        "runtime_license_failures": [r["name"] for r in runtimes if "error" in r or "status" in r],
        "files_in_release": len(paths), "archive_created": args.archive, "team": team["team_name"]}))


if __name__ == "__main__":
    main()
