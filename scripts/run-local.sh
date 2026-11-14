#!/usr/bin/env bash
# Arranca JDM Viral Clips AI en local (API + panel en un solo puerto)
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r apps/api/requirements.txt requests pytest httpx
echo "Panel + API en http://127.0.0.1:8077  (Ctrl+C para detener)"
DATABASE_URL="sqlite:///$PWD/data/jdm.db" .venv/bin/uvicorn main:app --app-dir apps/api --host 127.0.0.1 --port 8077
