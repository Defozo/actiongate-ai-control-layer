"""Read actual worker placement and bind evidence to its producer source files."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def producer_sources(*names):
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(names)}


def physical_runtime(project, artifacts, *, profile=None, require_business=True):
    result = {"project": project, "observed_at_utc": datetime.now(timezone.utc).isoformat(), "workers": [], "checks": {}}
    code = '''import json,urllib.request
def get(url):
 return json.loads(urllib.request.urlopen(url,timeout=10).read())
print(json.dumps({'ready':get('http://127.0.0.1:8090/health/ready'),'resident_models':get('http://127.0.0.1:11434/api/ps')['models']}))'''
    for role in ('guard', 'business'):
        name = f'{project}-{role}-worker-1'
        metadata = json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
        actual = json.loads(subprocess.check_output(['docker','exec',name,'python','-c',code],text=True))
        worker = {'role': role, 'name': name, 'container_id': metadata['Id'], 'image_id': metadata['Image'],
                  'device_requests': metadata['HostConfig'].get('DeviceRequests') or [], **actual}
        expected = artifacts['local-'+role]
        ready = worker['ready']
        matches = [model for model in worker['resident_models'] if str(model.get('digest','')).removeprefix('sha256:') == expected['manifest']['digest'].removeprefix('sha256:')]
        result['checks'][role+'_signed_model'] = (ready.get('model_manifest_sha256') == expected['sha256'] and
            all(ready.get(key) == expected['manifest'][key] for key in ('model','digest','runtime','tokenizer_sha256')))
        result['checks'][role+'_required_model_loaded'] = bool(matches) or role == 'business' and not require_business
        result['workers'].append(worker)
    cpu = all(not worker['device_requests'] and all(model.get('size_vram',0) == 0 for model in worker['resident_models']) for worker in result['workers'])
    gpu = all(worker['device_requests'] and worker['resident_models'] and all(model.get('size_vram',0) > 0 for model in worker['resident_models']) for worker in result['workers'])
    observed = 'CPU' if cpu else 'GPU' if gpu else None
    result['profile'] = observed
    result['checks']['physical_profile_verified'] = observed is not None and (profile is None or observed == profile.upper())
    result['passed'] = all(result['checks'].values())
    return result
