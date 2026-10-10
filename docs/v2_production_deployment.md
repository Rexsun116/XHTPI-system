# V2 controlled production deployment

V2 production is a separately deployed application. It never uses the V1
`instance/` directory.

## Required environment

Set these values in the process manager or another protected environment;
never commit them:

```sh
export XHTPI_V2_ENV=production
export XHTPI_V2_DATABASE_URL="sqlite:////absolute/path/to/instance_v2/database.db"
export XHTPI_V2_SECRET_KEY="a-long-unique-secret"
```

Optional values:

```sh
# The production launcher allows only a loopback bind behind a reverse proxy.
export XHTPI_V2_GUNICORN_BIND=127.0.0.1:5056
# SQLite deployments intentionally use one Gunicorn worker.
export XHTPI_V2_GUNICORN_WORKERS=1
# Preserve seals and other document assets outside the source checkout if needed.
export XHTPI_DOCUMENT_ASSET_DIR="/absolute/persistent/path/document_assets/stamps"
```

`XHTPI_V2_DATABASE_URL` and `XHTPI_V2_SECRET_KEY` are required. The
production launcher refuses a non-production environment, a non-loopback
Gunicorn bind, and any worker count other than one. Do not set `FLASK_DEBUG`.

## Start

Install the exact application dependencies, including the pinned Gunicorn
version in `requirements.txt`, then run from the project root:

```sh
./scripts/run_v2_production.sh
```

The launcher first runs `prepare_v2_local_start.py`. It verifies that the
configured database is a valid V2 SQLite file, checks integrity, foreign keys,
the full ORM schema, and the Alembic head, then publishes a verified online
SQLite backup beside the database. It never runs a migration. A failed check
or backup prevents Gunicorn from starting.

If the deployed database revision is not the application head, stop here.
Create and verify a separate backup, then run the approved Alembic migration
as an explicit maintenance operation before retrying startup.

## Reverse proxy and HTTPS

Gunicorn listens only on loopback. Terminate TLS at one directly connected
reverse proxy and do not expose the Gunicorn port publicly. The application
trusts exactly one proxy hop for `X-Forwarded-For` and `X-Forwarded-Proto`;
it does not trust forwarded host, port, or prefix headers.

For example, the proxy must send the original host normally and the client
scheme/address headers explicitly:

```nginx
proxy_pass http://127.0.0.1:5056;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Production sessions are Secure, HttpOnly, and SameSite=Lax. HTTPS must remain
enabled at the reverse proxy for Secure cookies to work.

## Persistent data

Back up and preserve all of the following together:

- the SQLite database selected by `XHTPI_V2_DATABASE_URL`, including its WAL
  and SHM files while the application is stopped;
- `static_v2/generated/`, which contains generated PI, Invoice, Packing List,
  Contract, and Booking files;
- `instance_v2/document_assets/`, including electronic seal assets;
- verified backups in the database-adjacent `backups/` directory.

The generated-documents endpoint writes or refreshes document files on demand,
so `static_v2/generated/` must be a writable persistent volume. A database
backup alone does not restore generated files or document assets.

## Pre-release checklist

1. Pin and install the reviewed dependency set from `requirements.txt`.
2. Confirm the database path, revision, integrity, and foreign-key checks.
3. Create and verify an independent SQLite backup before any migration or
   deployment.
4. Back up generated files and document assets to separate durable storage.
5. Confirm Gunicorn is reachable only through the HTTPS reverse proxy.
6. Confirm the deployment secret is unique, protected, and not logged.
7. Run the release smoke checks after deployment.

## Smoke checks

Verify login, Operations Dashboard, Business Overview, All Orders, New Order,
Order Detail, Current Action, customer payment, freight settlement, document
generation, correction, linked CUSTOMER_ORDER / EXPORT_ORDER, lifecycle
completion, and a new login after Sign out. Confirm generated files persist
on the configured volume.

## Rollback

Stop the application first. Preserve the current database, WAL/SHM files,
generated files, and document assets before any rollback. Redeploy the prior
reviewed application revision. Restore the matching verified V2 SQLite backup
and matching generated-file/asset backup only when accepting the loss of all
writes after that backup. Do not use Alembic downgrade as a routine rollback
mechanism after production writes; restore the verified file backup instead.
