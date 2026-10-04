"""Cache isolation, bounded retention, deep-copy and honest zero-cost hits."""
import pytest

from actiongate.semantic_cache import SemanticCache, ZERO_USAGE


def context():
    return dict(tenant="tenant-a", text="exact synthetic content", purpose="authorized supplier review",
        effect={"tool": "reports.save"}, label=2, origins=["document"], compartments=["procurement"],
        generation=3, policy_digest="signed-policy", model_digest="sha256:approved-model",
        model_manifest_sha256="manifest", guard_artifact={"prompt_version": "test", "sha256": "prompt"},
        run_purpose="supplier_review", revocation_epoch=1, label_version=2)


def verdict():
    return {"verdict": "benign", "risk_level": 0, "complete": True, "reason": "Ordinary supplier fact",
        "generation": 3, "model_digest": "sha256:approved-model", "evidence": [],
        "usage": {"total_tokens": 24, "inference_slot_seconds": 1.2, "cpu_seconds": .3},
        "windows": [{"verdict": "benign", "usage": {"total_tokens": 24, "upstream_timings": {"total_seconds": 1.1}}}]}


def put(cache, key, value=None):
    return cache.put(key, value or verdict(), operation_id="prior-operation", generation=3,
        model_digest="sha256:approved-model", guard_artifact=context()["guard_artifact"])


@pytest.mark.parametrize("change", [
    {"tenant": "tenant-b"}, {"text": "different content"}, {"purpose": "other purpose"},
    {"effect": {"tool": "reports.publish_demo"}}, {"label": 3}, {"origins": ["agent"]},
    {"compartments": ["finance"]}, {"generation": 4}, {"policy_digest": "new-signed-policy"},
    {"model_digest": "sha256:new-model"}, {"model_manifest_sha256": "new-manifest"},
    {"guard_artifact": {"prompt_version": "new", "sha256": "new-prompt"}},
    {"run_purpose": "other-workflow"}, {"revocation_epoch": 2}, {"label_version": 3},
], ids=["tenant", "content", "purpose", "effect", "label", "origins", "compartments", "generation", "policy", "model", "model-manifest", "prompt", "workflow-purpose", "revocation", "label-version"])
def test_semantic_cache_never_crosses_context_or_version(change):
    cache = SemanticCache()
    key = cache.key(**context())
    assert put(cache, key)
    assert cache.get(cache.key(**{**context(), **change})) is None
    assert context()["text"] not in key and context()["tenant"] not in key and len(key) == 64


def test_semantic_cache_returns_deep_copy_and_zeroes_every_usage_level():
    cache = SemanticCache()
    key = cache.key(**context())
    original = verdict()
    assert put(cache, key, original)
    first = cache.get(key)
    assert first["cache_hit"] and first["cache_source"]["operation_id"] == "prior-operation"
    assert first["usage"] == first["windows"][0]["usage"] == ZERO_USAGE
    assert original["usage"]["total_tokens"] == 24
    first["windows"][0]["usage"]["total_tokens"] = 999
    first["reason"] = "caller mutation"
    again = cache.get(key)
    assert again["reason"] == original["reason"] and again["windows"][0]["usage"] == ZERO_USAGE


def test_semantic_cache_expires_and_enforces_count_and_byte_limits():
    now = [10.0]
    cache = SemanticCache(ttl_seconds=2, max_entries=1, max_bytes=2000, max_entry_bytes=1800, clock=lambda: now[0])
    key = cache.key(**context())
    assert put(cache, key)
    second = cache.key(**{**context(), "text": "second"})
    assert put(cache, second) and cache.get(key) is None
    assert cache.info()["entries"] == 1 and cache.info()["serialized_bytes"] <= 2000
    too_big = {**verdict(), "reason": "x"*2000}
    assert not put(cache, key, too_big)
    now[0] = 12.0
    assert cache.get(second) is None and cache.info()["serialized_bytes"] == 0


@pytest.mark.parametrize("change", [{"complete": False}, {"verdict": "unknown"},
    {"usage": {"total_tokens": 10, "usage_unknown": True}}, {"usage": {}},
    {"model_digest": "unapproved"}, {"generation": 4}], ids=["incomplete", "unknown", "unknown-cost", "missing-cost", "model", "generation"])
def test_semantic_cache_does_not_store_unknown_or_unbound_judgments(change):
    cache = SemanticCache()
    key = cache.key(**context())
    assert not put(cache, key, {**verdict(), **change})
    assert cache.get(key) is None


@pytest.mark.integration
async def test_cached_output_releases_real_ledger_reservation_without_double_cost(actor, monkeypatch):
    """Controlled inference in a private cache; actual PostgreSQL ledger settlement."""
    from actiongate import broker, ledger, policies, semantic_cache
    from actiongate.contracts import RunRequest
    from actiongate.db import Run, Reservation, BudgetAccount, transaction, uid
    from actiongate.semantic import SemanticGuard
    private_cache = SemanticCache()
    monkeypatch.setattr(semantic_cache, "cache", private_cache)
    # This private cache is discarded with the test; fixtures never enter the
    # process cache used by the separate real-quality suite.
    monkeypatch.setattr(broker, "EXECUTION_MODE", "local")
    snapshot = policies.snapshot()
    assert snapshot["configuration"]["performance"]["semantic_cache_enabled"] is True
    archived = policies.verify_stored_snapshot(snapshot)
    model_digest = archived["model_artifacts"]["local-guard"]["manifest"]["digest"]
    calls = []
    async def scan(self, *args, **kwargs):
        calls.append(args)
        return {**verdict(), "generation": snapshot["generation"], "model_digest": model_digest}
    monkeypatch.setattr(SemanticGuard, "scan", scan)
    run_info = broker.create_run(RunRequest(document_ids=[]), actor)
    with transaction() as db:
        run = db.get(Run, run_info["id"])
    first = await broker.semantic_scan("cache-ledger-contract", run, uid(), snapshot)
    assert first["usage"]["total_tokens"] == 24 and not first["cache_hit"]
    reservation = ledger.reserve(run.id, uid(), "guard.output", snapshot["configuration"], tokens=100, slot_millis=1000, generation=snapshot["generation"])
    second = await broker.semantic_scan("cache-ledger-contract", run, uid(), snapshot, reservation_id=reservation, ticket_id="owned-output-ticket")
    assert len(calls) == 1 and second["cache_hit"] and second["usage"] == ZERO_USAGE
    with transaction() as db:
        assert db.get(Reservation, reservation).status == "released"
        assert db.get(Reservation, reservation).usage == ZERO_USAGE
        account = db.get(BudgetAccount, f"root:{run.id}:tokens")
        assert account.spent == 24 and account.reserved == 0
    await broker.semantic_scan("cache-ledger-contract", run, uid(), snapshot, use_cache=False)
    assert len(calls) == 2
    monkeypatch.setattr(broker, "EXECUTION_MODE", "contract-controlled-models")
    await broker.semantic_scan("cache-ledger-contract", run, uid(), snapshot)
    assert len(calls) == 3
