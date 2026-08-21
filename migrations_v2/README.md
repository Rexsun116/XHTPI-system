# V2 clean migration lineage

This directory is independent from `migrations/` and never targets the V1
`instance/` directory. Every command requires an explicit absolute
`XHTPI_V2_DATABASE_URL`. The initial revision creates the approved V2 schema
directly; it does not replay V1 migrations.
