"""Signed model artifacts and acknowledgements from the physical deployment."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import re

from .registry import ModelDefinition
from .signed import ControlError, digest, load_json

ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATHS = {"ollama": "models/model-manifest.json", "groq": "models/cloud-model-manifest.json"}


def _models(models):
    return [item if isinstance(item, ModelDefinition) else ModelDefinition.model_validate(item) for item in models]


def validate_model_artifacts(models, artifacts):
    definitions = _models(models)
    if not isinstance(artifacts, dict) or set(artifacts) != {item.id for item in definitions}:
        raise ControlError("Signed model artifacts do not cover the complete registry")
    for model in definitions:
        entry = artifacts[model.id]
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "manifest"}:
            raise ControlError("Invalid signed model artifact envelope")
        if model.manifest != MANIFEST_PATHS[model.provider] or entry["path"] != model.manifest:
            raise ControlError("Model manifest path is outside the prepared deployment")
        manifest = entry["manifest"]
        if not isinstance(manifest, dict) or digest(manifest) != entry["sha256"] or manifest.get("schema_version") != 1:
            raise ControlError("Model manifest digest or schema is invalid")
        if manifest.get("context_tokens") != model.context_tokens:
            raise ControlError("Model context differs from its signed manifest")
        if model.provider == "ollama":
            if model.id not in {"local-guard", "local-business"} or model.purpose != ("guard" if model.id == "local-guard" else "business"):
                raise ControlError("Local model role is not supported by the prepared workers")
            # This explicitly registered project alias resolves solely through
            # the signed manifest. Arbitrary Ollama names are never selected.
            if model.model not in {"actiongate-qwen3:4b", manifest.get("model")}:
                raise ControlError("Local model name differs from its signed manifest")
            if (manifest.get("format") != "GGUF" or manifest.get("runtime") not in {"ollama:0.18.2", "ollama:0.32.0"}
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest.get("digest", "")))
                    or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("tokenizer_sha256", "")))
                    or type(manifest.get("max_input_tokens")) is not int or not 1 <= manifest["max_input_tokens"] <= model.context_tokens):
                raise ControlError("Local manifest does not declare a verifiable worker contract")
            if any(type(manifest.get(key)) is not type(value) or manifest.get(key) != value
                   for key, value in {"temperature": 0, "seed": 42, "think": False}.items()):
                raise ControlError("Local manifest generation parameters differ from the prepared worker")
        else:
            required = {"provider": "groq", "model": model.model, "api_host": "api.groq.com", "api_path": "/openai/v1/chat/completions",
                "max_completion_tokens": 65536, "reservation_input_method": "full_provider_context_upper_bound",
                "reservation_output_method": "max_completion_tokens_including_hidden_reasoning", "include_reasoning": False,
                "reasoning_effort": "low", "automatic_retries": False, "redirects": False, "built_in_tools": False,
                "supported_tools": "client_function_schemas_only", "price_file": "policy/prices.json"}
            if any(manifest.get(key) != value or type(manifest.get(key)) is not type(value) for key, value in required.items()):
                raise ControlError("Cloud manifest differs from the implemented provider contract")
    return artifacts


def load_model_artifacts(models):
    artifacts = {}
    for model in _models(models):
        if model.manifest != MANIFEST_PATHS[model.provider]:
            raise ControlError("Model manifest path is outside the prepared deployment")
        path = ROOT / model.manifest
        if not path.resolve().is_relative_to(ROOT.resolve() / "models") or path.is_symlink() or path.stat().st_size > 65536:
            raise ControlError("Model manifest is not a bounded prepared artifact")
        manifest = load_json(path.read_bytes())
        artifacts[model.id] = {"path": model.manifest, "sha256": digest(manifest), "manifest": manifest}
        if model.provider == "ollama":
            tokenizer = ROOT / "models/tokenizer.json"
            if tokenizer.is_symlink() or not tokenizer.resolve().is_relative_to(ROOT.resolve() / "models"):
                raise ControlError("Tokenizer path is outside the prepared deployment")
            if hashlib.sha256(tokenizer.read_bytes()).hexdigest() != manifest.get("tokenizer_sha256"):
                raise ControlError("Prepared tokenizer differs from its model manifest")
    return validate_model_artifacts(models, artifacts)


async def verify_worker_artifacts(payload, workers=None):
    """Bind an already verified signed payload to both actual model workers."""
    artifacts = validate_model_artifacts(payload["models"], payload.get("model_artifacts"))
    if workers is None:
        from actiongate.runtime import WorkerClient
        values = await asyncio.gather(WorkerClient("guard").ready(), WorkerClient("business").ready())
        workers = dict(zip(("guard", "business"), values))
    elif isinstance(workers, (list, tuple)):
        workers = {item["role"]: item for item in workers}
    for role, model_id in (("guard", "local-guard"), ("business", "local-business")):
        expected = artifacts.get(model_id)
        actual = workers.get(role, {})
        if not expected:
            raise ControlError("Signed snapshot omits a required local worker artifact")
        manifest = expected["manifest"]
        fields = {"model": manifest["model"], "digest": manifest["digest"], "tokenizer_sha256": manifest["tokenizer_sha256"],
                  "model_manifest_sha256": expected["sha256"], "context_tokens": manifest["context_tokens"],
                  "max_input_tokens": manifest["max_input_tokens"]}
        if any(actual.get(key) != value for key, value in fields.items()):
            raise ControlError("Running worker differs from the signed model or tokenizer artifact")
        # Legacy 0.18.2 deployments predate runtime-version telemetry. Every
        # upgraded runtime and every worker which reports a version must match.
        if (manifest['runtime'] != 'ollama:0.18.2' or 'runtime' in actual) and actual.get('runtime') != manifest['runtime']:
            raise ControlError("Running inference runtime differs from the signed model manifest")
    return True
