# V2 Local Trial Runbook

V2 local trial is not production and never uses `instance/database.db`.

## Initialize once

```bash
mkdir -p instance_v2
venv/bin/python scripts/init_v2_test_db.py instance_v2/database.db
export XHTPI_V2_DATABASE_URL="sqlite:///$PWD/instance_v2/database.db"
export XHTPI_V2_SECRET_KEY="$(openssl rand -hex 32)"
venv/bin/python scripts/create_v2_admin.py admin
```

The initializer refuses an existing file and refuses the V1 instance
directory. Enter the admin password interactively; it is never stored in source.

## Run

```bash
export XHTPI_V2_DATABASE_URL="sqlite:///$PWD/instance_v2/database.db"
export XHTPI_V2_SECRET_KEY="your-persistent-local-secret"
export XHTPI_V2_PORT=5056
./scripts/run_v2_local.sh
```

Open `http://127.0.0.1:5056/login`. Add master data in this order: Bank Account,
Customer, Exporter/Factory, Product, Freight Forwarder and Freight Quote. Then
create the first PI, move it through the lifecycle, update facts, work Dashboard
tasks, and generate documents from the order page.

Stop the foreground development server with `Ctrl-C`. Do not kill SQLite while
a form submission is in progress.

## Backup

Stop the V2 app, then use SQLite's online-safe backup command:

```bash
mkdir -p "$HOME/XHTPI-v2-backups"
sqlite3 instance_v2/database.db ".backup '$HOME/XHTPI-v2-backups/database-v2-YYYYMMDD-HHMMSS.db'"
shasum -a 256 "$HOME/XHTPI-v2-backups/database-v2-YYYYMMDD-HHMMSS.db"
sqlite3 "$HOME/XHTPI-v2-backups/database-v2-YYYYMMDD-HHMMSS.db" "PRAGMA integrity_check;"
```

Never restore over V1. Restore V2 only while the V2 app is stopped and only
after separately preserving the current V2 file.

## Restore V2 only

With the V2 app stopped, first back up the current trial file as above. Verify
the selected backup, then restore only to the explicit V2 path:

```bash
sqlite3 "$HOME/XHTPI-v2-backups/database-v2-YYYYMMDD-HHMMSS.db" "PRAGMA integrity_check;"
cp "$HOME/XHTPI-v2-backups/database-v2-YYYYMMDD-HHMMSS.db" instance_v2/database.db
sqlite3 instance_v2/database.db "PRAGMA integrity_check; PRAGMA foreign_key_check;"
```

Never use `instance/database.db` as either side of a V2 restore command.
