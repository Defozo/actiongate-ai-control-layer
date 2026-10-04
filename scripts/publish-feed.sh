#!/usr/bin/env sh
set -eu
uv run python "$(dirname "$0")/publish.py" feed "$1"
