FROM ollama/ollama@sha256:57f573b47f1f71ebb445789f279fe3e596a8beab182f7cf486db9205bad87c5a AS ollama
FROM python@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS worker-base
COPY --from=ollama /usr/bin/ollama /usr/bin/ollama
# Ollama 0.32 uses the separate llama-server with these adjacent shared libraries.
COPY --from=ollama /usr/lib/ollama/lib*.so* /usr/lib/ollama/
COPY --from=ollama /usr/lib/ollama/llama-server /usr/lib/ollama/
COPY runtime/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
WORKDIR /app
COPY runtime/worker.py /app/worker.py
COPY models /app/models
RUN useradd --uid 10001 --create-home worker
USER 10001
ENV OLLAMA_MODELS=/models PYTHONUNBUFFERED=1 OLLAMA_NO_CLOUD=1
ENTRYPOINT ["python", "-m", "uvicorn", "worker:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]

FROM worker-base AS worker-gpu
COPY --from=ollama /usr/lib/ollama/cuda_v12 /usr/lib/ollama/cuda_v12
COPY runtime/licenses /usr/share/licenses/actiongate-gpu
COPY runtime/licenses/ollama-0.32.0.txt /usr/share/licenses/actiongate/ollama.txt
ENV LD_LIBRARY_PATH=/usr/local/nvidia/lib:/usr/local/nvidia/lib64 OLLAMA_LLM_LIBRARY=cuda_v12
COPY runtime/Dockerfile runtime/requirements.lock /app/build-inputs/runtime/

FROM worker-base AS worker-cpu
COPY runtime/Dockerfile runtime/requirements.lock /app/build-inputs/runtime/
COPY runtime/licenses/ollama-0.32.0.txt /usr/share/licenses/actiongate/ollama.txt
