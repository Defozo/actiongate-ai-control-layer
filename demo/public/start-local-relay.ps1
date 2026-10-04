# Local browser/SDK access to a retained, completely internal clean GPU stack.
# The independently authenticated HTTPS jury gateway remains a separate service.
param([Parameter(Mandatory)][ValidatePattern('^actiongate-gpu-[a-f0-9]{8}$')][string]$Project)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$networkName = "${Project}_edge"
$relayName = 'actiongate-local-demo-relay'
$image = 'nginx@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94'
$network = docker network inspect $networkName | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $network[0].Internal -or $network[0].Labels.'com.docker.compose.project' -ne $Project) {
    throw 'Expected the isolated edge network of the selected clean GPU installation.'
}
$transit = docker network inspect actiongate-jury-transit | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $transit[0].Internal) { throw 'The existing jury transit network is required.' }
$edge = docker inspect "$Project-edge-1" | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $edge[0].State.Running -or -not $edge[0].NetworkSettings.Networks.$networkName) {
    throw 'The selected internal operator edge is not running on its expected network.'
}
$origin = docker exec "$Project-gateway-a-1" python -c 'import os; print(os.environ["PUBLIC_BASE_URL"])'
if ($LASTEXITCODE -ne 0 -or $origin.Trim() -ne 'http://127.0.0.1:18089') { throw 'The target must use the local operator origin on port 18089.' }
if (Get-NetTCPConnection -State Listen -LocalPort 18089 -ErrorAction SilentlyContinue) { throw 'Port 18089 already has an owner.' }
$existing = docker ps -a --filter "name=^/$relayName`$" --format '{{.ID}}'
if ($LASTEXITCODE -ne 0 -or $existing) { throw 'Inspect the existing local relay before starting another one.' }
$config = (Resolve-Path (Join-Path $PSScriptRoot 'local-relay.conf')).Path
$containerId = docker create --name $relayName --label actiongate.role=local-operator-relay --label "actiongate.target=$Project" `
    --network actiongate-jury-transit --publish 127.0.0.1:18089:8080 --user 101:101 --read-only --cap-drop ALL `
    --security-opt no-new-privileges:true --memory 128m --pids-limit 64 `
    --tmpfs /var/cache/nginx:uid=101,gid=101 --tmpfs /var/run:uid=101,gid=101 `
    --mount "type=bind,source=$config,target=/etc/nginx/nginx.conf,readonly" $image
if ($LASTEXITCODE -ne 0) { throw 'Local relay creation failed.' }
docker network connect $networkName $relayName
if ($LASTEXITCODE -ne 0) { throw 'The local relay could not join the selected edge network.' }
docker start $relayName | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Local relay start failed.' }
@{ status = 'started_requires_verification'; project = $Project; container_id = $containerId;
   url = 'http://127.0.0.1:18089'; public_jury_gateway_changed = $false;
   configuration_sha256 = (Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash.ToLowerInvariant() } | ConvertTo-Json
