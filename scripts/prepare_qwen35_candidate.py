"""Prepare an isolated, pinned comparison model without changing production pins."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'qwen3.5:4b'
DIGEST = 'sha256:2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd'
TOKENIZER_COMMIT = '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'
TOKENIZER_SHA256 = '5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42'
IMAGE = 'ollama/ollama@sha256:5a5d014aa774f78ebe1340c0d4afc2e35afc12a2c3b34c84e71f78ea20af4ba3'
VOLUME = 'actiongate_qwen35_candidate_models'


def main():
    work = ROOT/'.state/model-candidates/qwen35'
    models = work/'models'
    models.mkdir(parents=True, exist_ok=True)
    registry_url = 'https://registry.ollama.ai/v2/library/qwen3.5/manifests/4b'
    with urllib.request.urlopen(registry_url, timeout=60) as source:
        registry = source.read()
    if 'sha256:'+hashlib.sha256(registry).hexdigest() != DIGEST:
        raise RuntimeError('Candidate registry tag moved; refusing to advance the approved pin')
    tokenizer_url = f'https://huggingface.co/Qwen/Qwen3.5-4B/resolve/{TOKENIZER_COMMIT}/tokenizer.json'
    tokenizer = models/'tokenizer.json'
    if not tokenizer.exists() or hashlib.sha256(tokenizer.read_bytes()).hexdigest() != TOKENIZER_SHA256:
        with urllib.request.urlopen(tokenizer_url, timeout=120) as source:
            payload = source.read()
        if hashlib.sha256(payload).hexdigest() != TOKENIZER_SHA256:
            raise RuntimeError('Candidate tokenizer digest mismatch')
        tokenizer.write_bytes(payload)
    from tokenizers import Tokenizer
    Tokenizer.from_file(str(tokenizer))
    subprocess.run(['docker','volume','create',VOLUME],check=True,capture_output=True)
    print('Preparing pinned candidate weights in the dedicated volume.',flush=True)
    with (work/'pull.log').open('w',encoding='utf-8') as log:
        subprocess.run(['docker','run','--rm','--name','actiongate-prepare-qwen35','--network','bridge',
            '--cpus','2','--memory','2g','-v',VOLUME+':/root/.ollama/models','--entrypoint','/bin/sh',IMAGE,'-c',
            'ollama serve >/tmp/ollama-prepare.log 2>&1 & pid=$!; trap "kill $pid" EXIT; sleep 2; ollama pull qwen3.5:4b'],
            check=True,stdout=log,stderr=subprocess.STDOUT)
    raw = subprocess.check_output(['docker','run','--rm','--network','none','-v',VOLUME+':/root/.ollama/models:ro',
        '--entrypoint','/bin/cat',IMAGE,'/root/.ollama/models/manifests/registry.ollama.ai/library/qwen3.5/4b'])
    if 'sha256:'+hashlib.sha256(raw).hexdigest() != DIGEST:
        raise RuntimeError('Downloaded candidate differs from approved digest')
    (work/'ollama-registry-manifest.json').write_bytes(raw)
    manifest = {'schema_version':1,'model':MODEL,'digest':DIGEST,'format':'GGUF','family':'Qwen3.5',
        'quantization':'Q4_K_M','license':'Apache-2.0','source':'https://ollama.com/library/qwen3.5:4b',
        'tokenizer_source':tokenizer_url,'tokenizer_commit':TOKENIZER_COMMIT,'tokenizer_sha256':TOKENIZER_SHA256,
        'context_tokens':8192,'max_input_tokens':4096,'think':False,'temperature':0,'seed':42,'runtime':'ollama:0.18.2',
        'prepared_at':datetime.now(timezone.utc).isoformat()}
    (models/'model-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (models/'cloud-model-manifest.json').write_bytes((ROOT/'models/cloud-model-manifest.json').read_bytes())
    report = {'status':'prepared_only','inference_verified':False,'candidate':MODEL,'model_digest':DIGEST,
        'tokenizer_sha256':TOKENIZER_SHA256,'tokenizer_parsed':True,'model_volume':VOLUME,
        'production_pins_unchanged':True,'registry_layers':json.loads(raw)['layers'],
        'prepared_at':manifest['prepared_at']}
    (ROOT/'artifacts/qwen35-candidate-preparation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
