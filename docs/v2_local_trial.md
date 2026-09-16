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
# Load XHTPI_V2_SECRET_KEY from your local protected environment (never commit it).
export XHTPI_V2_PORT=5056
./scripts/run_v2_local.sh
```

Open `http://127.0.0.1:5056/login`. Add master data in this order: Bank Account,
Customer, Exporter/Factory, Product, Freight Forwarder and Freight Quote. Then
create the first PI, move it through the lifecycle, update facts, work Dashboard
tasks, and generate documents from the order page.

Stop the foreground development server with `Ctrl-C`. Do not kill SQLite while
a form submission is in progress.

## Automatic pre-start verification and backup

`run_v2_local.sh` requires an explicit absolute SQLite file URL and a Secret Key.
It uses `.venv/bin/python`, falling back to `venv/bin/python`; no system Python
fallback is used. The port and `v2.app` entrypoint are unchanged.

Before Flask starts, `prepare_v2_local_start.py` opens the source read-only,
checks `integrity_check`, `foreign_key_check`, required V2 tables/columns and
the current Alembic revision. It never migrates. Missing, corrupt, incompatible,
in-memory or V1 instance-directory databases are refused.

Python's SQLite online backup API creates a standalone backup, including
committed WAL contents, in `backups/` beside the selected V2 database (normally
`instance_v2/backups/`). Names use sortable UTC timestamps and a collision-resistant
suffix: `database-v2-YYYYMMDD-HHMMSS-microseconds-random.db`.
Existing backups are never overwritten or automatically removed. Temporary
`.partial` files are removed on handled failures; after an abrupt process kill,
a leftover `.partial` is not a verified backup and must not be used for recovery.

The backup is checked again before publication. Console output shows the resolved
source path, revision, verified backup path and backup SHA-256. Any failed check
or backup prevents Flask from starting. Keep secrets in your protected local
environment or an ignored `.env` file; the launcher does not automatically load `.env`.
The entire `instance_v2/` tree, local `.env` files and generated documents are ignored.

## Manual recovery — V2 only

Stop the application before recovery. Independently preserve the current database
first, including any WAL state; use SQLite's backup API if the database is readable.
If it is corrupt, preserve its database and journal/WAL files for diagnosis before
attempting recovery. Do not overwrite your only current copy.

Select a verified `.db` backup, check its recorded SHA-256 and run both SQLite
integrity and foreign-key checks. Restore only to the explicitly confirmed V2
path while the application is stopped; account for existing WAL/journal files
rather than mixing them with the restored database. Never restore into V1's
`instance/` directory. On the next launch, preflight verifies and backs up the
restored V2 database again.

Local automatic backups are not off-site backups. Regularly copy verified backups
to another disk or trusted cloud storage. No passwords or Secret Keys belong in
this document or Git.
