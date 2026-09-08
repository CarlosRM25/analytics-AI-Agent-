# syntax=docker/dockerfile:1
#
# Cloud Run image (architecture doc §10): Flask + the agent + a baked-in,
# read-only SQLite snapshot. No database service, no writable disk.
#
#   analytics.db  and  model/seattle_energy/artifacts/model-*.joblib  are
#   committed to the repo and copied in as-is. Refresh them when Seattle
#   republishes the dataset (~annual):
#       python -m ingest.run --source seattle_energy --full-refresh
#       python -m model.seattle_energy.train
#   then re-commit and redeploy.
#
# Build + run locally:
#   docker build -f deploy/Dockerfile -t analytics-agent .
#   docker run --rm -p 8080:8080 -e ANTHROPIC_API_KEY=sk-ant-... analytics-agent
#   curl localhost:8080/health
#
# Deploy (source-based build, from the repo root):
#   gcloud run deploy analytics-agent --source . \
#     --region us-west1 --allow-unauthenticated \
#     --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest
#   (point Cloud Build at deploy/Dockerfile via .gcloudignore / --function or a
#    cloudbuild.yaml; or `docker build` + push to Artifact Registry + `gcloud run
#    deploy --image`.)

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEPLOY_MODE=deployed \
    ANALYST_MODEL=claude-haiku-4-5 \
    MAX_AGENT_ITERS=6 \
    SQLITE_PATH=/app/analytics.db \
    PORT=8080

WORKDIR /app

# deps first for layer caching — the runtime subset, not the full dev set
COPY deploy/requirements.txt ./deploy/requirements.txt
RUN pip install -r deploy/requirements.txt

# app code + the committed analytics.db + model artifact (.dockerignore trims the rest)
COPY . .

# fail the build if the baked-in data/model went missing
RUN test -f analytics.db && ls model/seattle_energy/artifacts/model-*.joblib >/dev/null

# non-root; the read-only DB needs no write access
RUN useradd --create-home --uid 1001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8080

# Cloud Run injects $PORT. One worker (scales to zero, low concurrency), a few
# threads for the mostly-I/O agent loop, a timeout above the worst-case run.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 120 'app.api:create_app()'"]
