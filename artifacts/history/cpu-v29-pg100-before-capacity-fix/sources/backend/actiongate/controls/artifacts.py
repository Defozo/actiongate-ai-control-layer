"""Offline artifact admission. This module has no model/object deserializer."""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any, Literal

from packaging.version import InvalidVersion, Version
from pydantic import Field

from .schema import StrictModel
from .signed import ControlError, SignedFeed, bounded_structure, load_json


class ArtifactManifest(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(max_length=128)
    format: Literal["json", "gguf", "safetensors"]
    source: str = Field(max_length=512)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1, le=100_000_000_000)
    package: str = Field(max_length=128)
    version: str = Field(max_length=64)
    trust_remote_code: Literal[False] = False
    approved_template_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    dependencies: dict[str, str] = Field(default_factory=dict)


def inspect_plain_json(value: Any) -> None:
    """Data stays data; active LangChain reconstruction markers never enter a loader."""
    bounded_structure(value)
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get("lc") == 1 and item.get("type") in {"constructor", "secret", "not_implemented"}:
                raise ControlError("Historical serialization structure denied: CVE-2025-68664")
            if any(key in item for key in ("__reduce__", "py/object", "py/reduce")):
                raise ControlError("Active object reconstruction metadata denied")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _gguf_metadata(stream, manifest: ArtifactManifest) -> dict:
    def read(size):
        if size > 1_048_576:
            raise ControlError("GGUF metadata entry exceeds limit")
        result = stream.read(size)
        if len(result) != size:
            raise ControlError("Truncated GGUF metadata")
        return result

    def integer(fmt):
        return struct.unpack(fmt, read(struct.calcsize(fmt)))[0]

    def string():
        length = integer("<Q")
        try:
            return read(length).decode("utf-8")
        except UnicodeError as exc:
            raise ControlError("Invalid GGUF metadata encoding") from exc

    item_count = 0

    def value(kind, depth=0):
        nonlocal item_count
        item_count += 1
        if depth > 4 or item_count > 1_000_000:
            raise ControlError("GGUF metadata exceeds parser limits")
        formats = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
        if kind in formats:
            return integer(formats[kind])
        if kind == 8:
            return string()
        if kind == 9:
            subtype, count = integer("<I"), integer("<Q")
            if count > 1_000_000:
                raise ControlError("GGUF array exceeds limit")
            for _ in range(count):
                value(subtype, depth + 1)
            return None  # Arrays are validated, not retained or executed.
        raise ControlError("Unknown GGUF metadata type")

    if read(4) != b"GGUF" or integer("<I") not in (2, 3):
        raise ControlError("Artifact is not supported GGUF")
    tensor_count, count = integer("<Q"), integer("<Q")
    if tensor_count > 100_000 or count > 4096:
        raise ControlError("GGUF header exceeds limits")
    metadata = {}
    for _ in range(count):
        key = string()
        if key in metadata:
            raise ControlError("Duplicate GGUF metadata key")
        metadata[key] = value(integer("<I"))
    template = metadata.get("tokenizer.chat_template")
    if template is not None:
        if not isinstance(template, str) or hashlib.sha256(template.encode()).hexdigest() != manifest.approved_template_sha256:
            raise ControlError("Unapproved model chat template")
    return {"tensor_count": tensor_count, "metadata_count": count}


def validate_artifact(path: Path | str, manifest: ArtifactManifest | dict, *, approved_sources: set[str],
                      feed: SignedFeed | None = None) -> dict:
    try:
        manifest = manifest if isinstance(manifest, ArtifactManifest) else ArtifactManifest.model_validate(manifest)
    except ValueError as exc:
        raise ControlError("Invalid artifact manifest") from exc
    path = Path(path)
    if path.is_symlink() or path.suffix.lower() in {".pkl", ".pickle", ".joblib", ".pt", ".pth", ".py", ".so", ".dll"}:
        raise ControlError("Executable or serialized object artifacts are prohibited")
    if manifest.source not in approved_sources:
        raise ControlError("Artifact source is not approved")
    if path.stat().st_size != manifest.size_bytes:
        raise ControlError("Artifact size does not match manifest")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            sha.update(chunk)
    actual = sha.hexdigest()
    if actual != manifest.sha256:
        raise ControlError("Artifact digest does not match manifest")
    # Advisory ranges are specific to their packages, never inferred for Ollama.
    packages = {**manifest.dependencies, manifest.package: manifest.version}
    for package, version in packages.items():
        try:
            parsed = Version(version)
        except InvalidVersion as exc:
            raise ControlError("Artifact dependency version cannot be verified") from exc
        normalized = package.replace("_", "-").lower()
        if normalized == "llama-cpp-python" and Version("0.2.30") <= parsed <= Version("0.2.71"):
            raise ControlError("Vulnerable metadata template runtime: CVE-2024-34359")
        if normalized == "langchain-core" and (parsed < Version("0.3.81") or Version("1.0.0") <= parsed < Version("1.2.5")):
            raise ControlError("Vulnerable serialization runtime: CVE-2025-68664")
        if feed:
            feed.check_freshness()
            attrs = {**manifest.model_dump(), "package": package, "version": version}
            rules = feed.match("artifact", attrs)
            if rules:
                raise ControlError("Artifact blocked by signed threat feed")
    with path.open("rb") as stream:
        if manifest.format == "json":
            if manifest.size_bytes > 1_048_576:
                raise ControlError("JSON artifact exceeds parser limit")
            inspect_plain_json(load_json(stream.read()))
        elif manifest.format == "gguf":
            _gguf_metadata(stream, manifest)
        else:
            raw = stream.read(8)
            if len(raw) != 8:
                raise ControlError("Invalid safetensors header")
            header_size = struct.unpack("<Q", raw)[0]
            if header_size < 2 or header_size > 1_048_576 or header_size + 8 > manifest.size_bytes:
                raise ControlError("Invalid safetensors header size")
            header = load_json(stream.read(header_size))
            if not isinstance(header, dict):
                raise ControlError("Invalid safetensors metadata")
            ranges = []
            for key, tensor in header.items():
                if key == "__metadata__":
                    if not isinstance(tensor, dict) or any(not isinstance(v, str) for v in tensor.values()):
                        raise ControlError("Invalid safetensors user metadata")
                    continue
                if not isinstance(tensor, dict) or set(tensor) != {"dtype", "shape", "data_offsets"}:
                    raise ControlError("Invalid safetensors tensor metadata")
                offsets = tensor["data_offsets"]
                if not isinstance(offsets, list) or len(offsets) != 2 or any(type(v) is not int for v in offsets):
                    raise ControlError("Invalid safetensors offsets")
                start, end = offsets
                if not 0 <= start <= end <= manifest.size_bytes - 8 - header_size:
                    raise ControlError("Safetensors data exceeds file bounds")
                ranges.append((start, end))
            ordered = sorted(ranges)
            if any(ordered[i][1] > ordered[i + 1][0] for i in range(len(ordered) - 1)):
                raise ControlError("Overlapping safetensors data")
    return {"admitted": True, "sha256": actual, "format": manifest.format,
            "size_bytes": manifest.size_bytes, "package": manifest.package, "version": manifest.version,
            "executed_loader": False}
