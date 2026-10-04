"""One bounded content scanner shared by ingress, memory, tools, and egress."""
from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from .schema import PolicyConfig
from .signed import ControlError, SignedFeed, bounded_structure


@dataclass(frozen=True)
class Finding:
    entity: str
    start: int
    end: int
    rule_id: str


@dataclass
class ScanResult:
    decision: str = "allow"
    redacted_text: str = ""
    rule_ids: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    reason: str = "Content controls passed"
    findings: list[Finding] = field(default_factory=list, repr=False)

    def metadata(self) -> dict:
        """Audit-safe result: deliberately excludes original/redacted content and spans."""
        return {"decision": self.decision, "rule_ids": self.rule_ids, "entities": self.entities, "reason": self.reason}


def valid_pesel(value: str) -> bool:
    if len(value) != 11 or not value.isascii() or not value.isdecimal():
        return False
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    if (10 - sum(int(n) * w for n, w in zip(value, weights)) % 10) % 10 != int(value[-1]):
        return False
    # Reject impossible dates, as well as valid checksums of arbitrary numeric IDs.
    from datetime import date
    month = int(value[2:4])
    centuries = {0: 1900, 1: 2000, 2: 2100, 3: 2200, 4: 1800}
    century = month // 20
    if century not in centuries:
        return False
    try:
        date(centuries[century] + int(value[:2]), month % 20, int(value[4:6]))
        return True
    except ValueError:
        return False


def valid_iban(value: str) -> bool:
    compact = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", compact):
        return False
    # Country lengths from the IBAN registry for supported demo countries.
    lengths = {"PL": 28, "GB": 22, "DE": 22, "FR": 27, "NL": 18, "ES": 24, "IT": 27,
               "BE": 16, "AT": 20, "CH": 21, "DK": 18, "NO": 15, "SE": 24, "IE": 22,
               "PT": 25, "FI": 18, "CZ": 24, "SK": 24, "LU": 20, "LT": 20, "LV": 21, "EE": 20}
    if compact[:2] not in lengths or len(compact) != lengths[compact[:2]]:
        return False
    checksum = 0
    for char in compact[4:] + compact[:4]:
        for digit in (str(ord(char) - 55) if char.isalpha() else char):
            checksum = (checksum * 10 + int(digit)) % 97
    return checksum == 1


def normalize_with_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
    output, spans = [], []
    for index, char in enumerate(text):
        if unicodedata.category(char) in {"Cf", "Cc"} and char not in "\t\r\n":
            continue
        normalized = unicodedata.normalize("NFKC", char)
        output.append(normalized)
        spans.extend([(index, index + 1)] * len(normalized))
    return "".join(output), spans


