FROM node@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd AS ui
WORKDIR /src/ui
COPY ui/package*.json ./
RUN npm ci
COPY ui ./
COPY scripts/image_manifest.mjs /image_manifest.mjs
RUN npm run build && node /image_manifest.mjs /src/ui /ui-build-inputs.json

FROM python@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS backend
WORKDIR /app
RUN pip install --no-cache-dir uv==0.6.14
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY migrations ./migrations
COPY policy ./policy
COPY feeds ./feeds
COPY models ./models
COPY demo ./demo
COPY packages ./packages
COPY tests/corpus /app/tests/corpus
COPY scripts/preflight_container.py ./scripts/preflight_container.py
COPY --from=ui /src/ui/dist ./ui/dist
COPY --from=ui /ui-build-inputs.json ./build-inputs/ui.json
COPY Dockerfile ./build-inputs/Dockerfile
COPY scripts/image_manifest.mjs ./build-inputs/image_manifest.mjs
RUN useradd --uid 10002 --create-home gateway && mkdir -p /app/artifacts /app/spool && chown -R gateway:gateway /app/artifacts /app/spool
ENV PYTHONPATH=/app/backend PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10002
EXPOSE 8000
CMD ["uvicorn", "actiongate.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM backend AS tests
USER root
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    UV_HTTP_TIMEOUT=180 UV_HTTP_RETRIES=5 UV_LINK_MODE=copy uv sync --frozen --no-install-project
COPY tests /app/tests
COPY scripts /app/scripts
USER 10002

FROM python@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS agent-probe
COPY deploy/agent_probe.py /agent_probe.py
USER 10004
CMD ["python", "/agent_probe.py"]
