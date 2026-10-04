"""Source freeze must distinguish generated artifacts and stale acceptance."""
import hashlib
import importlib.util
import json
import io
import posixpath
import re
import sys
import zipfile
import pytest
from pathlib import Path

spec = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parents[1]/"scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_actual_secret_scan_checks_decoded_members_and_rejects_stale_evidence(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"scripts"))
    import scan_release_secrets as scanner
    values = {name: "synthetic-private-test-value-" + name for name in scanner.NAMES}
    secret = values[scanner.NAMES[0]]
    (tmp_path/"backend").mkdir()
    source = tmp_path/"backend/app.py"
    source.write_text("safe synthetic source")
    (tmp_path/"artifacts").mkdir()
    archive_path = tmp_path/"artifacts/actiongate-submission.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backend/app.py", "prefix" + secret + "suffix")
    report = scanner.scan(tmp_path, archive_path, values)
    assert report["status"] == "failed"
    assert {"path": "backend/app.py", "key_name": scanner.NAMES[0], "scope": "zip_member"} in report["offending"]
    assert secret not in json.dumps(report)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("backend/app.py", source.read_bytes())
    report = scanner.scan(tmp_path, archive_path, values)
    assert scanner.verify_report(tmp_path, archive_path, report)
    source.write_text("changed source")
    assert not scanner.verify_report(tmp_path, archive_path, report)
    source.write_text("safe synthetic source")
    with archive_path.open("ab") as stream:
        stream.write(b"changed ZIP")
    assert not scanner.verify_report(tmp_path, archive_path, report)


def test_secret_scan_finds_values_across_chunk_boundary_and_utf16():
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
    scanner = importlib.import_module("scan_release_secrets")
    value = "synthetic-private-test-value-0123456789"
    patterns = scanner.secret_patterns({"ACTIONGATE_AUTH_KEY": value})
    assert scanner.scan_stream(io.BytesIO(b"x"*(1024*1024-5)+value.encode()+b"suffix"), patterns) == ["ACTIONGATE_AUTH_KEY"]
    assert scanner.scan_stream(io.BytesIO(value.encode("utf-16-le")), patterns) == ["ACTIONGATE_AUTH_KEY"]


def test_secret_scan_rejects_archive_changed_during_inspection(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"scripts"))
    import scan_release_secrets as scanner
    archive_path = tmp_path/"package.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "safe")
    inventory = scanner.source_inventory
    calls = 0
    def mutate_after_inspection(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            with archive_path.open("ab") as stream:
                stream.write(b"different uninspected archive bytes")
        return inventory(root)
    monkeypatch.setattr(scanner, "source_inventory", mutate_after_inspection)
    report = scanner.scan(tmp_path, archive_path, {name: "synthetic-private-test-value-"+name for name in scanner.NAMES})
    assert report["status"] == "failed" and report["archive_stable"] is False
    assert not scanner.verify_report(tmp_path, archive_path, report)


def test_archive_excludes_build_helpers_and_private_files(tmp_path):
    for name in ("docs/.build/browser.json", "docs/images/proof.png", "runtime/worker.py", "artifacts/private/key.txt", ".env", ".env.example", "ui/node_modules/package/index.js"):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
    names = {path.relative_to(tmp_path).as_posix() for path in release.archive_files(tmp_path)}
    assert names == {"docs/images/proof.png", "runtime/worker.py", ".env.example"}


def test_archive_excludes_publication_clone_and_git_metadata_without_hiding_evidence(tmp_path):
    included = {"artifacts/reports/published.json", "artifacts/github-publication-20261004-summary.json",
        "docs/publication.md"}
    excluded = {"artifacts/github-publication-20261004/OWNER_REQUEST.json",
        "artifacts/github-publication-20261004/workspace/README.md",
        "artifacts/github-publication-20261004/workspace/.git/config",
        "artifacts/another-clone/.git/objects/pack/history.pack", "docs/worktree/.git"}
    for name in included | excluded:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic evidence or operational state")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in release.archive_files(tmp_path):
            archive.write(path, path.relative_to(tmp_path).as_posix())
    with zipfile.ZipFile(buffer) as archive:
        assert set(archive.namelist()) == included


@pytest.mark.parametrize("prohibited", ["artifacts/github-publication-20261004/OWNER_REQUEST.json",
    "artifacts/github-publication-20261004/workspace/README.md", "artifacts/clone/.git/config", "docs/worktree/.git"])
def test_release_checker_rejects_operational_clone_even_if_packager_is_bypassed(tmp_path, monkeypatch, prohibited):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"scripts"))
    import check_release
    (tmp_path/"artifacts").mkdir()
    with zipfile.ZipFile(tmp_path/"artifacts/actiongate-submission.zip", "w") as archive:
        archive.writestr(prohibited, "synthetic operational state")
    monkeypatch.setattr(sys, "argv", ["check_release.py", str(tmp_path)])
    with pytest.raises(RuntimeError, match="prohibited"):
        check_release.main()


