$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try { uv run python scripts/runtime_bootstrap.py doctor; exit $LASTEXITCODE } finally { Pop-Location }
