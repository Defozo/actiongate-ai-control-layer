"""Scan the final ZIP and selected source bytes against project vault values.

Only the eleven ActionGate names and explicitly requested optional Groq key are
injected. Values, value hashes and matching snippets never leave this process.
The report is outside the ZIP to avoid a self-referential archive hash.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.parse
import zipfile

from release import archive_files
from runtime_bootstrap import NAMES


def source_inventory(root):
    paths = sorted(archive_files(root))
    manifest = root / "artifacts/release-manifest.json"
    if manifest.is_file():
        paths.append(manifest)
    entries = [(path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths]
    return paths, hashlib.sha256(json.dumps(sorted(entries), separators=(",", ":")).encode()).hexdigest()


def secret_patterns(values):
    patterns = {}
    for name, value in values.items():
        if not isinstance(value, str) or len(value) < 16:
            raise ValueError("Required project secret is missing or too short: " + name)
        raw = value.encode()
        candidates = {raw, value.encode("utf-16-le"), value.encode("utf-16-be"),
                      base64.b64encode(raw), base64.urlsafe_b64encode(raw),
                      urllib.parse.quote(value, safe="").encode(), json.dumps(value)[1:-1].encode()}
        # Signing/encryption entries may also have leaked as decoded key bytes.
        if name.endswith(("ENCRYPTION_KEY", "SPOOL_KEY", "SIGNING_KEY")):
            try:
                decoded = base64.urlsafe_b64decode(raw)
                if len(decoded) >= 16:
                    candidates.add(decoded)
            except (ValueError, base64.binascii.Error):
                pass
        patterns[name] = candidates
    return patterns


def scan_stream(stream, patterns):
    overlap = max(len(pattern) for variants in patterns.values() for pattern in variants) - 1
    tail, hits = b"", set()
    while chunk := stream.read(1024 * 1024):
        data = tail + chunk
        for name, variants in patterns.items():
            if any(pattern in data for pattern in variants):
                hits.add(name)
        tail = data[-overlap:] if overlap else b""
    return sorted(hits)


def scan(root, archive_path, values):
    patterns = secret_patterns(values)
    archive_before = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    paths, source_hash = source_inventory(root)
    offending = []
    source_bytes = 0
    for path in paths:
        source_bytes += path.stat().st_size
        with path.open("rb") as stream:
            offending.extend({"path": path.relative_to(root).as_posix(), "key_name": name, "scope": "source"}
                             for name in scan_stream(stream, patterns))
    with archive_path.open("rb") as stream:
        offending.extend({"path": archive_path.name, "key_name": name, "scope": "zip_raw"}
                         for name in scan_stream(stream, patterns))
    members = 0
    expanded_bytes = 0
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            members += 1
            expanded_bytes += item.file_size
            with archive.open(item) as stream:
                offending.extend({"path": item.filename, "key_name": name, "scope": "zip_member"}
                                 for name in scan_stream(stream, patterns))
    # Detect a source edit during scanning; archive bytes are bound separately.
    _, final_hash = source_inventory(root)
    archive_after = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return {"schema_version": 1, "status": "passed" if not offending and final_hash == source_hash and archive_before == archive_after else "failed",
        "created_at": datetime.now(timezone.utc).isoformat(), "mode": "actual-selective-vault-value-scan",
        "secret_names": sorted(values), "secret_count": len(values), "source_files": len(paths),
        "source_bytes": source_bytes, "source_inventory_sha256": source_hash, "source_stable": final_hash == source_hash,
        "archive_sha256": archive_after, "archive_stable": archive_before == archive_after, "zip_members": members,
        "expanded_zip_bytes": expanded_bytes, "offending": offending,
        "scope": "Exact vault values, UTF-16, base64, URL/JSON forms and decoded signing/encryption keys; source release inventory, raw ZIP bytes and every decompressed ZIP member. No values or snippets are recorded."}


def verify_report(root, archive_path, report):
    _, source_hash = source_inventory(root)
    names = set(report.get("secret_names", []))
    return (report.get("status") == "passed" and report.get("mode") == "actual-selective-vault-value-scan"
        and set(NAMES) <= names <= set(NAMES) | {"GROQ_API_KEY"}
        and report.get("secret_count") == len(names) and report.get("source_stable") is True and report.get("archive_stable") is True
        and report.get("offending") == [] and report.get("source_inventory_sha256") == source_hash
        and report.get("archive_sha256") == hashlib.sha256(archive_path.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cloud", action="store_true", help="Also selectively inject the optional Groq key")
    parser.add_argument("--injected", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.root.resolve()
    names = [*NAMES, *(["GROQ_API_KEY"] if args.cloud or os.getenv("GROQ_API_KEY") else [])]
    if not args.injected:
        executable = shutil.which("psst.cmd" if os.name == "nt" else "psst")
        if not executable:
            raise RuntimeError("psst is required for the actual selective secret scan")
        result = subprocess.run([executable, *names, "--", sys.executable, str(Path(__file__).resolve()),
                                 *sys.argv[1:], "--injected"], cwd=root, capture_output=True, text=True)
        # Only this scanner's final JSON is forwarded, never vault diagnostics.
        try:
            summary = json.loads(result.stdout.strip())
            print(json.dumps({key: summary[key] for key in ("status", "secret_count", "source_files", "zip_members", "archive_sha256", "offending")}))
        except (ValueError, KeyError):
            print(json.dumps({"status": "failed", "reason": "Selective injection or scan failed; no secret output retained"}))
            return 1
        return result.returncode
    values = {name: os.environ.get(name) for name in names}
    report = scan(root, root/"artifacts/actiongate-submission.zip", values)
    output = root/".state/release-secret-scan.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "secret_count", "source_files", "zip_members", "archive_sha256", "offending")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"status": "failed", "reason": "Secret scan did not complete; no diagnostic values emitted"}))
        raise SystemExit(1) from None
