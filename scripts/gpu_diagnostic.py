"""Operator-only CUDA startup diagnosis with a fixed synthetic one-token input.

Run inside an isolated acceptance worker, never against the reference services.
All spawned diagnostic processes are stopped before returning initialization logs.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import httpx
import psutil


log = Path('/tmp/actiongate-gpu-init.log')
library = os.getenv('GPU_DIAGNOSTIC_LIBRARY', '')
if library not in ('', 'cuda_v12', 'cuda_v13'):
    raise ValueError('Only the pinned CUDA backends can be diagnosed')
with log.open('w') as output:
    env = dict(os.environ, OLLAMA_HOST='127.0.0.1:11435', OLLAMA_NO_CLOUD='1',
               HOME='/tmp/ollama', OLLAMA_LLM_LIBRARY=library)
    env.pop('WORKER_TOKEN', None)
    process = subprocess.Popen(['/usr/bin/ollama', 'serve'], env=env, stdout=output, stderr=output, start_new_session=True)
    result = {}
    try:
        with httpx.Client(timeout=1) as client:
            for _ in range(60):
                try:
                    client.get('http://127.0.0.1:11435/api/tags').raise_for_status()
                    break
                except httpx.HTTPError:
                    time.sleep(.25)
        with httpx.Client(timeout=60) as client:
            response = client.post('http://127.0.0.1:11435/api/chat', json={
                'model':'qwen3:4b-instruct-2507-q4_K_M', 'messages':[{'role':'user','content':'Reply ready.'}],
                'stream':False, 'think':False, 'options':{'num_gpu':-1,'num_predict':1,'num_ctx':8192,'num_thread':2}})
            result = {'http_status':response.status_code, 'result':response.json(),
                      'residency':client.get('http://127.0.0.1:11435/api/ps').json()}
    except Exception as error:
        result = {'error':type(error).__name__}
    finally:
        descendants = psutil.Process(process.pid).children(recursive=True)
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        _, alive = psutil.wait_procs(descendants, timeout=5)
        result['diagnostic_processes_stopped'] = process.poll() is not None and not alive
print(json.dumps({**result, 'synthetic_only':True, 'requested_library':library,
                  'initialization_log':log.read_text()[-18000:]}, indent=2))
