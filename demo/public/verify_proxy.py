"""Exercise the real jury proxy and bind its proof to the running container."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_binding():
    value = json.loads(subprocess.run(['docker', 'inspect', 'actiongate-jury-proxy'],
                                     capture_output=True, text=True, check=True).stdout)[0]
    live = subprocess.run(['docker', 'exec', 'actiongate-jury-proxy', 'sha256sum', '/etc/nginx/nginx.conf'],
                          capture_output=True, text=True, check=True).stdout.split()[0]
    return {'container_id': value['Id'], 'image_id': value['Image'], 'user': value['Config']['User'],
            'read_only': value['HostConfig']['ReadonlyRootfs'], 'cap_drop': value['HostConfig']['CapDrop'],
            'networks': sorted(value['NetworkSettings']['Networks']), 'live_configuration_sha256': live,
            'mounts': sorted([{'source': mount['Source'], 'destination': mount['Destination'], 'rw': mount['RW']}
                              for mount in value['Mounts']], key=lambda mount: mount['destination']),
            'ports': value['NetworkSettings']['Ports']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--origin', required=True)
    parser.add_argument('--network', required=True, help='Expected isolated upstream Docker network')
    parser.add_argument('--mode', choices=['echo', 'application'], default='echo')
    parser.add_argument('--public', action='store_true')
    args = parser.parse_args()
    origin = args.origin.rstrip('/')
    if urlparse(origin).scheme != 'https':
        raise SystemExit('Expected HTTPS public origin')
    before = runtime_binding()
    config = ROOT / '.state/public-demo/nginx.conf'
    sources = {f'demo/public/{name}': sha(ROOT / 'demo/public' / name)
               for name in ('configure.py', 'nginx.conf.template', 'verify_proxy.py')}
    configuration_hash = sha(config)
    checks = {}
    expected_networks = sorted([args.network, 'actiongate-jury-transit'])
    checks['exact_upstream_networks'] = before['networks'] == expected_networks
    checks['live_configuration_matches_rendered'] = before['live_configuration_sha256'] == configuration_hash
    checks['unprivileged_readonly_proxy'] = before['user'] == '101:101' and before['read_only'] and before['cap_drop'] == ['ALL']
    checks['loopback_binding_only'] = before['ports'] == {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18088'}]}
    auth = ('juror', os.environ['ACTIONGATE_JURY_PASSWORD'])
    base = origin if args.public else 'http://127.0.0.1:18088'
    headers = {'Host': urlparse(origin).hostname}
    with httpx.Client(trust_env=False, timeout=30) as client:
        checks['unauthenticated_denied'] = client.get(base, headers=headers).status_code == 401
        checks['wrong_password_denied'] = client.get(base, headers=headers, auth=('juror', 'wrong-test-password')).status_code == 401
        checks['foreign_origin_denied'] = client.post(base, headers={**headers, 'Origin': 'https://foreign.example'}, auth=auth).status_code == 403
        checks['missing_mutation_origin_denied'] = client.post(base, headers=headers, auth=auth).status_code == 403
        if not args.public:
            checks['unexpected_host_denied'] = client.get(base, headers={'Host': 'foreign.example'}, auth=auth).status_code == 421
        route = '/api/demo/session' if args.mode == 'application' else '/'
        valid = client.post(base + route, headers={**headers, 'Origin': origin}, auth=auth,
                            json={'role': 'analyst', 'tenant': 'synthetic_test_tenant'} if args.mode == 'application' else {})
        checks['authenticated_same_origin_forwarded'] = valid.status_code == 200
        cookie = valid.headers.get('set-cookie', '').lower()
        checks['session_cookie_hardened'] = all(flag in cookie for flag in ('secure', 'httponly', 'samesite=strict'))
        if args.mode == 'echo':
            checks['basic_credential_removed_upstream'] = valid.json().get('authorization_received') is False
        else:
            # HTTP-only local probe cannot resend a Secure cookie automatically.
            # Supply exactly the freshly issued cookie for this local check.
            session_cookie = valid.cookies.get('actiongate_session')
            session = client.get(base + '/api/session', headers={**headers, 'Cookie': 'actiongate_session=' + (session_cookie or '')}, auth=auth)
            checks['fresh_application_session'] = session.status_code == 200 and session.json().get('tenant') == 'synthetic_test_tenant'
            checks['basic_credential_removed_upstream'] = checks['fresh_application_session']
    after = runtime_binding()
    checks['runtime_unchanged_during_probe'] = before == after and configuration_hash == sha(config)
    checks['producer_unchanged_during_probe'] = sources == {name: sha(ROOT / name) for name in sources}
    record = {'status': 'passed' if all(checks.values()) else 'failed', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'mode': args.mode, 'transport': 'external HTTPS' if args.public else 'local real Nginx',
              'url': origin, 'checks': [{'name': key, 'passed': bool(value)} for key, value in checks.items()],
              'template_sha256': sources['demo/public/nginx.conf.template'], 'producer_sources': sources,
              'rendered_configuration_sha256': configuration_hash, 'runtime_binding': after}
    filename = 'public-proxy-external.json' if args.public else 'public-proxy-contract.json'
    (ROOT / 'artifacts' / filename).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': record['status'], 'checks': record['checks'], 'mode': args.mode,
                      'transport': record['transport'], 'report': 'artifacts/' + filename}, indent=2))
    raise SystemExit(record['status'] != 'passed')


if __name__ == '__main__':
    main()
