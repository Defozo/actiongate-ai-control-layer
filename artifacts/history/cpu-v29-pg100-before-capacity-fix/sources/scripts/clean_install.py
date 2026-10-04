"""Boot an independent, offline acceptance stack from prepared images and weights.

The disposable project has private databases, volumes, networks and policy copies.
Only the immutable model volume is shared. Main project services are untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import httpx
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory

from runtime_bootstrap import NAMES, ROOT, environment
from runtime_evidence import physical_runtime, producer_sources
sys.path.insert(0, str(ROOT/'backend'))


class ReplaceSequence(list):
    """Compose >=2.24.4 replaces, rather than appends, IPAM subnets."""


yaml.SafeDumper.add_representer(ReplaceSequence, lambda dumper, value: dumper.represent_sequence('!override', value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--injected', action='store_true')
    parser.add_argument('--port', type=int, default=18088)
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--runtime-profile', choices=('cpu', 'gpu'), default='cpu')
    parser.add_argument('--model-candidate', choices=('qwen35',))
    parser.add_argument('--runtime-candidate', choices=('ollama032',))
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.prepare_only and not args.keep:
        parser.error('--prepare-only requires --keep for subsequent isolated evaluation')
    if args.runtime_candidate and (args.model_candidate != 'qwen35' or args.runtime_profile != 'gpu'):
        parser.error('The Ollama candidate requires the isolated qwen35 GPU profile')
    if not args.injected:
        vault = shutil.which('psst.cmd' if os.name == 'nt' else 'psst')
        return subprocess.run([vault, *NAMES, '--', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], '--injected']).returncode
    sources = producer_sources('scripts/clean_install.py', 'scripts/preflight_container.py', 'scripts/runtime_bootstrap.py', 'scripts/runtime_evidence.py', 'scripts/gpu_concurrency_probe.py')
    identity = uuid.uuid4().hex[:8]
    project = 'actiongate-'+('candidate-' if args.model_candidate else 'gpu-' if args.runtime_profile == 'gpu' else 'clean-')+identity
    work = ROOT/'.state'/'clean-install'/identity
    work.mkdir(parents=True)
    shutil.copytree(ROOT/'policy', work/'policy')
    shutil.copytree(ROOT/'feeds', work/'feeds')
    candidate_models = None
    model_volume = 'actiongate_models'
    if args.model_candidate:
        from prepare_qwen35_candidate import MODEL, DIGEST, TOKENIZER_SHA256, VOLUME
        candidate_models = ROOT/'.state/model-candidates/qwen35/models'
        if args.runtime_candidate:
            upgraded_models = ROOT/'.state/model-candidates/qwen35-ollama032/models'
            shutil.copytree(candidate_models, upgraded_models, dirs_exist_ok=True)
            manifest_path = upgraded_models/'model-manifest.json'
            upgraded_manifest = json.loads(manifest_path.read_text())
            upgraded_manifest['runtime'] = 'ollama:0.32.0'
            manifest_path.write_text(json.dumps(upgraded_manifest, indent=2)+'\n')
            candidate_models = upgraded_models
        manifest = json.loads((candidate_models/'model-manifest.json').read_text())
        if manifest['model'] != MODEL or manifest['digest'] != DIGEST or hashlib.sha256((candidate_models/'tokenizer.json').read_bytes()).hexdigest() != TOKENIZER_SHA256:
            raise RuntimeError('Candidate artifacts have not passed their pinned preparation')
        subprocess.run(['docker','volume','inspect',VOLUME],check=True,capture_output=True)
        model_volume = VOLUME
        registry_path = work/'policy/model-registry.json'
        registry = json.loads(registry_path.read_text())
        for model in registry:
            if model['provider'] == 'ollama':
                model.update(model=MODEL, version=model['version']+1)
        registry_path.write_text(json.dumps(registry,indent=2)+'\n')
    network_names = ['agent','data','guard','business','tools','opa','feed','edge','cloud','cloud-egress','download']
    existing_ids = subprocess.check_output(['docker','network','ls','-q'], text=True).split()
    existing = json.loads(subprocess.check_output(['docker','network','inspect',*existing_ids], text=True))
    occupied = [ipaddress.ip_network(c['Subnet']) for n in existing for c in (n['IPAM'].get('Config') or []) if 'Subnet' in c]
    block = next((ipaddress.ip_network(f'10.253.{n}.0/24') for n in range(90,200)
                  if not any(ipaddress.ip_network(f'10.253.{n}.0/24').overlaps(net) for net in occupied if net.version == 4)), None)
    if block is None:
        raise RuntimeError('No separate acceptance network range is available')
    subnets = list(block.subnets(new_prefix=28))
    override = {'networks': {name: {'internal': True, 'ipam': {'config': ReplaceSequence([{'subnet': str(subnets[index])}])}}
                            for index,name in enumerate(network_names)},
        'volumes': {'models': {'external': True, 'name': model_volume}},
        'services': {}}
    for service in ('gateway-a','gateway-b'):
        override['services'][service] = {'volumes': [f'{(work/"policy").as_posix()}:/app/policy:ro', f'{(work/"feeds").as_posix()}:/app/feeds:ro']}
    override['services']['test-runner'] = {'volumes': [f'{(work/"policy").as_posix()}:/app/policy:ro',
        f'{(work/"feeds").as_posix()}:/app/feeds:ro']}
    override['services']['feed-server'] = {'volumes': [f'{(work/"policy").as_posix()}:/app/policy', f'{(work/"feeds").as_posix()}:/app/feeds:ro']}
    if candidate_models:
        output = ROOT/('artifacts/qwen35-ollama032-tuning' if args.runtime_candidate else 'artifacts/qwen35-tuning')
        output.mkdir(exist_ok=True)
        override['services']['test-runner'] = {'volumes': [f'{(work/"policy").as_posix()}:/app/policy:ro',
            f'{(work/"feeds").as_posix()}:/app/feeds:ro', f'{output.as_posix()}:/app/artifacts']}
        for service in ('guard-worker','business-worker','gateway-a','gateway-b','feed-server','test-runner'):
            settings = override['services'].setdefault(service, {})
            settings.setdefault('volumes', []).append(f'{candidate_models.as_posix()}:/app/models:ro')
            if service.endswith('-worker'):
                settings.setdefault('environment', {})['OLLAMA_MODEL'] = manifest['model']
    override_path = work/'compose.override.yaml'
    override_path.write_text(yaml.safe_dump(override, sort_keys=False))
    env = environment()
    env['ACTIONGATE_PORT'] = str(args.port)
    command = ['docker','compose','--project-name',project,'--file',str(ROOT/'compose.yaml'),'--file',str(override_path)]
    if args.runtime_profile == 'gpu':
        command.extend(['--file', str(ROOT/'deploy/compose.gpu.yaml')])
    if args.runtime_candidate:
        command.extend(['--file', str(ROOT/'deploy/compose.ollama032-candidate.yaml')])
    report = {'suite':'gpu-preflight' if args.runtime_profile == 'gpu' else 'clean-install-offline',
        'runtime_profile':args.runtime_profile,'project':project,'started_at':datetime.now(timezone.utc).isoformat(),
        'mode':'fresh database and isolated internal networks; predownloaded immutable images and weights',
        'credentials':'existing scoped psst credentials, never exported','checks':{},'passed':False,'status':'running'}
    report['candidate_model'] = args.model_candidate
    report['candidate_runtime'] = args.runtime_candidate
    report['prepare_only'] = args.prepare_only
    target = ROOT/'artifacts'/('qwen35-ollama032-candidate-runtime.json' if args.runtime_candidate else 'qwen35-candidate-runtime.json' if args.model_candidate else 'gpu-preflight.json' if args.runtime_profile == 'gpu' else 'clean-install.json')
    target.parent.mkdir(exist_ok=True)
    if args.runtime_profile == 'gpu' and shutil.which('nvidia-smi'):
        try:
            devices = subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu',
                                      '--format=csv,noheader'],capture_output=True,text=True,timeout=10)
            report['gpu_host_before_start']={'returncode':devices.returncode,'csv':devices.stdout.strip(),
                                            'scope':'whole physical device, including unrelated workloads'}
        except subprocess.TimeoutExpired:
            report['gpu_host_before_start']={'status':'unavailable','reason':'host nvidia-smi exceeded 10 seconds'}
    target.write_text(json.dumps(report,indent=2)+'\n')

    def compose(*parts, **kwargs):
        kwargs.setdefault('check', True)
        return subprocess.run([*command,*parts],cwd=ROOT,env=env,**kwargs)

    def receipt_count():
        result = compose('exec','-T','postgres','psql','-U','actiongate_owner','-d','actiongate','-Atc','SELECT count(*) FROM connector_receipts',capture_output=True,text=True)
        return int(result.stdout.strip())

    def direct(service, method, path, payload=None, token=None):
        code = 'import httpx,json\n'
        code += 'r=httpx.request('+repr(method)+', "http://127.0.0.1:8000"+'+repr(path)+',json='+repr(payload)+',headers='+repr({'Authorization':'Bearer '+token} if token else {})+',timeout=1850)\n'
        code += 'print(json.dumps({"http_status":r.status_code,"body":r.json()}))\n'
        result = compose('exec','-T',service,'python','-',input=code,capture_output=True,text=True)
        return json.loads(result.stdout)

    class OfflineOperator:
        """Exercise the real edge from inside the fully internal network.

        Docker does not publish host ports from an internal-only bridge. The
        acceptance client therefore runs in the private project; the ordinary
        host-facing UI is separately tested on the reference deployment.
        """
        def __init__(self):
            self.cookies = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, path, json=None, headers=None):
            operator_headers = {'Origin':f'http://127.0.0.1:{args.port}', 'Host':f'127.0.0.1:{args.port}', **(headers or {})}
            code = 'import httpx,json\n'
            code += 'r=httpx.request('+repr(method)+', "http://edge:8080"+'+repr(path)+',json='+repr(json)+',headers='+repr(operator_headers)+',cookies='+repr(self.cookies)+',timeout=1850)\n'
            code += 'try:\n body=r.json()\nexcept ValueError:\n body=r.text\n'
            code += 'print(json.dumps({"http_status":r.status_code,"body":body,"cookies":dict(r.cookies)}))\n'
            result = compose('exec','-T','gateway-a','python','-',input=code,capture_output=True,text=True)
            data = __import__('json').loads(result.stdout)
            self.cookies.update(data['cookies'])
            return httpx.Response(data['http_status'],json=data['body'],request=httpx.Request(method,'http://edge:8080'+path))

        def get(self, path, **kwargs):
            return self.request('GET', path, **kwargs)

        def post(self, path, **kwargs):
            return self.request('POST', path, **kwargs)

    def wait_ready(client, timeout=240):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            try:
                result=client.get('/health/ready')
                if result.status_code==200:
                    replicas=[direct(service,'GET','/health/ready') for service in ('gateway-a','gateway-b')]
                    if all(replica['http_status']==200 and replica['body'].get('generation')==result.json()['generation'] for replica in replicas):
                        return result.json()
            except (httpx.HTTPError, subprocess.CalledProcessError):
                pass
            time.sleep(1)
        raise RuntimeError('Independent stack did not become functionally ready')

    try:
        compose('up','-d','--no-build','--pull','never')
        actual_networks=json.loads(subprocess.check_output(['docker','network','inspect',*[project+'_'+name for name in network_names if name not in ('download','cloud-egress')]],text=True))
        report['checks']['all_service_networks_deny_external_egress']=all(item['Internal'] for item in actual_networks)
        offline_probe = '''import json,socket
checks={}
try:
    connection=socket.create_connection(("1.1.1.1",443),timeout=2)
    connection.close()
    checks["public_tcp_blocked"]=False
except OSError:
    checks["public_tcp_blocked"]=True
try:
    socket.gethostbyname("example.com")
    checks["external_dns_blocked"]=False
except OSError:
    checks["external_dns_blocked"]=True
print(json.dumps(checks))
'''
        for service in ('gateway-a','guard-worker','business-worker'):
            probe=compose('exec','-T',service,'python','-',input=offline_probe,capture_output=True,text=True)
            report['checks'][service+'_actual_internet_unreachable']=all(json.loads(probe.stdout).values())
        report['operator_client']='isolated acceptance process through the real internal edge; host UI verified separately'
        with OfflineOperator() as client:
            initial=wait_ready(client)
            report['initial_generation']=initial['generation']
            binding = compose('exec','-T','gateway-a','python','-c',
                "import json,httpx; from actiongate.policies import snapshot,verify_stored_snapshot; from actiongate.runtime import WorkerClient; s=snapshot(); p=verify_stored_snapshot(s); workers={role:httpx.get(WorkerClient(role).url+'/health/ready',timeout=5).json() for role in ('guard','business')}; print(json.dumps({'model_artifacts':p['model_artifacts'],'guard_artifact':p['guard_artifact'],'semantic_configuration':s['configuration']['semantic'],'workers':workers}))",capture_output=True,text=True)
            report.update(json.loads(binding.stdout))
            report['checks']['fresh_database_generation']=initial['generation']==1
            migration=compose('exec','-T','postgres','psql','-U','actiongate_owner','-d','actiongate','-Atc','SELECT version_num FROM alembic_version',capture_output=True,text=True)
            report['alembic_revision']=migration.stdout.strip()
            migration_config=Config()
            migration_config.set_main_option('script_location',str(ROOT/'migrations'))
            report['expected_alembic_head']=ScriptDirectory.from_config(migration_config).get_current_head()
            report['checks']['versioned_owner_migration_applied']=report['alembic_revision']==report['expected_alembic_head']
            if args.runtime_profile == 'gpu':
                # Reject a silent CPU fallback before the longer GPU suite can
                # contend with the independent reference CPU acceptance run.
                warmup = '''import asyncio,json
from actiongate.runtime import WorkerClient,RuntimeFailure
from actiongate.policies import snapshot
async def main():
 active=snapshot()
 generation=active['generation']
 results={}
 for role in ('guard','business'):
  try:
   deadline=active['configuration']['semantic']['deadline_seconds'] if role=='guard' else active['configuration']['local_resources']['business_call_deadline_seconds']
   response=await WorkerClient(role).infer([{'role':'user','content':'Reply ready.'}],1,deadline_seconds=deadline,generation=generation)
   results[role]=response['usage']
  except RuntimeFailure as error:
   results[role]={'error':str(error),'usage':error.usage,'stop':error.stop}
   break
 print(json.dumps(results))
asyncio.run(main())
'''
                measured=compose('exec','-T','gateway-a','python','-',input=warmup,capture_output=True,text=True)
                report['gpu_residency_warmup_usage']=json.loads(measured.stdout)
                if any('error' in value for value in report['gpu_residency_warmup_usage'].values()):
                    raise RuntimeError('Bounded GPU startup inference failed; inspect gpu_residency_warmup_usage')
                startup_residency={}
                for service in ('guard-worker','business-worker'):
                    measured=compose('exec','-T',service,'python','-c',
                        'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:11434/api/ps").read().decode())',capture_output=True,text=True)
                    startup_residency[service]=json.loads(measured.stdout)['models']
                report['gpu_startup_residency']=startup_residency
                report['checks']['gpu_enabled_before_functional_suite']=all(any(model.get('size_vram',0)>0 for model in models) for models in startup_residency.values())
                if not report['checks']['gpu_enabled_before_functional_suite']:
                    raise RuntimeError('GPU profile silently fell back to CPU; full GPU suite was not run')
            if args.prepare_only:
                report['ready_prepared'] = all(report['checks'].values())
                return 0 if report['ready_prepared'] else 1
            result=client.post('/api/demo/session',json={'role':'admin','tenant':'acme'})
            result.raise_for_status()
            request={'document_ids':['supplier-acme-1']}
            result=client.post('/runs',json=request)
            result.raise_for_status()
            run=result.json()
            action={'run_id':run['id'],'tool':'documents.read','arguments':{'document_id':'supplier-acme-1'},'idempotency_key':uuid.uuid4().hex}
            result=client.post('/actions',json=action,headers={'Authorization':'Bearer '+run['workload_token']})
            result.raise_for_status()
            operation=result.json()
            report['checks']['real_document_mcp_guard_workflow']=operation.get('status')=='completed' and 'Supplier review' in json.dumps(operation.get('result'))
            report['operation_id']=operation.get('id')
            report['initial_operation_outcome']={key:operation.get(key) for key in ('status','decision','reason','rule_ids','stage','metadata')}
            if not report['checks']['real_document_mcp_guard_workflow']:
                raise RuntimeError('Legal baseline did not complete; failure and recovery workflows cannot establish availability and were not exercised')
            # Observe placement while the model used by this real workflow is
            # still resident. Later restart recovery may legitimately reuse a
            # validated semantic cache entry without loading the model again.
            report['physical_runtime'] = physical_runtime(project, report['model_artifacts'], profile=args.runtime_profile,
                require_business=args.runtime_profile == 'gpu')
            report['checks']['actual_physical_runtime_verified'] = report['physical_runtime']['passed']
            if args.runtime_profile == 'gpu':
                # The same functional contract runs against separate GPU workers.
                # VRAM residency is measured after real inference, never inferred
                # from device visibility or the existence of CUDA libraries.
                proof=compose('exec','-T','gateway-a','python','-',input=(ROOT/'scripts/preflight_container.py').read_text(),capture_output=True,text=True,check=False)
                report['functional_preflight']=json.loads(proof.stdout)
                report['checks']['gpu_runtime_full_functional_preflight']=report['functional_preflight']['passed']
                if proof.returncode or not report['functional_preflight']['passed']:
                    raise RuntimeError('GPU functional preflight failed; individual results retained in functional_preflight')
                residency={}
                for service in ('guard-worker','business-worker'):
                    sample=compose('exec','-T',service,'python','-c',
                        'import json,urllib.request; print(urllib.request.urlopen("http://127.0.0.1:11434/api/ps").read().decode())',capture_output=True,text=True)
                    residency[service]=json.loads(sample.stdout)['models']
                report['gpu_model_residency']=residency
                report['checks']['both_models_actually_resident_in_vram']=all(any(model.get('size_vram',0)>0 for model in models) for models in residency.values())
                concurrency=compose('exec','-T','gateway-a','python','-',input=(ROOT/'scripts/gpu_concurrency_probe.py').read_text(),capture_output=True,text=True,check=False)
                report['gpu_concurrency']=json.loads(concurrency.stdout)
                report['checks']['gpu_independent_watchdog_during_other_inference']=report['gpu_concurrency']['passed']
                if concurrency.returncode or not report['gpu_concurrency']['passed']:
                    raise RuntimeError('GPU concurrency preflight failed; measured results retained in gpu_concurrency')
            before=receipt_count()
            compose('stop','opa-a')
            failed=direct('gateway-a','GET','/health/ready')
            action['idempotency_key']=uuid.uuid4().hex
            denied=direct('gateway-a','POST','/actions',action,run['workload_token'])
            denied_outcome=denied['http_status']>=400 or denied['body'].get('status') in ('blocked','failed','output_blocked','outcome_unknown')
            report['checks']['opa_down_denies_action_without_side_effect']=failed['http_status']==503 and denied_outcome and receipt_count()==before
            compose('start','opa-a')
            recovered=wait_ready(client)
            report['checks']['opa_restart_auto_restages_current_generation']=recovered['generation']==initial['generation']
            before=receipt_count()
            compose('stop','postgres')
            action['idempotency_key']=uuid.uuid4().hex
            denied=direct('gateway-a','POST','/actions',action,run['workload_token'])
            compose('start','postgres')
            wait_ready(client)
            report['checks']['database_down_denies_dispatch']=denied['http_status']>=400 and receipt_count()==before
            before=receipt_count()
            compose('stop','guard-worker')
            action['idempotency_key']=uuid.uuid4().hex
            denied=direct('gateway-a','POST','/actions',action,run['workload_token'])
            report['checks']['guard_unavailable_denies_effect']=receipt_count()==before and (denied['http_status']>=400 or denied['body'].get('status') in ('blocked','failed','output_blocked','outcome_unknown'))
            # A restarted worker loses its prepared generations. The gateway must
            # fail closed until the current snapshot has been acknowledged again.
            compose('start','guard-worker')
            workers_lost_config=direct('gateway-a','GET','/health/ready')
            report['worker_restart_immediate_health']=workers_lost_config['http_status']
            recovered=wait_ready(client)
            report['checks']['worker_restart_auto_prepares_current_generation']=recovered['generation']==initial['generation']
            # The watcher may already have acknowledged the generation before
            # the first HTTP sample. Both replicas must be ready on that same
            # generation before testing persistence through a gateway restart.
            compose('restart','gateway-a','gateway-b')
            restored=wait_ready(client)
            report['checks']['restart_reprepares_current_generation']=restored['generation']==initial['generation']
            action['idempotency_key']=uuid.uuid4().hex
            completed=client.post('/actions',json=action,headers={'Authorization':'Bearer '+run['workload_token']})
            report['checks']['durable_run_usable_after_worker_and_gateway_restart']=completed.status_code==200 and completed.json().get('status')=='completed'
        report['passed']=all(report['checks'].values())
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:300]
        if isinstance(exc,subprocess.CalledProcessError) and exc.stderr:
            detail=exc.stderr
            for name in NAMES:
                value=os.environ.get(name)
                if value:
                    detail=detail.replace(value,'[redacted]')
            report['process_error']=detail[-2000:]
    finally:
        report['producer_sources'] = sources
        report['producer_source_stable'] = sources == producer_sources(*sources)
        report['passed'] = report['passed'] and report['producer_source_stable']
        report['status']='prepared_only' if args.prepare_only and report.get('ready_prepared') else 'passed' if report['passed'] else 'failed'
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        target.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        if not args.keep:
            # This exact randomly generated project was created above. External
            # model storage and every other Compose project remain untouched.
            compose('down','--volumes','--remove-orphans')
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
