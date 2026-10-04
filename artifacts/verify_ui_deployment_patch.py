"""UI-only delivery evidence. Metadata, local health/TCP and file hashes only.

--capture records the original deployment. --verify checks the replacement
gateways, unchanged other containers/configuration and four actual TCP sessions.
No inference, credential output, policy write or container mutation is performed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'.state/ux-audit-cycle'
PROJECTS=('actiongate','actiongate-gpu-0b92f97d')
GATEWAYS=('gateway-a','gateway-b')
BASELINE=PRIVATE/'production-before.json'
OUTPUT=ROOT/'artifacts/reports/ui-deployment-patch.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    return subprocess.check_output(args,text=True)


def frozen_sources():
    expected=json.loads((ROOT/'.state/benchmark-fix/promotion.json').read_text())['source_files']
    assert len(expected)==95
    changed=[p for p,value in expected.items() if sha(ROOT/p)!=value]
    if changed:raise RuntimeError('Frozen sources changed: '+','.join(changed))
    return expected


def metadata():
    result={}
    for project in PROJECTS:
        ids=run('docker','ps','-aq','--filter','label=com.docker.compose.project='+project).split()
        for item in json.loads(run('docker','inspect',*ids)):
            name=item['Name'].lstrip('/'); env=dict(x.split('=',1) for x in item['Config']['Env'])
            host=item['HostConfig']
            result[name]={'id':item['Id'],'image_id':item['Image'],'state':item['State']['Status'],
                'health':item['State'].get('Health',{}).get('Status'),
                'networks':sorted(item['NetworkSettings']['Networks']),
                'safe_environment':{k:env.get(k) for k in ('PUBLIC_BASE_URL','UVICORN_TIMEOUT_KEEP_ALIVE')},
                'limits':{k:host.get(k) for k in ('Memory','NanoCpus','PidsLimit','ReadonlyRootfs','CapDrop','SecurityOpt')},
                'mounts':[{k:m.get(k) for k in ('Type','Name','Source','Destination','RW')} for m in item['Mounts']]}
    return result


def signed_configuration():
    code="""import json;from actiongate.policies import snapshot,verify_stored_snapshot
s=snapshot();v=verify_stored_snapshot(s)
print(json.dumps({'generation':s['generation'],'digest':s['digest'],'guard_artifact':v['guard_artifact'],'model_artifacts':v['model_artifacts'],'configuration':s['configuration']}))
"""
    return {p:json.loads(run('docker','exec',p+'-gateway-a-1','python','-c',code)) for p in PROJECTS}


def preserved_media():
    excluded={'ActionGate-UI-workflows.mp4','ui-workflow-start.webm','ui-workflow-result.webm'}
    suffixes={'.mp4','.webm','.mp3','.wav','.vtt','.srt','.pdf','.pptx','.png'}
    return {p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'artifacts/submission').rglob('*')
            if p.is_file() and p.suffix.lower() in suffixes and p.name not in excluded}


def ui_inputs():
    selected=[p for p in (ROOT/'ui').iterdir() if p.name in {'src','public','index.html','vite.config.ts'}
              or p.suffix=='.json' and p.name.startswith(('package','tsconfig'))]
    files=[p for item in selected for p in (item.rglob('*') if item.is_dir() else [item]) if p.is_file()]
    return {p.relative_to(ROOT).as_posix():sha(p) for p in files}


def capture():
    assert not BASELINE.exists(),'Baseline must not be overwritten'
    value={'captured_at':datetime.now(timezone.utc).isoformat(),'containers':metadata(),
           'signed_configuration':signed_configuration(),'frozen_sources':frozen_sources(),
           'ui_inputs':ui_inputs(),'preserved_media':preserved_media(),'compose_sha256':sha(ROOT/'compose.yaml'),
           'producer_sha256':sha(Path(__file__))}
    BASELINE.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'captured':True,'containers':len(value['containers']),'generations':{p:v['generation'] for p,v in value['signed_configuration'].items()}}))


def verify():
    before=json.loads(BASELINE.read_text()); after=metadata()
    report={'status':'running','scope':'UI accessibility delivery revision v0.1.1; core application0.1.0; no inference in this verification',
        'recorded_at':datetime.now(timezone.utc).isoformat(),'before':before,'after':after,'checks':{},
        'producer':'artifacts/verify_ui_deployment_patch.py','producer_sha256':sha(Path(__file__))}
    try:
        spec=importlib.util.spec_from_file_location('original_keepalive',ROOT/'artifacts/verify_keepalive_runtime.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        def mounts(value):
            normalized=[]
            for item in value:
                item=dict(item); source=item.get('Source') or ''
                if len(source)>2 and source[1]==':' and source[2] in ('/','\\'):
                    item['Source']=source.replace('\\','/')
                normalized.append(item)
            return helper.canonical_mounts(normalized)
        def same_metadata(a,b):
            return all(a[k]==b[k] for k in a if k!='mounts') and mounts(a['mounts'])==mounts(b['mounts'])
        report['mount_comparison']='Named fields sorted by destination. Normalize only Windows drive separators and Docker Desktop /run/desktop/mnt/host/<drive>/ mapping to the identical drive path; preserve raw observations above.'
        report['tcp_probe_producer_sha256']=sha(ROOT/'artifacts/verify_keepalive_runtime.py')
        names=[p+'-'+role+'-1' for p in PROJECTS for role in GATEWAYS]
        checks=report['checks']
        checks['same_container_names']=set(before['containers'])==set(after)
        checks['all_other_containers_unchanged']=all(same_metadata(after[n],v) for n,v in before['containers'].items() if n not in names)
        checks['frozen95_unchanged']=frozen_sources()==before['frozen_sources']
        checks['reviewed_ui_inputs_unchanged']=ui_inputs()==before['ui_inputs']
        checks['preserved_pitch_audio_song_pdf_bytes']=preserved_media()==before['preserved_media']
        checks['compose_unchanged']=sha(ROOT/'compose.yaml')==before['compose_sha256']
        prior_producer=ROOT/'artifacts/history/ui-patch-verifier-mount-representation/first-producer.py'
        checks['original_capture_producer_preserved']=sha(prior_producer)==before['producer_sha256']
        report['signed_configuration_after']=signed_configuration()
        checks['generation_models_guard_policy_unchanged']=report['signed_configuration_after']==before['signed_configuration']
        expected_image=json.loads(run('docker','image','inspect','actiongate-gateway:ui-0.1.1'))[0]['Id']
        report['new_gateway_image_id']=expected_image
        actual_mounts=json.loads(run('docker','inspect',*names))
        report['current_only_mount_mode_propagation']={c['Name'].lstrip('/'):[
            {k:m.get(k) for k in ('Type','Name','Source','Destination','RW','Mode','Propagation')}
            for m in c['Mounts']] for c in actual_mounts}
        report['mount_capture_limitation']='Original capture did not include Mode/Propagation. Their complete actual readback is current-only, checked against declared readonly bind and writable named-volume contracts, not claimed as historical equality.'
        checks['current_mount_modes_and_private_propagation']=all(
            len(rows)==len(after[name]['mounts']) and all(
                (m['Type']=='bind' and m['RW'] is False and m['Mode']=='ro' and m['Propagation']=='rprivate')
                or (m['Type']=='volume' and m['Name'] and m['RW'] is True and m['Mode']=='rw' and m['Propagation']=='')
                for m in rows) for name,rows in report['current_only_mount_mode_propagation'].items())
        with ThreadPoolExecutor(max_workers=4) as pool:
            report['tcp_probes']=dict(pool.map(helper.probe,names))
        image_code="""import hashlib,json,pathlib