class DLPScanner:
    def __init__(self):
        # Presidio recognizers are used directly. They do not require an NLP model,
        # download weights, or claim entity recognition beyond these patterns.
        try:
            from presidio_analyzer.predefined_recognizers import EmailRecognizer, PhoneRecognizer
            import tldextract
            # Use the package's frozen suffix data. Inference must remain offline.
            extractor = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
            class OfflineEmailRecognizer(EmailRecognizer):
                def validate_result(self, pattern_text):
                    return extractor(pattern_text).fqdn != ""
            self.email = OfflineEmailRecognizer()
            self.phone = PhoneRecognizer(supported_regions=["PL", "US", "GB", "DE"])
        except ImportError as exc:
            raise ControlError("Presidio analyzer is required for DLP") from exc
        self.secret_patterns = (
            ("secret.synthetic", re.compile(r"\b(?:AG_TEST_SECRET|ACTIONGATE_SECRET)_[A-Za-z0-9_-]{8,256}\b")),
            ("secret.api_key", re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,256}|gsk_[A-Za-z0-9]{16,128}|gh[pousr]_[A-Za-z0-9]{20,128})\b")),
            ("secret.aws", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
            ("secret.private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
            ("secret.bearer", re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9_.-]{20,4096}")),
            ("secret.credential_assignment", re.compile(r"(?i)\b(?:api[_-]?key|client[_-]?secret|password)[ \t]*[:=][ \t]*[\"']?[A-Za-z0-9_+/=-]{12,256}")),
        )

    def scan(self, text: str, config: PolicyConfig, feed: SignedFeed | None = None, *, tenant: str = "acme",
             scope: str = "text", profile: str | None = None, redact: bool = True) -> ScanResult:
        if not isinstance(text, str):
            raise ControlError("Content must be text")
        if len(text.encode("utf-8")) > max(config.transport.max_request_bytes, config.transport.max_response_bytes):
            return ScanResult("block", "", ["transport.content_limit"], [], "Content exceeds scanner limit")
        name = profile or config.active_profile
        if name not in {"strict", "balanced", "observe"}:
            raise ControlError("Unknown screening profile")
        current = getattr(config.profiles, name)
        if name == "observe" and tenant != current.restrict_to:
            return ScanResult("block", "", ["profile.observe_tenant"], [], "Observe is restricted to the synthetic test tenant")
        normalized, mapping = normalize_with_spans(text)
        findings: list[Finding] = []

        def add(entity, start, end, rule):
            if end > start and start < len(mapping):
                findings.append(Finding(entity, mapping[start][0], mapping[end - 1][1], rule))

        if config.controls.secrets.enabled:
            for rule, pattern in self.secret_patterns:
                for match in pattern.finditer(normalized):
                    add("SECRET", match.start(), match.end(), rule)
        if config.controls.pii.enabled:
            entities = config.controls.pii.entities
            if "EMAIL" in entities:
                for hit in self.email.analyze(normalized, ["EMAIL_ADDRESS"]):
                    add("EMAIL", hit.start, hit.end, "pii.email.presidio")
            if "PHONE" in entities:
                for hit in self.phone.analyze(normalized, ["PHONE_NUMBER"]):
                    add("PHONE", hit.start, hit.end, "pii.phone.presidio")
            if "PESEL" in entities:
                for match in re.finditer(r"(?<![0-9])[0-9]{11}(?![0-9])", normalized):
                    if valid_pesel(match.group()):
                        add("PESEL", match.start(), match.end(), "pii.pesel.checksum")
            if "IBAN" in entities:
                # The checksum decides validity. Explicit boundaries avoid matching a prefix
                # of a longer account identifier or redacting neighboring words.
                for match in re.finditer(r"\b[A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]){11,30}\b", normalized):
                    candidate = match.group()
                    if valid_iban(candidate):
                        add("IBAN", match.start(), match.end(), "pii.iban.checksum")
        rules = sorted({f.rule_id for f in findings})
        if feed is not None and config.controls.historical_attacks.enabled:
            try:
                feed.check_freshness(max_age_hours=config.feed.max_age_hours)
                matches = feed.match(scope, {"text": normalized})
            except ControlError:
                return ScanResult("block", "", ["feed.expired"], [], "Threat feed is not current")
            if matches:
                return ScanResult("block", "", sorted(set(rules + matches)), sorted({f.entity for f in findings}), "A signed threat rule blocked the content", findings)
        detected = sorted({f.entity for f in findings})
        if "SECRET" in detected:
            # Never return a copy of rejected credentials to an operation store.
            return ScanResult("block", "", rules, detected, "Credential material is prohibited", findings)
        if not findings:
            return ScanResult("allow", text)
        if name == "observe" and "pii" in current.advisory_controls:
            return ScanResult("allow", text, rules, detected, "PII observed in synthetic sandbox", findings)
        if current.pii_action == "block" or not redact:
            return ScanResult("block", "", rules + ([] if redact else ["pii.non_text_parameter"]), detected,
                              "PII is prohibited in this profile or effect parameter", findings)
        # Merge intersecting detections before replacing, so phone/IBAN overlap
        # cannot accidentally retain some bytes of an identifier.
        spans: list[list[Any]] = []
        for finding in sorted(findings, key=lambda f: (f.start, -f.end)):
            if spans and finding.start < spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], finding.end)
                spans[-1][2].add(finding.entity)
            else:
                spans.append([finding.start, finding.end, {finding.entity}])
        redacted = text
        for start, end, kinds in reversed(spans):
            redacted = redacted[:start] + "[REDACTED:" + "+".join(sorted(kinds)) + "]" + redacted[end:]
        return ScanResult("redact", redacted, rules, detected, "PII redacted from an approved text field", findings)

    def scan_payload(self, payload: dict, config: PolicyConfig, feed: SignedFeed | None = None, *,
                     redact_fields: set[str] | list[str], tenant: str = "acme", scope: str = "text") -> tuple[dict | None, ScanResult]:
        """Only exact JSON pointers can be redacted. Every key and value is scanned."""
        bounded_structure(payload)
        allowed = set(redact_fields)
        result = ScanResult("allow", "")
        output = copy.deepcopy(payload)
        blocked = False

        def visit(value: Any, path: str):
            nonlocal blocked
            if isinstance(value, str):
                found = self.scan(value, config, feed, tenant=tenant, scope=scope, redact=path in allowed)
                result.rule_ids.extend(found.rule_ids)
                result.entities.extend(found.entities)
                if found.decision == "block":
                    blocked = True
                    result.reason = found.reason
                    return ""
                if found.decision == "redact":
                    result.decision = "redact"
                return found.redacted_text
            if isinstance(value, dict):
                for key in list(value):
                    key_result = self.scan(key, config, feed, tenant=tenant, scope=scope, redact=False)
                    if key_result.decision == "block":
                        blocked = True
                        result.rule_ids.extend(key_result.rule_ids)
                    escaped = key.replace("~", "~0").replace("/", "~1")
                    value[key] = visit(value[key], path + "/" + escaped)
            if isinstance(value, list):
                for index, child in enumerate(value):
                    value[index] = visit(child, path + "/" + str(index))
            return value

        visit(output, "")
        result.rule_ids = sorted(set(result.rule_ids))
        result.entities = sorted(set(result.entities))
        if blocked:
            result.decision = "block"
            return None, result
        return output, result
