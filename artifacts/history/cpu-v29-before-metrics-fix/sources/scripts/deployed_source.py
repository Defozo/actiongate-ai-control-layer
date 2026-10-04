"""Bind deployed image files and build inputs to the reviewed source tree.

Only file digests, public policy/model artifacts and container resource limits
are collected. No environment or secret values are read into the report.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from runtime_evidence import producer_sources

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(paths):
    files = [child for path in paths for child in (path.rglob('*') if path.is_dir() else [path]) if child.is_file()]
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(files)}


def remote(container, code):
    return json.loads(subprocess.check_output(['docker','exec','-i',container,'python','-c',code],text=True))


def image_metadata(container):
    value = json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0]
    limits = value['HostConfig']
    return {'container_id':value['Id'],'image_id':value['Image'], 'image_ref':value['Config']['Image'],
        'read_only':limits['ReadonlyRootfs'],'memory_limit_bytes':limits['Memory'],
        'cpu_limit_nanocpus':limits['NanoCpus'],'pids_limit':limits['PidsLimit'],
        'networks':sorted(value['NetworkSettings']['Networks'])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='actiongate')
    parser.add_argument('--output',default='artifacts/deployed-source.json')
    parser.add_argument('--runtime-dockerfile', default='runtime/Dockerfile',
                        choices=('runtime/Dockerfile', 'runtime/Dockerfile.ollama032'))
    args = parser.parse_args()
    sources = producer_sources('scripts/deployed_source.py', 'scripts/runtime_evidence.py')
    backend = tree(list((ROOT/'backend').rglob('*.py')))
    ui_paths = [p for p in (ROOT/'ui').iterdir() if p.name in {'src','public','index.html','vite.config.ts'}
                or p.suffix == '.json' and p.name.startswith(('package','tsconfig'))]
    expected_ui = tree(ui_paths)
    gateway_code = '''import hashlib,json,pathlib
r=pathlib.Path('/app')
def hashes(paths):
 return {p.relative_to(r).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths) if p.is_file()}
from actiongate.policies import snapshot,verify_stored_snapshot
s=snapshot();signed=verify_stored_snapshot(s)
print(json.dumps({'backend':hashes((r/'backend').rglob('*.py')),'ui_build':json.loads((r/'build-inputs/ui.json').read_text()),
'ui_outputs':hashes((r/'ui/dist').rglob('*')),'recipes':hashes([r/'build-inputs/Dockerfile',r/'build-inputs/image_manifest.mjs',r/'pyproject.toml',r/'uv.lock']),
'generation':s['generation'],'guard_artifact':signed['guard_artifact'],'model_artifacts':signed['model_artifacts'],
'semantic_configuration':s['configuration']['semantic'],'local_resource_configuration':s['configuration']['local_resources'],
'performance':s['configuration']['performance']}))'''
    worker_recipe = '/app/build-inputs/runtime/' + Path(args.runtime_dockerfile).name
    worker_code = '''import hashlib,json,pathlib,urllib.request
paths=['/app/worker.py',RECIPE,'/app/build-inputs/runtime/requirements.lock']
print(json.dumps({'files':{p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in paths},
'ready':json.loads(urllib.request.urlopen('http://127.0.0.1:8090/health/ready').read())}))'''
    worker_code = worker_code.replace('RECIPE', repr(worker_recipe))
    report = {'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'project':args.project,
        'runtime_dockerfile':args.runtime_dockerfile,
        'host_build_inputs':tree([ROOT/'Dockerfile',ROOT/args.runtime_dockerfile,ROOT/'runtime/requirements.lock',ROOT/'compose.yaml',
            ROOT/'deploy/compose.gpu.yaml',ROOT/'scripts/image_manifest.mjs',ROOT/'pyproject.toml',ROOT/'uv.lock']),
        'host_backend':backend,'host_ui_inputs':expected_ui,'gateways':{},'workers':{},'checks':{}}
    for role in ('gateway-a','gateway-b'):
        container=f'{args.project}-{role}-1'
        value=remote(container,gateway_code)
        report['gateways'][role]={**image_metadata(container),**value}
        report['checks'][role+'_backend_matches']=value['backend']==backend
        report['checks'][role+'_ui_build_inputs_match']=value['ui_build']['inputs']==expected_ui
        report['checks'][role+'_ui_outputs_match']=value['ui_build']['outputs']==value['ui_outputs']
        recipes={'build-inputs/Dockerfile':'Dockerfile','build-inputs/image_manifest.mjs':'scripts/image_manifest.mjs',
                 'pyproject.toml':'pyproject.toml','uv.lock':'uv.lock'}
        report['checks'][role+'_recipes_match']=all(value['recipes'][key]==digest(ROOT/path) for key,path in recipes.items())
    for role in ('guard-worker','business-worker'):
        container=f'{args.project}-{role}-1'
        value=remote(container,worker_code)
        report['workers'][role]={**image_metadata(container),**value}
        paths={'/app/worker.py':'runtime/worker.py',worker_recipe:args.runtime_dockerfile,
               '/app/build-inputs/runtime/requirements.lock':'runtime/requirements.lock'}
        report['checks'][role+'_source_and_recipe_match']=all(value['files'][key]==digest(ROOT/path) for key,path in paths.items())
    a,b=report['gateways'].values()
    report['checks']['replicas_same_generation_and_artifacts']=all(a[key]==b[key] for key in
        ('generation','guard_artifact','model_artifacts','semantic_configuration','local_resource_configuration'))
    for role, model in [('guard-worker','local-guard'),('business-worker','local-business')]:
        ready=report['workers'][role]['ready']; artifact=a['model_artifacts'][model]
        report['checks'][role+'_matches_signed_artifact']=all([
            ready['model_manifest_sha256']==artifact['sha256'],ready['digest']==artifact['manifest']['digest'],
            ready['tokenizer_sha256']==artifact['manifest']['tokenizer_sha256'],ready.get('runtime')==artifact['manifest']['runtime'],
            a['generation'] in ready['prepared_generations']])
    report['passed']=all(report['checks'].values())
    report['producer_sources'] = sources
    report['producer_source_stable'] = sources == producer_sources(*sources)
    report['passed'] = report['passed'] and report['producer_source_stable']
    destination=ROOT/args.output
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    configuration={key:a[key] for key in ('generation','guard_artifact','model_artifacts','semantic_configuration','local_resource_configuration','performance')}
    configuration['workers']={role:report['workers'][role+'-worker']['ready'] for role in ('guard','business')}
    configuration['verified_at']=report['generated_at']
    configuration['source_evidence_passed']=report['passed']
    destination.with_name('deployed-configuration.json').write_text(json.dumps(configuration,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'backend_files':len(backend),'ui_inputs':len(expected_ui),
        'checks':report['checks'],'report':args.output}))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
