#!/bin/sh
set -eu
: "${XHTPI_V2_DATABASE_URL:?Set XHTPI_V2_DATABASE_URL to an explicit V2 SQLite URL}"
: "${XHTPI_V2_SECRET_KEY:?Set XHTPI_V2_SECRET_KEY}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/venv/bin/python" ]; then
  PYTHON="$ROOT/venv/bin/python"
else
  echo "V2 startup refused: install project .venv/bin/python or venv/bin/python" >&2
  exit 1
fi
cd "$ROOT"
"$PYTHON" "$ROOT/scripts/prepare_v2_local_start.py"
exec "$PYTHON" -m v2.app
