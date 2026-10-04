"""Stage a whole signed generation, then switch one PostgreSQL fence."""
import asyncio
import difflib
import json
import os
import hashlib
import copy
from functools import lru_cache
from pathlib import Path
import httpx
from sqlalchemy import select, func, update
from .db import State, PolicyGeneration, transaction
from .settings import settings
from .security import audit, digest


def policy_directory():
    return settings()["policy_path"].parent


def validate_yaml(source):
    from .controls import PolicyConfig, ControlPlane, load_yaml
    if len(source.encode()) > 100_000:
        raise ValueError("Policy exceeds its size limit")
    raw = load_yaml(source)
    cfg = PolicyConfig.model_validate(raw)
    from .semantic import PROMPT_VERSION
    if cfg.semantic.prompt_version != PROMPT_VERSION:
        raise ValueError("Classifier prompt version is not supported by this deployment")
    ControlPlane(policy_directory(), config=cfg, require_feed=False, scanner=shared_scanner())
    return cfg.model_dump(mode="json")


def snapshot(*, require_fresh=True, allow_legacy=False):
    with transaction() as db:
        # One statement sees the fence and its generation in the same database
        # snapshot. Separate READ COMMITTED queries could observe a superseded
        # generation after a publication between those queries.
        active = db.execute(select(State, PolicyGeneration).join(
            PolicyGeneration, PolicyGeneration.id == State.generation).where(State.id == 1)).one_or_none()
        if active is None or active[1].status != "active":
            raise RuntimeError("No complete active generation")
        state, row = active
        result = {"generation": row.id, "configuration": row.configuration, "yaml": row.yaml,
                "feed": row.feed, "digest": row.digest, "signature": row.signature,
                "revocation_epoch": state.revocation_epoch, "kill_switch": state.kill_switch}
    verify_stored_snapshot(result, require_fresh=require_fresh, allow_legacy=allow_legacy)
    return result


@lru_cache(maxsize=1)
def shared_scanner():
    from .controls import DLPScanner
    return DLPScanner()


@lru_cache(maxsize=32)
def _verified_envelope(envelope, keys):
    from .controls import validate_snapshot, load_json
    return validate_snapshot(load_json(envelope), json.loads(keys), require_fresh_feed=False)


def verify_stored_snapshot(snap, *, require_fresh=True, allow_legacy=False):
    from .controls import validate_snapshot, load_json
    if not allow_legacy and snap["configuration"].get("performance", {}).get("control_cache_enabled", True):
        verified = copy.deepcopy(_verified_envelope(snap["signature"], json.dumps(public_keys("POLICY"), sort_keys=True)))
        if require_fresh and verified.get("feed"):
            from .controls import SignedFeed
            SignedFeed.model_validate(verified["feed"]).check_freshness(max_age_hours=verified["policy"]["feed"]["max_age_hours"])
    else:
        verified = validate_snapshot(load_json(snap["signature"]), public_keys("POLICY"), require_fresh_feed=require_fresh, allow_legacy=allow_legacy)
    if (verified["generation"] != snap["generation"] or verified["policy"] != snap["configuration"]
            or verified["feed"] != snap["feed"] or verified["snapshot_digest"] != snap["digest"]):
        raise ValueError("Persisted generation differs from its signed control snapshot")
    source = verified.get("rego_source")
    if source is not None and hashlib.sha256(source.encode()).hexdigest() != verified["rego_sha256"]:
        raise ValueError("Stored Rego differs from the approved generation")
    return verified


def _make_plane(encoded):
    from .controls import ControlPlane
    verified = json.loads(encoded)
    # Registries are taken from the signed generation, never mutable files.
    return ControlPlane(policy_directory(), config=verified["policy"], feed=verified["feed"],
                        tools=verified["tools"], models=verified["models"], scanner=shared_scanner(), prices=verified.get("prices"), model_artifacts=verified["model_artifacts"])


_compiled_plane = lru_cache(maxsize=32)(_make_plane)


def plane_for(snap):
    verified = verify_stored_snapshot(snap)
    encoded = json.dumps(verified, sort_keys=True, separators=(",", ":"))
    return (_compiled_plane if verified["policy"].get("performance", {}).get("control_cache_enabled", True)
            else _make_plane)(encoded)


