"""Canonical signed data, bounded parsers, and monotonic external rule feeds."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import Field, model_validator

from .schema import StrictModel


class ControlError(ValueError):
    """Safe public error; never include an untrusted value in its message."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def bounded_structure(value: Any, *, max_depth: int = 24, max_nodes: int = 20000) -> None:
    pending, seen = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        seen += 1
        if depth > max_depth or seen > max_nodes:
            raise ControlError("Document exceeds structural limits")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ControlError("Object keys must be strings")
            try:
                for key in item:
                    key.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise ControlError("Object keys must contain valid Unicode") from exc
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise ControlError("Text must contain valid Unicode") from exc
        elif isinstance(item, float) and not math.isfinite(item):
            raise ControlError("Non-finite number")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ControlError("Duplicate object key")
        result[key] = value
    return result


def load_json(data: bytes | str, *, max_bytes: int = 1_048_576) -> Any:
    try:
        if len(data if isinstance(data, bytes) else data.encode("utf-8", errors="strict")) > max_bytes:
            raise ControlError("Document exceeds byte limit")
        # json.loads(bytes) otherwise uses surrogatepass and accepts invalid
        # UTF-8 that cannot later be signed, encrypted or sent to a worker.
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="strict")
        value = json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ControlError("Non-finite number")))
        bounded_structure(value)
        return value
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ControlError("Invalid JSON document") from exc


class _UniqueSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ControlError("YAML aliases are not permitted")
        depth = getattr(self, "_depth", 0)
        if depth > 24:
            raise ControlError("YAML exceeds depth limit")
        self._depth = depth + 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _yaml_mapping(loader, node, deep=False):
    return _pairs([(loader.construct_object(k, deep=deep), loader.construct_object(v, deep=deep)) for k, v in node.value])


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def load_yaml(data: str) -> dict:
    try:
        if len(data.encode("utf-8", errors="strict")) > 262144:
            raise ControlError("Policy exceeds byte limit")
        value = yaml.load(data, Loader=_UniqueSafeLoader)
        bounded_structure(value)
        if not isinstance(value, dict):
            raise ControlError("Policy must be a mapping")
        return value
    except (yaml.YAMLError, UnicodeError, RecursionError, TypeError) as exc:
        raise ControlError("Invalid policy YAML") from exc


def sign_document(payload: dict, private_key_b64: str, key_id: str) -> dict:
    raw = base64.b64decode(private_key_b64, altchars=b"-_", validate=True)
    key = Ed25519PrivateKey.from_private_bytes(raw)
    # Bind key identifier and algorithm as well as payload to the signature.
    protected = {"key_id": key_id, "algorithm": "Ed25519", "payload": payload}
    return {**protected, "signature": base64.b64encode(key.sign(canonical_json(protected))).decode("ascii")}


def verify_document(envelope: dict, public_keys: dict[str, str]) -> dict:
    if set(envelope) != {"payload", "key_id", "algorithm", "signature"} or envelope.get("algorithm") != "Ed25519":
        raise ControlError("Invalid signed envelope")
    if envelope["key_id"] not in public_keys:
        raise ControlError("Unknown signing key")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_keys[envelope["key_id"]], validate=True))
        protected = {k: envelope[k] for k in ("key_id", "algorithm", "payload")}
        key.verify(base64.b64decode(envelope["signature"], validate=True), canonical_json(protected))
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise ControlError("Invalid document signature") from exc
    if not isinstance(envelope["payload"], dict):
        raise ControlError("Signed payload must be an object")
    bounded_structure(envelope["payload"])
    return envelope["payload"]


class FeedRule(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    scope: Literal["text", "artifact", "tool"]
    selector: Literal["text", "name", "package", "version", "sha256", "source", "format", "description"]
    operator: Literal["equals", "in", "version_range", "re2"]
    value: str | list[str]
    action: Literal["block"] = "block"
    source: str = Field(max_length=512)
    package: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def valid_operator(self):
        if self.operator == "in":
            if not isinstance(self.value, list) or len(self.value) > 100 or not self.value:
                raise ValueError("Membership rules require one to 100 values")
            if any(len(v) > 512 for v in self.value):
                raise ValueError("Rule value too long")
        elif not isinstance(self.value, str) or len(self.value) > 512:
            raise ValueError("Rule requires a bounded string")
        if self.operator == "version_range":
            if self.selector != "version" or not self.package:
                raise ValueError("Version rule requires an explicit package")
            try:
                SpecifierSet(self.value)
            except InvalidSpecifier as exc:
                raise ValueError("Invalid version range") from exc
        if self.operator == "re2":
            try:
                import re2
                options = re2.Options()
                options.max_mem = 1_048_576
                options.log_errors = False
                re2.compile(self.value, options=options)
            except ImportError as exc:
                raise ValueError("RE2 is required for regular-expression feed rules") from exc
            except Exception as exc:
                raise ValueError("Invalid bounded RE2 rule") from exc
        return self


class SignedFeed(StrictModel):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=1)
    publisher: str = Field(min_length=1, max_length=80)
    issued_at: str = Field(max_length=40)
    expires_at: str = Field(max_length=40)
    rules: list[FeedRule] = Field(max_length=256)

    @model_validator(mode="after")
    def unique(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Duplicate rule ID")
        return self

    def check_freshness(self, now: datetime | None = None, max_age_hours: int = 24) -> None:
        now = now or datetime.now(timezone.utc)
        try:
            issued = datetime.fromisoformat(self.issued_at.replace("Z", "+00:00"))
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if issued.tzinfo is None or expires.tzinfo is None:
                raise ValueError("Timezone required")
            if issued > now + timedelta(seconds=30) or expires <= now or expires <= issued:
                raise ControlError("Feed is not currently valid")
            if now - issued > timedelta(hours=max_age_hours) or expires - issued > timedelta(hours=max_age_hours):
                raise ControlError("Feed exceeds permitted age")
        except ValueError as exc:
            raise ControlError("Invalid feed validity period") from exc

    def match(self, scope: str, attributes: dict[str, Any]) -> list[str]:
        matches = []
        for rule in self.rules:
            if rule.scope != scope or (rule.package is not None and attributes.get("package") != rule.package):
                continue
            value = attributes.get(rule.selector)
            if not isinstance(value, str):
                continue
            if rule.operator == "equals":
                hit = value == rule.value
            elif rule.operator == "in":
                hit = value in rule.value
            elif rule.operator == "version_range":
                try:
                    hit = Version(value) in SpecifierSet(rule.value)
                except InvalidVersion:
                    hit = True  # An unparseable artifact version cannot prove admission.
            else:
                import re2
                options = re2.Options()
                options.max_mem = 1_048_576
                options.log_errors = False
                hit = re2.compile(rule.value, options=options).search(value) is not None
            if hit:
                matches.append(rule.id)
        return matches


def validate_feed(envelope: dict | bytes | str, public_keys: dict[str, str], *, minimum_revision: int = 0,
                  publisher: str = "approved-threat-feed", max_age_hours: int = 24,
                  now: datetime | None = None) -> SignedFeed:
    if not isinstance(envelope, dict):
        envelope = load_json(envelope)
    payload = verify_document(envelope, public_keys)
    try:
        feed = SignedFeed.model_validate(payload)
    except ValueError as exc:
        raise ControlError("Invalid feed schema or rule") from exc
    if feed.revision <= minimum_revision:
        raise ControlError("Feed revision rollback or replay")
    if feed.publisher != publisher:
        raise ControlError("Unexpected feed publisher")
    feed.check_freshness(now, max_age_hours)
    return feed
