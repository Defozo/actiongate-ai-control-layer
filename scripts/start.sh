#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec uv run python scripts/runtime_bootstrap.py start --profile "${1:-local}"
