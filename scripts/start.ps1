param([ValidateSet('local','cloud')][string]$Profile = 'local')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try { uv run python scripts/runtime_bootstrap.py start --profile $Profile; exit $LASTEXITCODE } finally { Pop-Location }
