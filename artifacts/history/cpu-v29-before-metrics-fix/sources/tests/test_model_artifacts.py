"""A policy signature must bind the model, tokenizer and provider contract."""
import copy
from pathlib import Path

import pytest

from actiongate.controls import ControlError, ControlPlane, digest
from actiongate.controls.model_artifacts import load_model_artifacts, validate_model_artifacts, verify_worker_artifacts

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def artifact_plane():
    return ControlPlane(ROOT / "policy", require_feed=False)


def workers_for(artifacts):
    workers = {}
    for role in ("guard", "business"):
        artifact = artifacts["local-" + role]
        manifest = artifact["manifest"]
        workers[role] = {"role": role, "model": manifest["model"], "digest": manifest["digest"],
            "tokenizer_sha256": manifest["tokenizer_sha256"], "model_manifest_sha256": artifact["sha256"],
            "context_tokens": manifest["context_tokens"], "max_input_tokens": manifest["max_input_tokens"],
            "runtime": manifest["runtime"]}
    return workers


def test_snapshot_contains_complete_local_and_cloud_artifacts(artifact_plane):
    payload = artifact_plane.snapshot(10)
    assert set(payload["model_artifacts"]) == {"local-business", "local-guard", "cloud-business"}
    assert payload["model_artifacts"]["local-guard"]["manifest"]["tokenizer_sha256"]
    assert payload["model_artifacts"]["cloud-business"]["manifest"]["automatic_retries"] is False
    assert validate_model_artifacts(payload["models"], payload["model_artifacts"])


@pytest.mark.parametrize("path", ["../models/model-manifest.json", "/tmp/manifest.json", "models/other.json", "models\\model-manifest.json"])
def test_manifest_paths_cannot_escape_the_prepared_allowlist(artifact_plane, path):
    models = [item.model_dump() for item in artifact_plane.models.values()]
    models[0]["manifest"] = path
    with pytest.raises(ControlError, match="path"):
        load_model_artifacts(models)


def test_replay_uses_signed_artifacts_without_reading_mutable_files(artifact_plane, monkeypatch):
    from actiongate.controls import plane
    artifacts = copy.deepcopy(artifact_plane.model_artifacts)
    monkeypatch.setattr(plane, "load_model_artifacts", lambda _: pytest.fail("Replay read mutable manifests"))
    replay = ControlPlane(ROOT / "policy", config=artifact_plane.config, require_feed=False,
        tools=[item.model_dump() for item in artifact_plane.tools.values()],
        models=[item.model_dump() for item in artifact_plane.models.values()],
        model_artifacts=artifacts, scanner=artifact_plane.scanner)
    assert replay.model_artifacts == artifacts


async def test_prepared_workers_acknowledge_the_signed_artifacts(artifact_plane):
    payload = artifact_plane.snapshot(10)
    assert await verify_worker_artifacts(payload, workers_for(payload["model_artifacts"]))


@pytest.mark.parametrize("observed", [None, "ollama:0.18.2", "ollama:0.32.1"])
async def test_upgraded_runtime_requires_exact_actual_version(artifact_plane, observed):
    payload = artifact_plane.snapshot(10)
    for name in ("local-guard", "local-business"):
        artifact = payload["model_artifacts"][name]
        artifact["manifest"]["runtime"] = "ollama:0.32.0"
        artifact["sha256"] = digest(artifact["manifest"])
    workers = workers_for(payload["model_artifacts"])
    workers["business"]["runtime"] = "ollama:0.32.0"
    if observed is not None:
        workers["guard"]["runtime"] = observed
    else:
        workers["guard"].pop("runtime")
    with pytest.raises(ControlError, match="inference runtime"):
        await verify_worker_artifacts(payload, workers)
    workers["guard"]["runtime"] = "ollama:0.32.0"
    assert await verify_worker_artifacts(payload, workers)


def test_unprepared_runtime_version_is_rejected_even_with_correct_hash(artifact_plane):
    altered = copy.deepcopy(artifact_plane.model_artifacts)
    altered["local-guard"]["manifest"]["runtime"] = "ollama:latest"
    altered["local-guard"]["sha256"] = digest(altered["local-guard"]["manifest"])
    with pytest.raises(ControlError):
        validate_model_artifacts(list(artifact_plane.models.values()), altered)


@pytest.mark.parametrize("field", ["digest", "tokenizer_sha256", "model_manifest_sha256", "context_tokens", "max_input_tokens"])
async def test_worker_drift_fails_the_signed_deployment_fence(artifact_plane, field):
    payload = artifact_plane.snapshot(10)
    workers = workers_for(payload["model_artifacts"])
    workers["guard"][field] = "different" if "sha256" in field or field == "digest" else 1
    with pytest.raises(ControlError, match="Running worker"):
        await verify_worker_artifacts(payload, workers)


