param([ValidateSet('local')][string]$Profile = 'local', [ValidateRange(1,100)][int]$Repetitions = 1)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    uv run python scripts/runtime_bootstrap.py compose --profile test run --rm test-runner python scripts/benchmark.py --profile $Profile --repetitions $Repetitions
    exit $LASTEXITCODE
} finally { Pop-Location }
