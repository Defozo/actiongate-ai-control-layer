"""Record actual CPU/GPU placement and limits after real inference has loaded both roles."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from runtime_evidence import producer_sources

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='actiongate')
    parser.add_argument('--profile', choices=('CPU', 'GPU'), default='CPU')
    parser.add_argument('--output', default='artifacts/runtime-hardware.json')
    args = parser.parse_args()
    sources = producer_sources('scripts/runtime_hardware.py', 'scripts/runtime_evidence.py')
    configuration = json.loads((ROOT / Path(args.output).parent / 'deployed-configuration.json').read_text())
    if os.name == 'nt':
        processors = json.loads(subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
            'Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json'], text=True))
        if isinstance(processors, dict):
            processors = [processors]
    else:
        processors = [{'Name': platform.processor(), 'NumberOfLogicalProcessors': os.cpu_count()}]
    import psutil
    report = {'measured_at_utc': datetime.now(timezone.utc).isoformat(), 'runtime': 'Docker Linux containers',
        'profile': args.profile, 'project': args.project, 'processors': processors, 'host_memory_bytes': psutil.virtual_memory().total, 'workers': [],
        'checks': {'deployed_source_verified': configuration.get('source_evidence_passed') is True},
        'model': configuration['model_artifacts']['local-guard']['manifest'],
        'producer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        **{key: configuration[key] for key in ('generation', 'guard_artifact', 'model_artifacts', 'semantic_configuration')}}
    probe = """import json,urllib.request
def get(path):
 return json.loads(urllib.request.urlopen(path,timeout=10).read())
print(json.dumps({'ready':get('http://127.0.0.1:8090/health/ready'),'resident_models':get('http://127.0.0.1:11434/api/ps')['models']}))"""
    for role in ('guard', 'business'):
        container = f'{args.project}-{role}-worker-1'
        metadata = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
        host = metadata['HostConfig']
        actual = json.loads(subprocess.check_output(['docker', 'exec', container, 'python', '-c', probe], text=True))
        ready = actual['ready']
        expected = configuration['model_artifacts']['local-'+role]
        matched = [model for model in actual['resident_models']
                   if str(model.get('digest', '')).removeprefix('sha256:') == expected['manifest']['digest'].removeprefix('sha256:')]
        physical_profile = bool(matched) and all((model.get('size_vram', 0) == 0 if args.profile == 'CPU'
                                                 else model.get('size_vram', 0) > 0) for model in matched)
        if args.profile == 'CPU':
            physical_profile = physical_profile and not host.get('DeviceRequests')
        report['checks'][role+'_signed_model_loaded'] = (bool(matched) and ready['model_manifest_sha256'] == expected['sha256']
            and all(ready.get(key) == expected['manifest'][key] for key in ('model', 'digest', 'runtime', 'tokenizer_sha256')))
        report['checks'][role+'_physical_profile'] = physical_profile
        report['checks'][role+'_current_generation'] = configuration['generation'] in ready['prepared_generations']
        report['workers'].append({'role': role, 'name': container, 'container_id': metadata['Id'], 'image_id': metadata['Image'],
            'memory_bytes': host['Memory'], 'nano_cpus': host['NanoCpus'], 'pids_limit': host['PidsLimit'],
            'read_only_root': host['ReadonlyRootfs'], 'device_requests': host.get('DeviceRequests') or [], **actual})
    report['passed'] = all(report['checks'].values())
    report['producer_sources'] = sources
    report['producer_source_stable'] = sources == producer_sources(*sources)
    report['passed'] = report['passed'] and report['producer_source_stable']
    destination = ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'profile': args.profile, 'checks': report['checks']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
