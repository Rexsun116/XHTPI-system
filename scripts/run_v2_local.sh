#!/bin/sh
set -eu
: "${XHTPI_V2_DATABASE_URL:?Set XHTPI_V2_DATABASE_URL to an explicit V2 SQLite URL}"
: "${XHTPI_V2_SECRET_KEY:?Set XHTPI_V2_SECRET_KEY}"
case "$XHTPI_V2_DATABASE_URL" in
  *"/instance/database.db") echo "Refusing V1 database" >&2; exit 2 ;;
esac
exec "$(dirname "$0")/../venv/bin/python" -m v2.app
