"""Validate every archived byte against its manifest, without extracting files."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import sys
import zipfile

from release import SELECTED_SPEC_FILES, SELECTED_SPEC_ROOT


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    path = root / "artifacts/actiongate-submission.zip"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise RuntimeError("Archive contains duplicate names or invalid CRC")
        for name in names:
            item = PurePosixPath(name)
            if item.is_absolute() or ".." in item.parts or "\\" in name:
                raise RuntimeError("Archive contains a noncanonical path")
            if {".venv", ".psst", ".state", ".build", "node_modules", "private"} & set(item.parts):
                raise RuntimeError("Archive contains a prohibited private/runtime directory")
            if item.suffix in {".key", ".pem"} or (item.name.startswith(".env") and item.name != ".env.example"):
                raise RuntimeError("Archive contains a prohibited credential file")
            if name == "PLAN.md" or (item.parts[0] == SELECTED_SPEC_ROOT and name not in SELECTED_SPEC_FILES):
                raise RuntimeError("Archive contains an unselected planning document: "+name)
        manifest = json.loads(archive.read("artifacts/release-manifest.json"))
        if set(names) != {item["path"] for item in manifest["files"]} | {"artifacts/release-manifest.json"}:
            raise RuntimeError("Archive contents differ from the declared manifest inventory")
        for required in sorted(SELECTED_SPEC_FILES | {"Dockerfile", "compose.yaml", "runtime/requirements.lock", "migrations/env.py", "TEAM.json"}):
            if required not in names:
                raise RuntimeError("Release is missing a required build input: "+required)
        for item in manifest["files"]:
            data = archive.read(item["path"])
            if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise RuntimeError("Archive bytes do not match the frozen source manifest: "+item["path"])
        if manifest.get("release_state") in {"verified-local", "submission-prepared"}:
            if not manifest.get("git_commit") or manifest.get("source_changes_after_commit"):
                raise RuntimeError("Verified archive is not tied to a clean committed source revision")
            acceptance = json.loads(archive.read("artifacts/acceptance-report.json"))
            if acceptance.get("status") != "passed" or not manifest.get("acceptance", {}).get("fresh"):
                raise RuntimeError("Verified archive is missing passed current acceptance evidence")
            for name, expected in acceptance["evidence"].items():
                if name not in names or hashlib.sha256(archive.read(name)).hexdigest() != expected["sha256"]:
                    raise RuntimeError("Archived acceptance evidence differs from the aggregate: "+name)
        team = json.loads(archive.read("TEAM.json"))
        if manifest["team"] != team:
            raise RuntimeError("Submission team attribution differs from TEAM.json")
        from scan_release_secrets import verify_report
        report_path = root / ".state/release-secret-scan.json"
        if not report_path.is_file() or not verify_report(root, path, json.loads(report_path.read_text())):
            raise RuntimeError("Current source and ZIP require a passed actual selective-vault-value scan")
        print(json.dumps({"status": "passed", "verified_files": len(manifest["files"]),
                          "actual_secret_value_scan": "passed", "team": team["team_name"],
                          "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
