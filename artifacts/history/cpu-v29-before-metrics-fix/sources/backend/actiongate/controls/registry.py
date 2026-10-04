from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import jsonschema
from pydantic import Field, model_validator

from .schema import Level, StrictModel
from .signed import ControlError, bounded_structure, digest

LEVELS = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}


@dataclass(frozen=True)
class Label:
    level: str = "CONFIDENTIAL"
    compartments: frozenset[str] = field(default_factory=frozenset)
    origins: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        if self.level not in LEVELS:
            raise ControlError("Unknown confidentiality level")

    def join(self, other: "Label") -> "Label":
        return Label(max((self.level, other.level), key=LEVELS.get),
                     self.compartments | other.compartments, self.origins | other.origins)

    def flows_to(self, max_label: str, compartments: set[str] | None = None) -> bool:
        return max_label in LEVELS and LEVELS[self.level] <= LEVELS[max_label] and self.compartments.issubset(compartments or set())


class ToolDefinition(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,79}$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=2000)
    input_schema: dict[str, Any]
    resource_type: str
    recipient: str
    connector_identity: str
    effect: Literal["read", "write", "publish", "compute", "model"]
    redact_fields: list[str] = Field(default_factory=list)
    idempotency: Literal["read_only", "ledger_id", "none"]

    @model_validator(mode="after")
    def safe_schema(self):
        bounded_structure(self.input_schema)
        if self.input_schema.get("type") != "object" or self.input_schema.get("additionalProperties") is not False:
            raise ValueError("Tool schema must be a closed object")
        def visit(value):
            if isinstance(value, dict):
                if "$ref" in value or "$dynamicRef" in value:
                    raise ValueError("External and recursive schema references are forbidden")
                for v in value.values():
                    visit(v)
            elif isinstance(value, list):
                for v in value:
                    visit(v)
        visit(self.input_schema)
        jsonschema.Draft202012Validator.check_schema(self.input_schema)
        if any(not pointer.startswith("/") for pointer in self.redact_fields):
            raise ValueError("Redaction fields must be JSON pointers")
        return self

    @property
    def definition_digest(self) -> str:
        return digest(self.model_dump())

    def validate(self, arguments: dict):
        bounded_structure(arguments)
        try:
            jsonschema.Draft202012Validator(self.input_schema).validate(arguments)
        except jsonschema.ValidationError as exc:
            raise ControlError("Tool arguments do not match the approved schema") from exc
        if self.name == "models.chat":
            validate_chat_messages(arguments["messages"])


def validate_chat_messages(messages: list[dict]):
    """Preserve complete function-call turns and reject orphan/replayed results.

    This validates a conversation as data. It neither executes functions nor
    treats a reported tool result as an authorization or provenance assertion.
    """
    from .signed import load_json
    pending, seen = set(), set()
    for message in messages:
        role = message["role"]
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise ControlError("Tool result does not match an unresolved assistant call")
            pending.remove(call_id)
        else:
            if pending:
                raise ControlError("Assistant function calls require all corresponding tool results")
            for call in message.get("tool_calls") or []:
                if call["id"] in seen:
                    raise ControlError("Tool call identifiers must be unique within the conversation")
                if not isinstance(load_json(call["function"]["arguments"], max_bytes=65536), dict):
                    raise ControlError("Function arguments must be a complete JSON object")
                seen.add(call["id"])
                pending.add(call["id"])
    if pending:
        raise ControlError("Conversation ends with unresolved function calls")


class ModelDefinition(StrictModel):
    id: str
    version: int = Field(ge=1)
    provider: Literal["ollama", "groq"]
    model: str
    purpose: Literal["business", "guard"]
    recipient: Literal["local_model", "cloud_model"]
    context_tokens: int = Field(ge=512, le=2_000_000)
    manifest: str
    pricing_ref: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def provider_contract(self):
        if self.provider == "groq":
            if (self.id != "cloud-business" or self.model != "openai/gpt-oss-20b" or self.purpose != "business"
                    or self.recipient != "cloud_model" or self.context_tokens != 131072 or not self.pricing_ref):
                raise ValueError("Cloud model must match the approved isolated adapter contract")
        elif self.recipient != "local_model":
            raise ValueError("Local model must use the registered local recipient")
        if self.purpose == "guard" and self.provider != "ollama":
            raise ValueError("Semantic guard must remain local")
        return self


def typed_calculate(expression: str) -> float:
    """A tiny arithmetic interpreter. No eval, Python AST execution or imports."""
    import re
    if len(expression) > 256:
        raise ControlError("Calculator expression exceeds limit")
    compact = re.sub(r"\s+", "", expression)
    tokens = re.findall(r"[0-9]+(?:\.[0-9]+)?|[()+*/-]", compact)
    if "".join(tokens) != compact or not tokens or len(tokens) > 100:
        raise ControlError("Calculator only accepts decimal arithmetic")
    position = 0

    def atom(depth):
        nonlocal position
        if depth > 16 or position >= len(tokens):
            raise ControlError("Invalid calculator expression")
        token = tokens[position]
        position += 1
        if token in ("+", "-"):
            return atom(depth + 1) * (-1 if token == "-" else 1)
        if token == "(":
            value = expression_value(depth + 1)
            if position >= len(tokens) or tokens[position] != ")":
                raise ControlError("Unbalanced calculator parentheses")
            position += 1
            return value
        try:
            value = float(token)
        except ValueError as exc:
            raise ControlError("Invalid arithmetic operand") from exc
        return value

    def product(depth):
        nonlocal position
        value = atom(depth)
        while position < len(tokens) and tokens[position] in ("*", "/"):
            operator = tokens[position]
            position += 1
            right = atom(depth)
            if operator == "/" and right == 0:
                raise ControlError("Division by zero")
            value = value * right if operator == "*" else value / right
            if not math.isfinite(value) or abs(value) > 1e100:
                raise ControlError("Calculator result outside supported range")
        return value

    def expression_value(depth):
        nonlocal position
        value = product(depth)
        while position < len(tokens) and tokens[position] in ("+", "-"):
            operator = tokens[position]
            position += 1
            right = product(depth)
            value = value + right if operator == "+" else value - right
        return value

    result = expression_value(0)
    if position != len(tokens) or not math.isfinite(result) or abs(result) > 1e100:
        raise ControlError("Invalid or oversized calculator expression")
    return result
