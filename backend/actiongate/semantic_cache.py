"""Bounded, process-local reuse of complete model judgments, never authority."""
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import threading
import time


ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
              "usd_micros": 0, "inference_slot_seconds": 0.0, "cpu_seconds": 0.0, "usage_unknown": False}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def without_prior_usage(value):
    """Strip all nested metering and timing, including per-window measurements."""
    if isinstance(value, list):
        return [without_prior_usage(item) for item in value]
    if isinstance(value, dict):
        return {key: dict(ZERO_USAGE) if key == "usage" else without_prior_usage(item)
                for key, item in value.items()
                if key not in {"cache_source", "cache_hit", "upstream_timings", "phase_timings_ms", "seconds", "latency_ms"}}
    return value


class SemanticCache:
    def __init__(self, *, ttl_seconds=120, max_entries=512, max_bytes=8*1024*1024,
                 max_entry_bytes=64*1024, clock=time.monotonic):
        if min(ttl_seconds, max_entries, max_bytes, max_entry_bytes) <= 0:
            raise ValueError("Cache limits must be positive")
        self.ttl_seconds, self.max_entries = ttl_seconds, max_entries
        self.max_bytes, self.max_entry_bytes, self.clock = max_bytes, max_entry_bytes, clock
        self._secret = secrets.token_bytes(32)
        self._entries, self._bytes, self._lock = OrderedDict(), 0, threading.RLock()

    def key(self, *, tenant, text, purpose, effect, label, origins, compartments,
            generation, policy_digest, model_digest, model_manifest_sha256, guard_artifact,
            run_purpose, revocation_epoch=0, label_version=0):
        # Neither raw input nor a guessable unkeyed content digest is retained.
        body = {"version": 1, "tenant": tenant, "text": text, "purpose": purpose,
            "effect": effect, "label": label, "origins": sorted(set(origins)),
            "compartments": sorted(set(compartments)), "generation": generation,
            "policy_digest": policy_digest, "model_digest": model_digest,
            "model_manifest_sha256": model_manifest_sha256, "guard_artifact": guard_artifact,
            "run_purpose": run_purpose, "revocation_epoch": revocation_epoch, "label_version": label_version}
        return hmac.new(self._secret, canonical(body), hashlib.sha256).hexdigest()

    def _expire(self):
        instant = self.clock()
        for key in [key for key, entry in self._entries.items() if entry[0] <= instant]:
            self._bytes -= len(key)+len(self._entries.pop(key)[1])

    def get(self, key):
        with self._lock:
            self._expire()
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            # Deserialization is a deep copy. Callers cannot modify cached data.
            result = json.loads(entry[1])
        result["cache_hit"] = True
        result["usage"] = dict(ZERO_USAGE)
        return result

    def put(self, key, verdict, *, operation_id, generation, model_digest, guard_artifact):
        usage = verdict.get("usage") or {}
        if (verdict.get("complete") is not True or verdict.get("verdict") not in {"benign", "suspicious"}
                or usage.get("usage_unknown") or usage.get("contract_violation")
                or type(usage.get("total_tokens")) is not int or usage.get("total_tokens", -1) < 0
                or verdict.get("generation") != generation
                or verdict.get("model_digest") != model_digest
                or any(window.get("verdict") == "unknown" or (window.get("usage") or {}).get("usage_unknown") for window in verdict.get("windows", []))):
            return False
        result = without_prior_usage(verdict)
        result["usage"] = dict(ZERO_USAGE)
        result["cache_source"] = {"operation_id": operation_id, "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "generation": generation, "model_digest": model_digest, "guard_artifact": guard_artifact,
            "scope": "Prior complete model judgment; no inference was started by this cache hit"}
        encoded = canonical(result)
        size = len(key)+len(encoded)
        if size > min(self.max_entry_bytes, self.max_bytes):
            return False
        with self._lock:
            self._expire()
            previous = self._entries.pop(key, None)
            if previous:
                self._bytes -= len(key)+len(previous[1])
            while self._entries and (len(self._entries) >= self.max_entries or self._bytes+size > self.max_bytes):
                old_key, old_value = self._entries.popitem(last=False)
                self._bytes -= len(old_key)+len(old_value[1])
            self._entries[key] = (self.clock()+self.ttl_seconds, encoded)
            self._bytes += size
        return True

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def info(self):
        with self._lock:
            self._expire()
            return {"entries": len(self._entries), "serialized_bytes": self._bytes,
                "max_entries": self.max_entries, "max_serialized_bytes": self.max_bytes,
                "ttl_seconds": self.ttl_seconds, "scope": "per-process memory; object overhead also bounded by entry count"}


cache = SemanticCache()
