import asyncio
import os
from pathlib import Path
import pytest


@pytest.fixture(scope="session")
def platform():
    """Real PostgreSQL, publisher and OPA. Missing dependencies fail acceptance."""
    if not os.getenv("DATABASE_URL"):
        pytest.fail("DATABASE_URL is required. Run scripts/verify.ps1 in the prepared Compose environment.")
    from actiongate import policies
    from actiongate.seed import seed
    asyncio.run(policies.initialize())
    seed()
    return policies.snapshot()


@pytest.fixture
def actor(platform):
    from actiongate.db import Principal, transaction, uid
    principal = "contract:" + uid()
    with transaction() as db:
        db.add(Principal(id=principal, tenant="synthetic_test_tenant", role="analyst"))
    return {"sub": principal, "tenant": "synthetic_test_tenant", "role": "analyst"}


@pytest.fixture
def workflow_run(actor):
    from actiongate.broker import create_run
    from actiongate.contracts import RunRequest
    return create_run(RunRequest(document_ids=["supplier-synthetic_test_tenant-1"], allow_publish=True), actor)


@pytest.fixture
def controlled_models(monkeypatch):
    """Contract upstream, explicitly not the all-local semantic quality test."""
    from actiongate.semantic import SemanticGuard
    from actiongate.runtime import BusinessModel
    from actiongate import broker
    monkeypatch.setattr(broker, "EXECUTION_MODE", "contract-controlled-models")
    calls = {"guard": 0, "business": 0}
    async def scan(self, *args, **kwargs):
        calls["guard"] += 1
        return {"verdict": "benign", "risk_level": 0, "category": "contract_forced_benign", "complete": True,
                "generation": args[4], "usage": {"total_tokens": 25, "inference_slot_seconds": .001}, "model_digest": "contract-fixture"}
    async def reserve(self, *args, **kwargs):
        return {"ticket_id": "contract", "windows": 1}
    async def release(self, *args):
        return None
    async def chat(self, messages, **kwargs):
        calls["business"] += 1
        return {"id": "contract-completion", "object": "chat.completion", "model": "local-business",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Supplier review completed."}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 30, "inference_slot_seconds": .001}}
    monkeypatch.setattr(SemanticGuard, "scan", scan)
    monkeypatch.setattr(SemanticGuard, "reserve_output", reserve)
    monkeypatch.setattr(SemanticGuard, "release_output", release)
    monkeypatch.setattr(BusinessModel, "chat", chat)
    return calls
