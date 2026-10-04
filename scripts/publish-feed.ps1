param([Parameter(Mandatory=$true)][string]$Input)
$ErrorActionPreference = 'Stop'
uv run python "$PSScriptRoot/publish.py" feed $Input
exit $LASTEXITCODE