def test_manifest_content_hash_and_full_coverage_are_required(artifact_plane):
    models = list(artifact_plane.models.values())
    altered = copy.deepcopy(artifact_plane.model_artifacts)
    altered["local-guard"]["manifest"]["tokenizer_sha256"] = "0" * 64
    with pytest.raises(ControlError, match="digest"):
        validate_model_artifacts(models, altered)
    altered = copy.deepcopy(artifact_plane.model_artifacts)
    del altered["local-guard"]
    with pytest.raises(ControlError, match="complete registry"):
        validate_model_artifacts(models, altered)


@pytest.mark.parametrize(("field", "value"), [("temperature", 0.7), ("temperature", 0.0), ("seed", 43), ("think", True)])
def test_signed_generation_parameters_must_match_the_actual_worker(artifact_plane, field, value):
    altered = copy.deepcopy(artifact_plane.model_artifacts)
    altered["local-guard"]["manifest"][field] = value
    altered["local-guard"]["sha256"] = digest(altered["local-guard"]["manifest"])
    with pytest.raises(ControlError, match="generation parameters"):
        validate_model_artifacts(list(artifact_plane.models.values()), altered)


@pytest.mark.parametrize("publication", ["accepted", "rejected", "concurrent_completed", "concurrent_pending"])
async def test_restart_with_replaced_model_activates_signed_upgrade_before_old_worker_check(monkeypatch, tmp_path, publication):
    """Reproduce an unchanged YAML file whose pinned model has been replaced."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from actiongate import policies, controls, db
    from actiongate.controls import model_artifacts
    source = tmp_path/"control.yaml"
    source.write_text("desired signed configuration", encoding="utf-8")
    common = {"yaml": source.read_text(), "configuration": {"prepared": True}, "feed": {"revision": 1}}
    old = {**common, "generation": 7, "digest": "previous-model"}
    new = {**common, "generation": 8, "digest": "prepared-model"}
    active = [old]
    calls = []

    class ExistingDatabase:
        def get(self, model, key):
            return SimpleNamespace(generation=7) if model is db.State else SimpleNamespace(status="active")
        def scalar(self, statement):
            return 7

    @contextmanager
    def transaction():
        yield ExistingDatabase()

    @contextmanager
    def publisher_lock(*args):
        yield

    class DesiredPlane:
        def __init__(self, *args, **kwargs):
            pass
        def snapshot(self, generation):
            return {"snapshot_digest": new["digest"]}

    def verify(snap, **kwargs):
        return {"model_artifacts": {"prepared": snap["digest"]}, "guard_artifact": {},
                "policy": {"performance": {"semantic_cache_enabled": True}}, "rego_source": "signed rego"}

    async def activate(desired, tenant, **kwargs):
        calls.append("signed-activation")
        assert desired == source.read_text() and kwargs["expected_generation"] == 7
        if publication == "rejected":
            raise ControlError("Desired workers do not match signed candidate")
        if publication != "concurrent_pending":
            active[0] = new
        if publication.startswith("concurrent_"):
            raise BlockingIOError("Other replica owns publication")

    async def stage(generation, config, rego):
        calls.append(("stage", generation))

    async def verify_workers(payload):
        calls.append("actual-worker-check")
        if payload["model_artifacts"]["prepared"] != new["digest"]:
            raise ControlError("Running worker differs from the signed model")

    monkeypatch.setattr(policies, "transaction", transaction)
    monkeypatch.setattr(db, "root_boundary", publisher_lock)
    monkeypatch.setattr(policies, "snapshot", lambda **kwargs: active[0])
    monkeypatch.setattr(policies, "verify_stored_snapshot", verify)
    monkeypatch.setattr(policies, "settings", lambda: {"policy_path": source})
    monkeypatch.setattr(policies, "validate_yaml", lambda text: common["configuration"])
    monkeypatch.setattr(policies, "shared_scanner", lambda: None)
    monkeypatch.setattr(controls, "ControlPlane", DesiredPlane)
    monkeypatch.setattr(policies, "activate", activate)
    monkeypatch.setattr(policies, "stage", stage)
    monkeypatch.setattr(model_artifacts, "verify_worker_artifacts", verify_workers)
    if publication in {"accepted", "concurrent_completed"}:
        await policies.initialize()
        assert calls == ["signed-activation", ("stage", 8), "actual-worker-check"]
        assert active[0]["generation"] == 8
    else:
        with pytest.raises(ControlError):
            await policies.initialize()
        assert calls[0] == "signed-activation" and active[0]["generation"] == 7
        if publication == "rejected":
            assert calls == ["signed-activation"]
