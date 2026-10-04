param([ValidateSet('local','cloud')][string]$Profile = 'local')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
  uv sync --frozen
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  uv run python scripts/runtime_bootstrap.py bootstrap --profile $Profile
  exit $LASTEXITCODE
} finally { Pop-Location }
