#!/bin/sh
# Controlled V2 production launcher for a loopback-only Gunicorn worker.
set -eu

: "${XHTPI_V2_DATABASE_URL:?Set XHTPI_V2_DATABASE_URL to an explicit V2 SQLite URL}"
: "${XHTPI_V2_SECRET_KEY:?Set XHTPI_V2_SECRET_KEY}"

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/venv/bin/python" ]; then
  PYTHON="$ROOT/venv/bin/python"
else
  echo "V2 production startup refused: install project .venv/bin/python or venv/bin/python" >&2
  exit 1
fi

if [ "${XHTPI_V2_ENV:-production}" != "production" ]; then
  echo "V2 production startup refused: XHTPI_V2_ENV must be production" >&2
  exit 1
fi
export XHTPI_V2_ENV=production

BIND="${XHTPI_V2_GUNICORN_BIND:-127.0.0.1:5056}"
case "$BIND" in
  127.0.0.1:*|localhost:*|\[::1\]:*) ;;
  *)
    echo "V2 production startup refused: Gunicorn must bind to loopback behind a reverse proxy" >&2
    exit 1
    ;;
esac

if [ "${XHTPI_V2_GUNICORN_WORKERS:-1}" != "1" ]; then
  echo "V2 production startup refused: SQLite production runtime requires exactly one Gunicorn worker" >&2
  exit 1
fi

cd "$ROOT"
"$PYTHON" "$ROOT/scripts/prepare_v2_local_start.py"
exec "$PYTHON" -m gunicorn --bind "$BIND" --workers 1 --access-logfile - --error-logfile - v2.wsgi:app
