#!/bin/sh
# Sandbox: API and ingestion worker in ONE container (e.g. a single free Render service).
# Production keeps them as separate services; see NORTHFLANK_DEPLOY.md.
set -e

# Create the private lesson bucket and allow browser uploads from the frontend origin.
# Both steps are idempotent and never stop the API from starting.
python - <<'PY' || echo "sandbox: bucket setup skipped (check AWS_* and NIT_STORAGE_BUCKET)"
from learningx import private_store
if private_store.available():
    store = private_store.require_store()
    try:
        store.client.head_bucket(Bucket=store.bucket)
    except Exception:
        store.client.create_bucket(Bucket=store.bucket)
        print("sandbox: created bucket", store.bucket)
PY
python -m learningx.configure_cors --apply || echo "sandbox: bucket CORS not applied (check NIT_CORS_ORIGINS)"

if [ "$NIT_WORKER_BACKEND" = "northflank_poll" ]; then
  python -m learningx.northflank_worker &
fi

exec uvicorn learningx.api:app --host 0.0.0.0 --port "${PORT:-8080}"
