param([ValidateSet('contract','all-local','live-provider')][string]$Suite = 'all-local')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if ($Suite -eq 'all-local') {
        uv run python scripts/runtime_bootstrap.py doctor
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        uv run python scripts/isolation.py
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    uv run python scripts/runtime_bootstrap.py compose --profile test run --rm test-runner python scripts/verify_container.py --suite $Suite
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($Suite -eq 'all-local') {
        Push-Location ui
        try { npm test; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
    }
} finally { Pop-Location }
