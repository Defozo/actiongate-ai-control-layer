"""Executed inside the untrusted agent network to verify actual connectivity."""
import json
import os
import socket
import urllib.error
import urllib.request

results = {}
for host, port in [("gateway-a", 8000), ("gateway-b", 8000), ("postgres", 5432), ("opa-a", 8181), ("guard-worker", 8090), ("business-worker", 8090), ("demo-tools", 8010), ("feed-server", 8020), ("cloud-connector", 8030), ("1.1.1.1", 443), ("host.docker.internal", int(os.getenv("PUBLIC_GATEWAY_PORT", "8080")))]:
    try:
        with socket.create_connection((host, port), timeout=2):
            results[f"{host}:{port}"] = "reachable"
    except OSError:
        results[f"{host}:{port}"] = "blocked"
allowed = {"gateway-a:8000", "gateway-b:8000"}
passed = all((value == "reachable") == (target in allowed) for target, value in results.items())
try:
    socket.gethostbyname('example.com')
    external_dns = 'reachable'
except OSError:
    external_dns = 'blocked'
passed = passed and external_dns == 'blocked'
issuance = {}
for host in ('gateway-a', 'gateway-b'):
    try:
        request = urllib.request.Request(f'http://{host}:8000/api/demo/session',
            data=json.dumps({'role':'admin','tenant':'acme'}).encode(),
            headers={'Content-Type':'application/json', 'Host':'localhost',
                     'Origin':'http://127.0.0.1:'+os.getenv('PUBLIC_GATEWAY_PORT','8080')})
        with urllib.request.urlopen(request, timeout=10) as response:
            issuance[host] = {'http_status': response.status, 'denied': False}
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read()).get('detail')
        issuance[host] = {'http_status': exc.code, 'detail': detail,
                          'denied': exc.code == 403 and detail == 'Demo identity issuance requires the local operator edge'}
    except OSError as exc:
        issuance[host] = {'error': type(exc).__name__, 'denied': False}
passed = passed and all(value['denied'] for value in issuance.values())
print(json.dumps({"passed": passed, "checks": results, 'external_dns': external_dns, 'forged_host_admin_issuance': issuance}, indent=2))
raise SystemExit(0 if passed else 1)