async def publisher_call(method, path, payload=None):
    from .settings import secret
    url = os.getenv("PUBLISHER_URL", "http://publisher:8020")
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        response = await client.request(method, url + path, json=payload,
            headers={"Authorization": "Bearer " + secret("ACTIONGATE_CONNECTOR_KEY")})
        response.raise_for_status()
        return response.json()


def public_keys(kind):
    value = os.getenv(f"ACTIONGATE_{kind}_PUBLIC_KEY")
    path = os.getenv(f"ACTIONGATE_{kind}_PUBLIC_KEY_FILE")
    if path:
        value = Path(path).read_text().strip()
    if not value:
        raise RuntimeError(f"Missing anchored {kind} verification key")
    return {f"demo-{kind.lower()}-v1": value}


async def verified_feed(envelope=None, minimum_revision=0):
    from .controls.signed import validate_feed
    envelope = envelope or await publisher_call("GET", "/feed")
    feed = validate_feed(envelope, public_keys("FEED"), minimum_revision=minimum_revision)
    return feed.model_dump(mode="json") if hasattr(feed, "model_dump") else feed.payload


async def stage(generation, config, rego_source=None):
    from .controls import OPAClient, PolicyConfig
    from .runtime import WorkerClient
    urls = os.getenv("OPA_REPLICA_URLS", settings()["opa_url"]).split(",")
    result = await asyncio.gather(
        *(OPAClient(url).stage(generation, PolicyConfig.model_validate(config),
          policy_directory() / "control.rego", rego_source=rego_source) for url in urls),
        WorkerClient("guard").configure(generation, config),
        WorkerClient("business").configure(generation, config))
    return result


async def activate(source, tenant, *, feed=None, expected_generation=None):
    from .db import root_boundary
    # A dedicated session lock serializes staging without holding a money transaction.
    with root_boundary("actiongate-policy-publisher"):
        return await _activate_locked(source, tenant, feed=feed, expected_generation=expected_generation)


async def _activate_locked(source, tenant, *, feed=None, expected_generation=None):
    from .controls import ControlPlane, validate_snapshot
    config = validate_yaml(source)
    old = snapshot(require_fresh=False, allow_legacy=True)
    if expected_generation is not None and old["generation"] != expected_generation:
        raise ValueError("Active generation changed; validate again")
    feed = feed or old["feed"]
    equivalent = ControlPlane(policy_directory(), config=config, feed=feed, scanner=shared_scanner()).snapshot(old["generation"])
    if source == old["yaml"] and equivalent["snapshot_digest"] == old["digest"]:
        return {"generation": old["generation"], "status": "active", "digest": old["digest"], "unchanged": True}
    with transaction() as db:
        generation = (db.scalar(select(func.max(PolicyGeneration.id))) or 0) + 1
    payload = ControlPlane(policy_directory(), config=config, feed=feed, scanner=shared_scanner()).snapshot(generation)
    envelope = await publisher_call("POST", "/policy/sign", {"payload": payload})
    verified = validate_snapshot(envelope, public_keys("POLICY"), minimum_generation=old["generation"])
    if verified != payload:
        raise ValueError("Publisher returned a different policy")
    # An attempted generation is never reused, including partially staged
    # attempts. Components retain immutable snapshots even if another rejects.
    with transaction() as db:
        db.add(PolicyGeneration(id=generation, configuration=config, yaml=source, digest=payload["snapshot_digest"],
            signature=json.dumps(envelope), feed=feed, status="staging"))
    try:
        await stage(generation, config, payload.get("rego_source"))
        from .controls.model_artifacts import verify_worker_artifacts
        await verify_worker_artifacts(payload)
        # Persist desired source before the active fence. The last active
        # generation survives a failure and a later retry gets a fresh ID.
        await publisher_call("POST", "/policy/source", {"yaml": source, "generation": generation})
    except Exception:
        with transaction() as db:
            db.get(PolicyGeneration, generation).status = "rejected"
        raise
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        if state.generation != old["generation"]:
            raise ValueError("Concurrent publication; retry with current generation")
        db.execute(update(PolicyGeneration).where(PolicyGeneration.status == "active").values(status="superseded"))
        db.get(PolicyGeneration, generation).status = "active"
        state.generation = generation
        from .db import BudgetAccount
        budgets, local = config["budgets"], config["local_resources"]
        for account in db.scalars(select(BudgetAccount).order_by(BudgetAccount.id).with_for_update()):
            factor = 100 if account.scope in ("tenant", "user") else 1
            if account.unit == "usd_micros":
                account.limit = budgets["tenant_daily_usd_micros"] if factor == 100 else budgets["run_usd_micros"]
            elif account.unit == "tokens":
                account.limit = budgets["guard_subbudget_tokens"] if account.scope == "guard" else budgets["run_total_tokens"] * factor
            elif account.unit == "slot_millis":
                account.limit = (local["guard_subbudget_slot_seconds"] if account.scope == "guard" else local["run_slot_seconds"] * factor) * 1000
        audit(db, tenant, "policy.activated", {"generation": generation, "digest": payload["snapshot_digest"]})
    return {"generation": generation, "status": "active", "digest": payload["snapshot_digest"]}


