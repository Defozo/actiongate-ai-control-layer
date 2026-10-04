#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
uv run python scripts/runtime_bootstrap.py compose --profile test run --rm test-runner python scripts/benchmark.py --profile local --repetitions "${1:-1}"
