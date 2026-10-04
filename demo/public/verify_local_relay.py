"""Verify real loopback access to the retained offline GPU operator edge."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import httpx

ROOT = Path(__file__).resolve().parents[2]
BASE = 'http://127.0.0.1:18089'
NAME = 'actiongate-local-demo-relay'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding():
    value = json.loads(subprocess.check_output(['docker', 'inspect', NAME], text=True))[0]
    host = value['HostConfig']
    live_sha = subprocess.check_output(
        ['docker', 'exec', NAME, 'sha256sum', '/etc/nginx/nginx.conf'], text=True).split()[0]
    return {'container_id': value['Id'], 'image_id': value['Image'],
            'image_ref': value['Config']['Image'], 'user': value['Config']['User'],
            'labels': value['Config']['Labels'], 'read_only': host['ReadonlyRootfs'],
            'memory_limit_bytes': host['Memory'], 'pids_limit': host['PidsLimit'],
            'cap_drop': host['CapDrop'], 'security_options': host['SecurityOpt'],
            'networks': sorted(value['NetworkSettings']['Networks']),
            'ports': value['NetworkSettings']['Ports'], 'configuration_sha256': live_sha}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    args = parser.parse_args()
    sources, checks = {}, {}
    before = after = None
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': 'actual local HTTP through isolated operator edge',
              'status': 'running', 'project': args.project, 'url': BASE, 'checks': checks}
    target = ROOT / 'artifacts/local-demo-relay.json'
    target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    try:
        sources = {f'demo/public/{name}': sha(Path(__file__).with_name(name)) for name in
                   ('local-relay.conf', 'start-local-relay.ps1', 'verify_local_relay.py')}
        report['producer_sources'] = sources
        before = binding()
        published_ports = {port: values for port, values in before['ports'].items() if values}
        checks.update({
            'exact_target_networks': before['networks'] == sorted([args.project + '_edge', 'actiongate-jury-transit']),
            'target_label': before['labels'].get('actiongate.target') == args.project,
            'role_label': before['labels'].get('actiongate.role') == 'local-operator-relay',
            'loopback_only': published_ports == {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18089'}]},
            'unprivileged_readonly': before['user'] == '101:101' and before['read_only'] and before['cap_drop'] == ['ALL'],
            'bounded_resources': before['memory_limit_bytes'] == 134217728 and before['pids_limit'] == 64,
            'no_new_privileges': 'no-new-privileges:true' in before['security_options'],
            'pinned_image': before['image_ref'] == 'nginx@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94',
            'configuration_matches': before['configuration_sha256'] == sources['demo/public/local-relay.conf'],
        })
        if not all(checks.values()):
            raise RuntimeError('Local relay runtime binding differs')
        with httpx.Client(base_url=BASE, trust_env=False, timeout=30) as client:
            ready = client.get('/health/ready')
            checks['actual_application_ready'] = ready.status_code == 200 and ready.json().get('status') == 'ready'
            checks['unexpected_host_denied'] = client.get('/', headers={'Host': 'foreign.example'}).status_code == 421
            checks['foreign_origin_denied'] = client.post('/api/demo/session', headers={'Origin': 'https://foreign.example'},
                                                        json={'role': 'analyst', 'tenant': 'synthetic_test_tenant'}).status_code == 403
            checks['session_required'] = client.get('/api/session').status_code == 401
            login = client.post('/api/demo/session', headers={'Origin': BASE},
                                json={'role': 'analyst', 'tenant': 'synthetic_test_tenant'})
            checks['fresh_local_login'] = login.status_code == 200
            cookie = login.headers.get('set-cookie', '').lower()
            checks['httponly_strict_session'] = 'httponly' in cookie and 'samesite=strict' in cookie
            session = client.get('/api/session')
            checks['actual_tenant_session'] = session.status_code == 200 and session.json().get('tenant') == 'synthetic_test_tenant'
            checks['session_mutation_needs_origin'] = client.post('/api/demo/session',
                json={'role': 'analyst', 'tenant': 'synthetic_test_tenant'}).status_code == 403
            client.cookies.clear()
            checks['invalid_workload_credential_denied'] = client.get('/actions/not-an-operation',
                headers={'Authorization': 'Bearer invalid-synthetic-token'}).status_code == 401
            report['generation'] = ready.json().get('generation')
            checks['probe_completed'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        checks['probe_completed'] = False
    finally:
        try:
            after = binding()
            checks['runtime_unchanged'] = before is not None and before == after
        except Exception as error:
            checks['runtime_unchanged'] = False
            report['final_binding_error_type'] = type(error).__name__
        try:
            checks['producer_unchanged'] = bool(sources) and sources == {name: sha(ROOT / name) for name in sources}
        except OSError:
            checks['producer_unchanged'] = False
        report.update(status='passed' if all(checks.values()) else 'failed', runtime_binding=after)
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks': checks, 'report': str(target.relative_to(ROOT))}))
    raise SystemExit(report['status'] != 'passed')


if __name__ == '__main__':
    main()