async def initialize():
    from .db import root_boundary
    from .controls import ControlPlane, validate_snapshot
    with root_boundary("actiongate-policy-publisher"):
        with transaction() as db:
            if db.get(State, 1) is None:
                db.add(State(id=1))
                db.flush()
            active = db.get(PolicyGeneration, db.get(State, 1).generation)
            exists = active is not None and active.status == "active"
            generation = (db.scalar(select(func.max(PolicyGeneration.id))) or 0) + 1
        if not exists:
            source = settings()["policy_path"].read_text(encoding="utf-8")
            config = validate_yaml(source)
            feed = await verified_feed()
            payload = ControlPlane(policy_directory(), config=config, feed=feed, scanner=shared_scanner()).snapshot(generation)
            envelope = await publisher_call("POST", "/policy/sign", {"payload": payload})
            validate_snapshot(envelope, public_keys("POLICY"))
            with transaction() as db:
                db.add(PolicyGeneration(id=generation, configuration=config, yaml=source, feed=feed,
                    digest=payload["snapshot_digest"], signature=json.dumps(envelope), status="staging"))
            try:
                await stage(generation, config, payload.get("rego_source"))
                from .controls.model_artifacts import verify_worker_artifacts
                await verify_worker_artifacts(payload)
            except Exception:
                with transaction() as db:
                    db.get(PolicyGeneration, generation).status = "rejected"
                raise
            with transaction() as db:
                state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
                db.get(PolicyGeneration, generation).status = "active"
                state.generation = generation
    current = snapshot(require_fresh=False, allow_legacy=True)
    legacy = verify_stored_snapshot(current, require_fresh=False, allow_legacy=True)
    if ("model_artifacts" not in legacy or "guard_artifact" not in legacy
            or "semantic_cache_enabled" not in legacy["policy"].get("performance", {})):
        # A valid historical signature authorizes migration of the old policy,
        # never admission with an unbound classifier or mutable model manifest.
        renewed = await verified_feed(minimum_revision=current["feed"]["revision"] - 1)
        desired_source = settings()["policy_path"].read_text(encoding="utf-8")
        await activate(desired_source, "synthetic_test_tenant", feed=renewed, expected_generation=current["generation"])
        current = snapshot()
    try:
        verify_stored_snapshot(current)
    except Exception:
        renewed = await verified_feed(minimum_revision=current["feed"]["revision"])
        desired_source = settings()["policy_path"].read_text(encoding="utf-8")
        await activate(desired_source, "synthetic_test_tenant", feed=renewed,
            expected_generation=current["generation"])
        current = snapshot()
    verify_stored_snapshot(current)
    source = settings()["policy_path"].read_text(encoding="utf-8")
    candidate = ControlPlane(policy_directory(), config=validate_yaml(source), feed=current["feed"], scanner=shared_scanner()).snapshot(current["generation"])
    if source != current["yaml"] or candidate["snapshot_digest"] != current["digest"]:
        # A replaced worker cannot acknowledge the previous model's manifest.
        # Activate the desired complete signed snapshot first. activate() stages
        # and verifies its actual workers before committing the generation fence.
        try:
            await activate(source, "synthetic_test_tenant", expected_generation=current["generation"])
        except BlockingIOError:
            pass
        current = snapshot()
    archived = verify_stored_snapshot(current)
    await stage(current["generation"], current["configuration"], archived.get("rego_source"))
    from .controls.model_artifacts import verify_worker_artifacts
    await verify_worker_artifacts(archived)


