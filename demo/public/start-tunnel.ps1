# Start only the authenticated jury gateway verified by the local proxy probe.
# The user authorized publication of this project's demonstration endpoint.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$statePath = Join-Path $projectRoot '.state/public-demo'
$proof = Get-Content -LiteralPath (Join-Path $projectRoot 'artifacts/public-proxy-contract.json') -Raw | ConvertFrom-Json
if ($proof.status -ne 'passed' -or $proof.checks.Count -lt 9 -or @($proof.checks | Where-Object { -not $_.passed }).Count) {
    throw 'The authenticated proxy must pass its checks before publication.'
}
$templateHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'nginx.conf.template') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($proof.template_sha256 -ne $templateHash) { throw 'The verified proxy configuration has changed.' }
foreach ($source in $proof.producer_sources.PSObject.Properties) {
    $currentHash = (Get-FileHash -LiteralPath (Join-Path $projectRoot $source.Name) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($currentHash -ne $source.Value) { throw 'A proxy verification source has changed.' }
}
$renderedHash = (Get-FileHash -LiteralPath (Join-Path $statePath 'nginx.conf') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($proof.rendered_configuration_sha256 -ne $renderedHash) { throw 'The rendered proxy configuration has changed.' }
if (-not $env:ACTIONGATE_NGROK_AUTHTOKEN) { throw 'Inject ACTIONGATE_NGROK_AUTHTOKEN with psst.' }
$proxy = docker inspect actiongate-jury-proxy | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $proxy[0].State.Status -ne 'running') { throw 'The authenticated proxy is not running.' }
$liveHash = (docker exec actiongate-jury-proxy sha256sum /etc/nginx/nginx.conf).Split(' ')[0]
if ($LASTEXITCODE -ne 0 -or $liveHash -ne $renderedHash) { throw 'The live proxy configuration differs from the verified file.' }
$actualNetworks = @($proxy[0].NetworkSettings.Networks.PSObject.Properties.Name | Sort-Object)
$expectedNetworks = @($proof.runtime_binding.networks | Sort-Object)
if ($proxy[0].Id -ne $proof.runtime_binding.container_id -or $proxy[0].Image -ne $proof.runtime_binding.image_id `
    -or ($actualNetworks -join ',') -ne ($expectedNetworks -join ',')) {
    throw 'The verified proxy container, image or upstream network has changed.'
}
$binding = $proxy[0].NetworkSettings.Ports.'8080/tcp'
if ($binding.Count -ne 1 -or $binding[0].HostIp -ne '127.0.0.1' -or $binding[0].HostPort -ne '18088') {
    throw 'Expected exactly the verified loopback proxy binding.'
}
if (Get-NetTCPConnection -State Listen -LocalPort 4048 -ErrorAction SilentlyContinue) { throw 'A tunnel already owns port 4048.' }
$ngrokBinary = (Get-Command ngrok).Source
$arguments = @('http', 'http://127.0.0.1:18088', '--name=actiongate-jury',
    '--config=.state/public-demo/ngrok.yaml', '--inspect=false', '--log-format=json', '--log=stdout')
$process = Start-Process -FilePath $ngrokBinary -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -Environment @{ NGROK_AUTHTOKEN = $env:ACTIONGATE_NGROK_AUTHTOKEN } `
    -RedirectStandardOutput (Join-Path $statePath 'ngrok.stdout.jsonl') `
    -RedirectStandardError (Join-Path $statePath 'ngrok.stderr.log') -PassThru
@{ pid = $process.Id; target = 'http://127.0.0.1:18088'; started_at = (Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $statePath 'ngrok-process.json')
@{ pid = $process.Id; authenticated_proxy = $true } | ConvertTo-Json
