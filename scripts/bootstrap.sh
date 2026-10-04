#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv sync --frozen
exec uv run python scripts/runtime_bootstrap.py bootstrap --profile "${1:-local}"