def diff(source):
    return "\n".join(difflib.unified_diff(snapshot()["yaml"].splitlines(), source.splitlines(),
                                          fromfile="active", tofile="candidate", lineterm=""))


publication_state = {"source": "file", "status": "watching", "last_error": None}
feed_state = {"status": "unknown", "last_error": None}
component_state = {"status": "unknown", "last_error": None}


def source_digest():
    files = [settings()["policy_path"], policy_directory()/"control.rego", *policy_directory().glob("*-registry.json"), *policy_directory().glob("prices.json")]
    from .settings import ROOT
    files += [ROOT/"models/model-manifest.json", ROOT/"models/cloud-model-manifest.json"]
    return digest({path.name: path.read_text(encoding="utf-8") for path in files})


async def watch_configuration():
    last_seen = source_digest()
    while True:
        await asyncio.sleep(1)
        try:
            current = source_digest()
            if current == last_seen:
                continue
            source = settings()["policy_path"].read_text(encoding="utf-8")
            publication_state.update(status="validating", desired_digest=current)
            # A competing replica may already have published the same source.
            snap = snapshot()
            from .controls import ControlPlane
            candidate = ControlPlane(policy_directory(), config=validate_yaml(source), feed=snap["feed"], scanner=shared_scanner()).snapshot(snap["generation"])
            if snap["yaml"] != source or snap["digest"] != candidate["snapshot_digest"]:
                await activate(source, "synthetic_test_tenant", expected_generation=snap["generation"])
            publication_state.update(status="active", last_error=None, active_generation=snapshot()["generation"])
            last_seen = current
        except BlockingIOError:
            continue
        except Exception as exc:
            publication_state.update(status="rejected", last_error=type(exc).__name__)
            last_seen = current


async def watch_feed():
    while True:
        await asyncio.sleep(5)
        try:
            old = snapshot(require_fresh=False)
            envelope = await publisher_call("GET", "/feed")
            revision = envelope.get("payload", {}).get("revision", -1)
            if revision > old["feed"]["revision"]:
                fresh = await verified_feed(envelope, old["feed"]["revision"])
                await activate(old["yaml"], "synthetic_test_tenant", feed=fresh, expected_generation=old["generation"])
            elif revision < old["feed"]["revision"]:
                raise ValueError("Publisher attempted feed rollback")
            feed_state.update(status="available", last_error=None)
        except BlockingIOError:
            continue
        except Exception as exc:
            feed_state.update(status="unavailable", last_error=type(exc).__name__)


async def watch_components():
    """A restarted component must recover its signed generation before admission."""
    from .controls import OPAClient
    from .runtime import WorkerClient
    while True:
        await asyncio.sleep(5)
        try:
            snap = snapshot()
            opa_urls = os.getenv("OPA_REPLICA_URLS", settings()["opa_url"]).split(",")
            opa_ready = await asyncio.gather(*(OPAClient(url).ready(snap["generation"]) for url in opa_urls))
            workers = await asyncio.gather(WorkerClient("guard").ready(), WorkerClient("business").ready())
            from .controls.model_artifacts import verify_worker_artifacts
            archived = verify_stored_snapshot(snap)
            await verify_worker_artifacts(archived, workers)
            complete = all(opa_ready) and all(snap["generation"] in worker.get("prepared_generations", []) for worker in workers)
            if not complete:
                archived = verify_stored_snapshot(snap)
                await stage(snap["generation"], snap["configuration"], archived.get("rego_source"))
            component_state.update(status="ready", generation=snap["generation"], last_error=None)
        except Exception as exc:
            component_state.update(status="unavailable", last_error=type(exc).__name__)
