"""Bounded synthetic model-load diagnosis inside an isolated worker container.

It uses a separate loopback port and always kills its own process tree. No
worker authentication token is passed to the diagnostic server or its logs.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

import httpx
import psutil


manifest = json.loads(Path('/app/models/model-manifest.json').read_text())
env = dict(os.environ, OLLAMA_HOST='127.0.0.1:11435', OLLAMA_NO_CLOUD='1', HOME='/tmp/ollama-diagnostic',
           OLLAMA_NUM_PARALLEL='1', OLLAMA_MAX_LOADED_MODELS='1', OLLAMA_MAX_QUEUE='1', OLLAMA_DEBUG='1', LLAMA_ARG_CACHE_RAM='512')
env.pop('WORKER_TOKEN', None)
report = {'model': manifest['model'], 'runtime': manifest['runtime'], 'deadline_seconds': 90,
          'configured_backend': env.get('OLLAMA_LLM_LIBRARY')}
began = time.monotonic()
with tempfile.TemporaryFile() as log:
    process = subprocess.Popen(['/usr/bin/ollama', 'serve'], env=env, stdout=log, stderr=log, start_new_session=True)
    try:
        with httpx.Client(timeout=90) as client:
            for _ in range(40):
                try:
                    response = client.get('http://127.0.0.1:11435/api/version', timeout=1)
                    response.raise_for_status()
                    report['version'] = response.json()
                    break
                except httpx.HTTPError:
                    time.sleep(.25)
            response = client.post('http://127.0.0.1:11435/api/chat', json={
                'model': manifest['model'], 'messages': [{'role': 'user', 'content': 'Reply ready.'}],
                'think': False, 'stream': False, 'options': {'num_ctx': 8192, 'num_predict': 1,
                    'num_thread': 2, 'num_gpu': -1, 'temperature': 0, 'seed': 42}})
            report['http_status'] = response.status_code
            report['response'] = response.json()
            report['residency'] = client.get('http://127.0.0.1:11435/api/ps').json()
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'reason': str(error)}
    finally:
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            descendants = []
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)
        gone, alive = psutil.wait_procs(descendants, timeout=5)
        report['stop_confirmed'] = all(child.status() == psutil.STATUS_ZOMBIE for child in alive)
        report['seconds'] = time.monotonic()-began
        log.seek(0)
        report['upstream_log'] = log.read().decode('utf-8', errors='replace')[-50000:]
print(json.dumps(report))
