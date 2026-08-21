# V1 Archive and V2 Cutover Runbook

Status: approved strategy, not authorization to perform cutover.

## Archive manifest

The immutable V1 archive must contain:

- a byte-for-byte copy of `instance/database.db`;
- generated invoices, packing lists and booking documents required for records;
- the deployed code tag/commit;
- Alembic revision;
- SHA-256 for every archived file or archive bundle;
- archive timestamp and timezone;
- operator and storage location;
- a successful `PRAGMA integrity_check` result.

The archive is mounted/read as read-only. It is never used as the V2 database
and is never overwritten by a V2 initializer.

## V2 initialization and seed

V2 starts from the isolated `migrations_v2` baseline. Seed only active master
data: Users (with password reset), Customers, Exporters, Factories, Freight
Forwarders, Products, Freight Quotes as needed, and Bank Accounts. Do not copy
PI rows automatically. Users manually re-enter open/unpaid/unarrived/unsettled
orders using their original PI business numbers. An explicit re-entry tool may
record source archive references later, but normal V2 runtime has no legacy
fallbacks or Legacy Done import.

## Cutover gate

1. Stop V1 writes and record final hashes/revision/integrity.
2. Create and verify the immutable archive bundle.
3. Initialize a different absolute V2 database path from `v2_0001`.
4. Seed approved master data without PI history.
5. Manually re-enter selected open orders and reconcile each order.
6. Complete user acceptance, document rendering and backup/restore tests.
7. Switch application configuration to V2; retain V1 read-only access.

Rollback before V2 go-live means returning application configuration to the
untouched V1 deployment. After go-live, V2 transactions are not merged back
into V1; operational rollback requires a separately approved procedure.
