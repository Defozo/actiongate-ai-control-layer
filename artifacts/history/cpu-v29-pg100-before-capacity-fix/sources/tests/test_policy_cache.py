"""Caching cannot extend freshness, trust modified rows, or mix generations."""
import base64
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

from actiongate import policies
from actiongate.controls import ControlPlane, SignedFeed, sign_document, digest


@pytest.fixture
def cached_snapshot(monkeypatch):
    monkeypatch.setattr(policies, "policy_directory", lambda: Path(__file__).resolve().parents[1]/"policy")
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(policies, "public_keys", lambda kind: {"cache-test": public})
    date = datetime.now(timezone.utc)
    feed = {"schema_version": 1, "revision": 1, "publisher": "approved-threat-feed", "issued_at": date.isoformat(),
            "expires_at": (date+timedelta(hours=1)).isoformat(), "rules": []}
    plane = ControlPlane(Path(__file__).resolve().parents[1]/"policy", feed=feed)
    def make(enabled=True, generation=51, legacy_semantic=False):
        plane.config.performance.control_cache_enabled = enabled
        payload = plane.snapshot(generation)
        if legacy_semantic:
            payload["policy"]["performance"].pop("semantic_cache_enabled")
            payload["snapshot_digest"] = digest({key: value for key, value in payload.items() if key != "snapshot_digest"})
        return {"generation": generation, "configuration": payload["policy"], "feed": payload["feed"],
            "digest": payload["snapshot_digest"], "signature": json.dumps(sign_document(payload, private, "cache-test"))}
    policies._verified_envelope.cache_clear()
    policies._compiled_plane.cache_clear()
    return make


def test_cache_toggle_and_generation_never_reuse_another_control_plane(cached_snapshot):
    on, off, next_generation = cached_snapshot(), cached_snapshot(False), cached_snapshot(generation=52)
    assert policies.plane_for(on) is policies.plane_for(on)
    assert policies.plane_for(off) is not policies.plane_for(off)
    assert policies.plane_for(on) is not policies.plane_for(next_generation)
    assert policies._compiled_plane.cache_info().maxsize == 32


def test_cache_rechecks_feed_expiry_after_prior_success(cached_snapshot, monkeypatch):
    snapshot = cached_snapshot()
    policies.plane_for(snapshot)
    def expired(self, *args, **kwargs):
        raise ValueError("Feed expired after cache was populated")
    monkeypatch.setattr(SignedFeed, "check_freshness", expired)
    with pytest.raises(ValueError, match="expired"):
        policies.plane_for(snapshot)


def test_cached_signature_does_not_trust_modified_database_or_returned_values(cached_snapshot):
    snapshot = cached_snapshot()
    result = policies.verify_stored_snapshot(snapshot)
    result["policy"]["tools"]["allowed"].clear()
    assert policies.verify_stored_snapshot(snapshot)["policy"]["tools"]["allowed"]
    changed = copy.deepcopy(snapshot)
    changed["configuration"]["active_profile"] = "strict"
    with pytest.raises(ValueError, match="Persisted generation"):
        policies.verify_stored_snapshot(changed)


def test_new_semantic_cache_default_never_rewrites_a_legacy_signed_snapshot(cached_snapshot):
    previous = cached_snapshot(legacy_semantic=True)
    verified = policies.verify_stored_snapshot(previous, allow_legacy=True)
    assert verified["policy"] == previous["configuration"]
    assert "semantic_cache_enabled" not in verified["policy"]["performance"]
    assert verified["snapshot_digest"] == previous["digest"]