def test_archive_preserves_selected_plan_reference_closure_without_unselected_state(tmp_path):
    selected = release.SELECTED_SPEC_ROOT
    for name in release.SELECTED_SPEC_FILES:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic selected specification")
    documents = {
        "README.md": f"[Current plan]({selected}/PLAN.md)",
        f"{selected}/PLAN.md": "[Task](TASK.json) [Materials](MATERIALS.md) [Alternatives](proposals/README.md)",
        f"{selected}/MATERIALS.md": "[Brief](materials/786a9bb4a858f98d.pdf) [Text](materials/786a9bb4a858f98d.pdf.txt)",
        f"{selected}/proposals/README.md": " ".join(f"[{proposal}]({proposal}/PLAN.md)" for proposal in "ABCD"),
        **{f"{selected}/proposals/{proposal}/PLAN.md": "[Services](SERVICES.md) [Brief](materials/786a9bb4a858f98d.pdf)" for proposal in "ABCD"}}
    excluded = ["PLAN.md", f"{selected}/.plans/scratch.md", f"{selected}/materials/download-cache.json",
        f"{selected}/proposals/E/PLAN.md", f"{selected}/materials/unreviewed.pdf"]
    for name, content in {**documents, **dict.fromkeys(excluded, "not selected")}.items():
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in release.archive_files(tmp_path):
            archive.write(path, path.relative_to(tmp_path).as_posix())
    with zipfile.ZipFile(buffer) as archive:
        names = set(archive.namelist())
        assert names == release.SELECTED_SPEC_FILES | {"README.md"}
        assert names.isdisjoint(excluded)
        for name, content in documents.items():
            assert archive.read(name).decode() == content
            for target in re.findall(r"\]\(([^)]+)\)", content):
                assert posixpath.normpath(posixpath.join(posixpath.dirname(name), target)) in names


def test_source_freeze_ignores_generated_evidence_but_detects_edits_and_deletion(tmp_path):
    paths = [tmp_path/"scripts/release.py", tmp_path/"backend/actiongate/app.py", tmp_path/"artifacts/acceptance-report.json"]
    state = {"changes": [{"path": "artifacts/acceptance-report.json", "status": " M"},
        {"path": "scripts/release.py", "status": " M"}, {"path": "backend/actiongate/deleted.py", "status": " D"},
        {"path": "runtime/worker.py", "status": " D"}, {"path": "README.md", "status": " D"},
        {"path": "official-2026-10-03/PLAN.md", "status": " D"},
        {"path": "official-2026-10-03/proposals/B/materials/manifest.json", "status": " D"},
        {"path": "official-2026-10-03/materials/download-cache.json", "status": " D"}]}
    assert [item["path"] for item in release.source_changes(state, paths, tmp_path)] == ["scripts/release.py", "backend/actiongate/deleted.py", "runtime/worker.py", "README.md", "official-2026-10-03/PLAN.md", "official-2026-10-03/proposals/B/materials/manifest.json"]


def test_passed_acceptance_becomes_stale_when_any_bound_evidence_changes(tmp_path):
    (tmp_path/"artifacts").mkdir()
    evidence = tmp_path/"artifacts/actual.json"
    evidence.write_text('{"status":"passed"}')
    report = {"status": "passed", "checks": [{"required": True, "status": "passed"}],
        "evidence": {"artifacts/actual.json": {"sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}}}
    (tmp_path/"artifacts/acceptance-report.json").write_text(json.dumps(report))
    assert release.acceptance_binding(tmp_path)["fresh"]
    evidence.write_text('{"status":"failed"}')
    binding = release.acceptance_binding(tmp_path)
    assert not binding["fresh"] and binding["mismatched_evidence"] == ["artifacts/actual.json"]


def test_model_notices_follow_exact_model_tokenizer_and_runtime():
    commit = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
    model = {"model": "qwen3.5:4b", "license": "Apache-2.0", "runtime": "ollama:0.18.2",
        "tokenizer_commit": commit, "tokenizer_source": f"https://huggingface.co/Qwen/Qwen3.5-4B/resolve/{commit}/tokenizer.json"}
    sources = release.model_license_sources(model)
    assert [row[0] for row in sources] == ["Qwen3.5-4B", "Qwen3.5-4B-tokenizer", "ollama-0.18.2"]
    assert sources[0][2] == sources[1][2] == f"https://huggingface.co/Qwen/Qwen3.5-4B/resolve/{commit}/LICENSE"
    assert sources[2][2] == "https://raw.githubusercontent.com/ollama/ollama/v0.18.2/LICENSE"
    upgraded = {**model, "runtime": "ollama:0.32.0"}
    assert release.model_license_sources(upgraded)[2][2] == "https://raw.githubusercontent.com/ollama/ollama/v0.32.0/LICENSE"
    assert release.gpu_libraries(upgraded)[1] == {"name": "libcublas/libcublasLt", "version": "12.8.5.5", "toolkit": "12.8.2"}
    for alteration in ({"model": "unreviewed-model"}, {"tokenizer_commit": "different"}, {"runtime": "ollama:latest"}):
        with pytest.raises(RuntimeError):
            release.model_license_sources({**model, **alteration})
