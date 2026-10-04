#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
suite="${1:-all-local}"
if [ "$suite" = "all-local" ]; then
  uv run python scripts/runtime_bootstrap.py doctor
  uv run python scripts/isolation.py
fi
uv run python scripts/runtime_bootstrap.py compose --profile test run --rm test-runner python scripts/verify_container.py --suite "$suite"
if [ "$suite" = "all-local" ]; then
  (cd ui && npm test)
fi
