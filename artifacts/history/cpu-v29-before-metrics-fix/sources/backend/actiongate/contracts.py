from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(Strict):
    role: Literal["admin", "analyst", "approver", "manager"] = "analyst"
    tenant: Literal["acme", "globex", "synthetic_test_tenant"] = "acme"


class RunRequest(Strict):
    purpose: Literal["supplier_review"] = "supplier_review"
    document_ids: list[str] = Field(default_factory=lambda: ["supplier-acme-1"], max_length=20)
    allow_publish: bool = False
    parent_id: str | None = None
    tools: list[str] | None = None
    public_document_ids: list[str] = Field(default_factory=list, max_length=20)
    public_model_task: Literal["supplier_directory_summary"] | None = None


class ActionRequest(Strict):
    run_id: str = Field(min_length=1, max_length=80)
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any]
    idempotency_key: str = Field(min_length=1, max_length=128)


class ApprovalRequest(Strict):
    approved: bool
    payload_hash: str = Field(min_length=64, max_length=64)


class YamlRequest(Strict):
    yaml: str = Field(max_length=100_000)


class PlaygroundRequest(Strict):
    text: str = Field(min_length=1, max_length=65000)
    profile: Literal["strict", "balanced", "observe"] | None = None


class ScenarioRequest(Strict):
    scenario: Literal["legal", "pii", "injection", "cross_tenant", "public", "budget", "memory", "approval"]


class TestRequest(Strict):
    suite: Literal["contract", "all-local"] = "contract"


class ChatMessage(Strict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(Strict):
    model: str = "local-business"
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    stream: bool = False
    tools: list[dict] | None = None
    tool_choice: Literal["auto", "none"] | None = None
    temperature: Literal[0.0] | None = Field(default=None, description="Generation is pinned to zero temperature by the approved model deployment.")

    @field_validator("temperature", mode="before")
    @classmethod
    def pinned_temperature(cls, value):
        if value is not None and (type(value) not in (int, float) or value != 0):
            raise ValueError("This deployment supports only temperature=0")
        return value