r=pathlib.Path('/app');h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'backend':{p.relative_to(r).as_posix():h(p) for p in (r/'backend').rglob('*.py')},'ui_build':json.loads((r/'build-inputs/ui.json').read_text()),'ui_outputs':{p.relative_to(r).as_posix():h(p) for p in (r/'ui/dist').rglob('*') if p.is_file()}}))
"""
        report['gateway_files']={}
        expected_backend={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'backend').rglob('*.py')}
        approved=json.loads((PRIVATE/'round2-reviewed-build-manifest.json').read_text())
        for name in names:
            previous,current=before['containers'][name],after[name]
            checks[name+'_new_id_ready_pinned_image']=current['id']!=previous['id'] and current['health']=='healthy' and current['image_id']==expected_image
            checks[name+'_limits_networks_env_mounts_unchanged']=all(current[k]==previous[k] for k in ('limits','networks','safe_environment')) and mounts(current['mounts'])==mounts(previous['mounts'])
            probe=report['tcp_probes'][name]
            checks[name+'_actual_tcp_after_6_seconds']=probe['effective_cli_timeout_keep_alive']==30 and probe['idle_seconds']>=6.05 and probe['auto_open']==0 and probe['same_tcp_socket_after_both_responses'] and all(probe[k]['status']==200 and probe[k]['same_socket'] and not probe[k]['will_close'] for k in ('first','second'))
            files=json.loads(run('docker','exec',name,'python','-c',image_code));report['gateway_files'][name]=files
            checks[name+'_reviewed_image_bytes']=files['backend']==expected_backend and files['ui_build']['inputs']==before['ui_inputs'] and files['ui_build']['outputs']==files['ui_outputs'] and files['ui_outputs']=={p:v['sha256'] for p,v in approved['build']['outputs'].items()}
        checks['producer_stable_during_verification']=sha(Path(__file__))==report['producer_sha256']
        report['status']='passed' if all(checks.values()) else 'failed'
    finally:
        if report['status']=='running':report['status']='failed'
        OUTPUT.parent.mkdir(parents=True,exist_ok=True)
        OUTPUT.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'checks':report['checks']}))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['capture','verify']);args=parser.parse_args()
    raise SystemExit(capture() if args.mode=='capture' else verify())
