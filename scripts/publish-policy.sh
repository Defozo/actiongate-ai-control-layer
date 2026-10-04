#!/usr/bin/env sh
set -eu
uv run python "$(dirname "$0")/publish.py" policy "$1"
