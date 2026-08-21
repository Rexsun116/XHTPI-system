# XHTPI-system V2 Clean Schema Specification

Status: **FINAL V2 clean baseline specification**. This document does not authorize altering V1, creating a V2 production database, squashing V1 migrations, or performing production cutover.

## 1. Design rules

1. V2 starts from a clean database; V1 is a read-only archive.
2. Required fact and Task completion are separate sources of truth.
3. Money uses `Numeric(18,2)` and `Decimal`; percentages use `Numeric(7,4)`.
4. USD and CNY freight bills remain separate and are never summed without an explicit exchange-rate domain.
5. Lifecycle policy controls both rendering and server-side mutation.
6. V1 adapters belong to an optional import layer, not normal V2 runtime.

## 2. Final V2 physical table list

The `v2_0001` baseline creates exactly these application tables:

`user`, `customer`, `exporter`, `factory`, `freight_forwarder`, `freight_quote`,
`bank_account`, `product`, `pi`, `pi_item`, `product_batch`,
`order_freight_agreement`, `freight_settlement`, `order_task`, `task_activity`,
and `order_correction_session`. Alembic additionally owns `alembic_version`.

It deliberately excludes TradeGroup, Shipment, PaymentRecord, and a mixed-container table.

## 3. Final V2 PI schema — 93 physical fields

`introduced` is the first relevant lifecycle. `editable_until` is the last ordinary editable lifecycle; COMPLETED is always read-only.

| # | Field | Type / nullable | Source of truth / derived | Introduced → editable until | Document dependency | Reminder dependency |
|---:|---|---|---|---|---|---|
|1|id|Integer PK, no|DB|NEW → never user-editable|all relations|Task FK|
|2|pi_no|String(50), no, unique|user|NEW → NEW|all commercial docs|display|
|3|order_type|String(20), no, check SALES/COMMISSION|user|NEW → NEW|document/presenter selection|none|
|4|status|String(20), no, lifecycle check|status transition service|NEW → ARRIVED|document availability|many rules|
|5|pi_date|Date, no|user|NEW → NEW|PI/CI/PL|none|
|6|customer_id|FK Customer, no|user/master|NEW → NEW|snapshot source|presenter/payment|
|7|exporter_id|FK Exporter, nullable; required for SALES|user/master|NEW → NEW|snapshot source|none|
|8|commission_factory_id|FK Factory, nullable; required for COMMISSION|user/master|NEW → NEW|commission docs|none|
|9–15|customer_*_snapshot|name/address/tax/country/contact/phone/email; nullable strings|snapshot at order confirmation|NEW → NEW|PI/CI/PL|customer name fallback|
|16–22|exporter_*_snapshot|same seven fields|snapshot at order confirmation|NEW → NEW|PI/CI/PL|none|
|23|payment_terms|String(200), nullable|contract text, never parsed|NEW → PRE_SHIPMENT|PI/CI|none|
|24|currency|String(10), no|user|NEW → PRE_SHIPMENT|money display|PAY rules|
|25|advance_payment_percent|Numeric(5,2), nullable|user plan|NEW → PRE_SHIPMENT|PI summary|PAY derive|
|26|advance_payment_amount|Numeric(18,2), nullable|explicit override or derived suggestion accepted by user|NEW → PRE_SHIPMENT|PI summary|PAY expected|
|27|advance_received_amount|Numeric(18,2), nullable|user/bank fact|NEW → ARRIVED|receipt display|PAY resolve/settlement|
|28|advance_received_at|DateTime, nullable|user/bank fact|NEW → ARRIVED|receipt display|settlement trigger|
|29|balance_payment_amount|Numeric(18,2), nullable|explicit override or derived suggestion|NEW → PRE_SHIPMENT|PI summary|PAY expected|
|30|balance_received_amount|Numeric(18,2), nullable|user/bank fact|SHIPPED → ARRIVED|receipt display|PAY resolve/settlement|
|31|balance_received_at|DateTime, nullable|user/bank fact|SHIPPED → ARRIVED|receipt display|settlement trigger|
|32|loading_port|String(100), nullable|user|NEW → PRE_SHIPMENT|PI/CI/PL/Booking|none|
|33|destination_port|String(100), nullable|user|NEW → PRE_SHIPMENT|PI/CI/PL/Booking|none|
|34|planned_shipment_date|Date, nullable|user plan|NEW → PRE_SHIPMENT|PI|timeline|
|35|bank_account_id|FK BankAccount, nullable|user selects reusable master|NEW → PRE_SHIPMENT|snapshot source|none|
|36|bank_beneficiary_snapshot|String(200), nullable|immutable order snapshot|NEW → PRE_SHIPMENT|PI/CI remittance|none|
|37|bank_name_snapshot|String(200), nullable|immutable order snapshot|NEW → PRE_SHIPMENT|PI/CI remittance|none|
|38|bank_address_snapshot|Text, nullable|immutable order snapshot|NEW → PRE_SHIPMENT|PI/CI remittance|none|
|39|bank_account_number_snapshot|String(100), nullable|immutable order snapshot|NEW → PRE_SHIPMENT|PI/CI remittance|none|
|40|bank_swift_snapshot|String(50), nullable|immutable order snapshot|NEW → PRE_SHIPMENT|PI/CI remittance|none|
|41|bank_currency_snapshot|String(10), nullable|immutable order snapshot|NEW → PRE_SHIPMENT|PI/CI remittance|none|
|42|bank_remittance_snapshot|Text, nullable|immutable order snapshot|NEW → PRE_SHIPMENT|PI/CI remittance|none|
|43|note|Text, nullable|user|NEW → ARRIVED|PI optional|none|
|44|freight_forwarder_id|FK FreightForwarder, nullable|user|PRE_SHIPMENT → PRE_SHIPMENT|Booking|freight context|
|45|container_loading_at|DateTime, nullable|user logistics fact|PRE_SHIPMENT → PRE_SHIPMENT|Booking|DOC/Driver|
|46|container_location|String(200), nullable|user|PRE_SHIPMENT → PRE_SHIPMENT|Booking|none|
|47|driver_name|String(100), nullable|user/forwarder|PRE_SHIPMENT → PRE_SHIPMENT|operations|Driver rule|
|48|driver_phone|String(50), nullable|user/forwarder|PRE_SHIPMENT → PRE_SHIPMENT|operations|Driver rule|
|49|vehicle_number|String(50), nullable|user/forwarder|PRE_SHIPMENT → PRE_SHIPMENT|operations|Driver rule|
|50|etd|Date, nullable|user/forwarder|PRE_SHIPMENT → SHIPPED correction|CI/PL|departure/doc rules|
|51|eta|Date, nullable|user/forwarder|PRE_SHIPMENT → ARRIVED correction|operations|arrival rule|
|52|actual_departure_date|Date, nullable|user/forwarder fact|SHIPPED → SHIPPED correction|CI/PL|SHIP/FRT|
|53|actual_arrival_date|Date, nullable|user/forwarder fact|SHIPPED → ARRIVED|operations|arrival resolve|
|54|coo_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC COO|
|55|apta_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC APTA|
|56|export_license_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC rule|
|57|customs_docs_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC rule|
|58|coc_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC COC|
|59|coa_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC COA|
|60|original_bl_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC/mail|
|61|obd_electronic_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC|
|62|insurance_original_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC/mail|
|63|insurance_electronic_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC|
|64|original_documents_mail_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|DOC mail|
|65|telex_release_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|PAY/DOC|
|66|settlement_documents_required|Boolean, nullable|user requirement|NEW → PRE_SHIPMENT|none|settlement rules|
|67|other_document_notes|Text, nullable|free text; never parsed|NEW → PRE_SHIPMENT|display only|none|
|68|bill_of_lading_number|String(100), nullable|user/document fact|SHIPPED → ARRIVED|CI/PL|none|
|69|shipping_company|String(100), nullable|user|PRE_SHIPMENT → SHIPPED|CI/PL/Booking|none|
|70|vessel_info|String(100), nullable|user/forwarder|PRE_SHIPMENT → SHIPPED|Booking|none|
|71|booking_number|String(100), nullable|user/forwarder|PRE_SHIPMENT → SHIPPED|Booking|none|
|72|shipping_mark|String(100), nullable|user|NEW → PRE_SHIPMENT|PL/Booking|none|
|73|freight_term|String(50), nullable|user|PRE_SHIPMENT → PRE_SHIPMENT|Booking|none|
|74|contract_number|String(100), nullable|user|NEW → PRE_SHIPMENT|Booking/commercial docs|none|
|75|freight_clause|Text, nullable|user|PRE_SHIPMENT → PRE_SHIPMENT|Booking|none|
|76|waybill_option|String(30), nullable, checked enum|user|PRE_SHIPMENT → SHIPPED|Booking/BL|none|
|77|container_type|String(20), nullable|user|PRE_SHIPMENT → PRE_SHIPMENT|Booking presenter|none|
|78|container_count|Integer, nullable, >0|user|PRE_SHIPMENT → PRE_SHIPMENT|derive `1 × 20GP`|none|
|79|container_number|String(100), nullable|user/forwarder|SHIPPED → SHIPPED|CI/PL|none|
|80|seal_number|String(100), nullable|user/forwarder|SHIPPED → SHIPPED|CI/PL|none|
|81|package_count|Integer, nullable|user/derived from items when homogeneous|PRE_SHIPMENT → SHIPPED|PL/Booking|none|
|82|package_unit|String(20), nullable, enum|user|PRE_SHIPMENT → SHIPPED|PL/Booking|none|
|83|gross_weight_kg|Numeric(18,3), nullable|user/document fact|PRE_SHIPMENT → SHIPPED|PL/Booking|none|
|84|volume_cbm|Numeric(18,3), nullable|user/document fact|PRE_SHIPMENT → SHIPPED|PL/Booking|none|
|85|vgm_kg|Numeric(18,3), nullable|user/document fact|PRE_SHIPMENT → SHIPPED|Booking|none|
|86|commission_rate|Numeric(7,4), nullable; COMMISSION only|user|NEW → PRE_SHIPMENT|commission display|derive amount|
|87|commission_amount|Numeric(18,2), nullable; COMMISSION only|contract total × rate, unless override|NEW → PRE_SHIPMENT|commission display|settlement context|
|88|commission_currency|String(10), nullable; required when commission amount exists|user|NEW → PRE_SHIPMENT|commission display|none|
|89|commission_amount_mode|String(20), nullable, DERIVED/EXPLICIT_OVERRIDE|user decision|NEW → PRE_SHIPMENT|audit display|calculation behavior|
|90|commission_override_reason|Text, nullable; required in override mode|user/audit fact|NEW → PRE_SHIPMENT|audit display|none|
|91|commission_status|String(20), nullable, UNSETTLED/SETTLED|user settlement fact|NEW → ARRIVED|commission display|completion readiness|
|92|created_at|DateTime, not null|system|never editable|audit|none|
|93|updated_at|DateTime, not null|system|never directly editable|audit|none|

The authoritative physical count is also verified from `PRAGMA table_info(pi)`
after `v2_0001`: **93**.

## 4. PIItem schema — 22 physical fields

| Field | Type / nullable | Source / lifecycle | Documents |
|---|---|---|---|
|id|Integer PK, no|DB|relations|
|pi_id|FK PI, no|DB, NEW|all|
|product_id|FK Product, nullable|user/master, NEW|snapshot source|
|factory_id|FK Factory, nullable|user/master, NEW|snapshot source|
|trade_term|String(20), nullable|user, NEW|PI/CI|
|unit_price|Numeric(18,4), no|user, NEW|PI/CI|
|quantity|Numeric(18,3), no|user, NEW|PI/CI/PL|
|quantity_unit|String(20), no|user, NEW|PI/CI/PL|
|line_total|Numeric(18,2), derived|`unit_price × quantity`, ROUND_HALF_UP|PI/CI/payment contract total|
|product_category_snapshot|String(100), nullable|snapshot|documents|
|product_brand_snapshot|String(100), nullable|snapshot|documents|
|product_model_snapshot|String(100), nullable|snapshot|documents|
|product_packaging_snapshot|String(200), nullable|snapshot|PI/PL|
|factory_name_snapshot|String(100), nullable|snapshot|operations|
|factory_address_snapshot|String(200), nullable|snapshot|operations|
|factory_tax_code_snapshot|String(100), nullable|snapshot|operations|
|factory_country_snapshot|String(50), nullable|snapshot|operations|
|factory_contact_snapshot|String(100), nullable|snapshot|operations|
|factory_phone_snapshot|String(50), nullable|snapshot|operations|
|factory_email_snapshot|String(100), nullable|snapshot|operations|
|created_at|DateTime, no|system audit|none|
|updated_at|DateTime, no|system audit|none|

## 5. ProductBatch schema

```text
product_batch
- id                  Integer PK
- pi_item_id          Integer FK PIItem, NOT NULL, indexed
- batch_number        String(100), NOT NULL
- display_order       Integer, NOT NULL, default 0
- created_at          DateTime, NOT NULL
- updated_at          DateTime, NOT NULL
UNIQUE(pi_item_id, batch_number)
```

`PI.batch_no` is not present in V2. Rendering:

- Proforma Invoice: omit; batch is normally unknown at contract creation.
- Booking: current DOCX does not use batch, so omit.
- Commercial Invoice: show under the corresponding product only when required, joined `A / B / C` for compact layouts.
- Packing List: preferred multiline per PIItem (`Batch No.: A / B / C`); never merge batches from different PIItems.
- Current code audit found `batch_no` only in edit/status/view pages; existing PI/CI/PL/Booking generators do not currently render it.

`PI → PIItem → ProductBatch` uses `ON DELETE CASCADE` plus ORM
`delete-orphan`. Cascading is permitted only inside an intentional order/item
mutation; COMPLETED orders are read-only, so this cannot silently erase
historical batches through ordinary editing.

## 6. Container requirement

Current Booking uses `container_type_quantity` directly and separately also has `container_type` and `quantity_units`. V2 one-container-type baseline uses:

```text
container_type  String(20)  e.g. 20GP
container_count Integer     e.g. 1
```

Presenter derives `1 × 20GP`; the DOCX placeholder `{{container_type_quantity}}` receives this derived display. `quantity_units` is not a container count and is replaced by explicit package fields.

If real orders require mixed `1 × 20GP + 2 × 40HQ`, introduce `OrderContainerRequirement(pi_id, container_type, container_count, display_order)`. Do not create that table until a real case is confirmed.

## 7. BankAccount master and order snapshot

`bank_account` stores only document-used remittance facts: code/name,
beneficiary, bank name/address, account number, SWIFT, optional account
currency, other remittance information, active flag, and timestamps. PI stores
the selected FK plus seven snapshot fields. Later BankAccount edits never
change historical order documents.

## 8. Freight quote vs actual bill

Existing `FreightQuote` is a reusable market quote catalog, but it is insufficient as the agreed order snapshot because it:

- is not linked to PI;
- uses Float;
- has no currency;
- can be edited/deleted independently;
- cannot prove what was accepted for a specific order.

V2 retains a cleaned quote catalog and adds an order agreement snapshot.

```text
freight_quote
- id, freight_forwarder_id
- shipping_company, departure_port, destination_port, route_type
- amount Numeric(18,2), currency String(10)
- quote_date, valid_until, note

order_freight_agreement
- id
- pi_id FK, NOT NULL, UNIQUE for first version
- source_freight_quote_id FK, nullable
- freight_forwarder_id FK, NOT NULL
- agreed_amount Numeric(18,2), NOT NULL
- currency String(10), NOT NULL
- quoted_at Date, nullable
- agreed_at DateTime, nullable
- note Text, nullable
```

Actual settlement remains separate:

```text
freight_settlement
- id, pi_id UNIQUE FK
- usd_bill_required Boolean nullable
- usd_bill_amount Numeric(18,2) nullable
- usd_bill_confirmed Boolean nullable
- cny_bill_required Boolean nullable
- cny_bill_amount Numeric(18,2) nullable
- cny_bill_confirmed Boolean nullable
- invoice_issued Boolean nullable
- invoice_issued_at DateTime nullable
- payment_status String(20) nullable
- paid_at DateTime nullable
```

Comparison rule:

- compare agreement only with the actual component having the same currency;
- same currency + different amount → `FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE` warning/exception with signed difference;
- different currency → not comparable, no arithmetic and no combined total;
- missing amount/currency → validation warning, not a fabricated difference.

The accepted snapshot intentionally excludes route/ports: those already belong
to PI shipment facts, while FreightQuote retains them as quote-search context.
It keeps the forwarder snapshot, amount, currency, quote date, agreed time and
optional note—the minimum required to reproduce the commercial agreement.

## 9. Commission rules

- Calculation base is never duplicated: it is `sum(PIItem.line_total)`.
- Rate: `Numeric(7,4)` percent.
- Derived amount: `base × rate / 100`, quantized to `0.01` using `ROUND_HALF_UP`.
- `commission_amount_mode=DERIVED`: amount recalculates when base/rate changes.
- `EXPLICIT_OVERRIDE`: user enters amount; base/rate remain visible for audit but do not overwrite it.
- Override requires `commission_override_reason`; no approval workflow is required.
- Currency: explicit `commission_currency`; never inferred from free text. It may equal PI currency but is not forced.
- `commission_exporter_id` is absent from V2.

## 10. Correction audit model

`order_correction_session` is separate from TaskActivity because correcting
business facts is not a Task status event. It records PI, one module
(`COMMERCIAL`, `PAYMENT`, `DOCUMENTS`, `SHIPPING`, `FREIGHT`, `ARRIVAL`), required
reason, opening actor/time, closing actor/time and close note. A COMPLETED PI
remains read-only except for the selected module during one open session.
Closing the session runs targeted reconcile in the same transaction. Existing
Task completion and Activity rows are retained.

## 11. Lifecycle matrix

| Module | NEW | PRE_SHIPMENT | SHIPPED | ARRIVED | COMPLETED |
|---|---|---|---|---|---|
|COMMERCIAL_CORE / PI_ITEMS|EDITABLE|READ_ONLY|READ_ONLY|READ_ONLY|READ_ONLY|
|PAYMENT_PLAN|EDITABLE|EDITABLE|READ_ONLY|READ_ONLY|READ_ONLY|
|PAYMENT_RECEIPTS|HIDDEN|HIDDEN|EDITABLE|EDITABLE|READ_ONLY|
|DOCUMENT_REQUIREMENTS|EDITABLE|EDITABLE|READ_ONLY|READ_ONLY|READ_ONLY|
|INITIAL_SHIPMENT_PLAN|EDITABLE|EDITABLE|READ_ONLY|READ_ONLY|READ_ONLY|
|SHIPPING_PREPARATION / DRIVER_INFO|HIDDEN|EDITABLE|READ_ONLY|READ_ONLY|READ_ONLY|
|FREIGHT_REQUIREMENTS|HIDDEN|EDITABLE|READ_ONLY|READ_ONLY|READ_ONLY|
|FREIGHT_SETTLEMENT|HIDDEN|HIDDEN|EDITABLE|EDITABLE|READ_ONLY|
|ACTUAL_DEPARTURE|HIDDEN|HIDDEN|EDITABLE|READ_ONLY|READ_ONLY|
|ACTUAL_ARRIVAL|HIDDEN|HIDDEN|EDITABLE|EDITABLE|READ_ONLY|

A correction session overrides only its mapped module in COMPLETED. Templates
and crafted POST validation consume the same `order_lifecycle.py` policy.

## 12. Task schema

Retain `OrderTask` and append-only `TaskActivity`. V2 rules read only V2 facts; `legacy_*` task codes and fallback adapters are excluded from ordinary runtime. Manually re-entered historical open orders use an explicit initialization/import operation when historical completions must be retained.

Normal V2 reconcile never calls V1 adapters and never creates Legacy Done.
Adapters remain import-only code until V1 archive tooling is separately approved.

## 13. V1 fields excluded from V2

`pi_type`, `commission_exporter_id`, `container_date`, `ocean_freight`, `container_type_quantity`, `quantity_units`, `batch_no`, `coa_status`, `insurance_status`, `document_shipping_status`, `tracking_number`, `payment_received`, `freight_invoice_amount`, `freight_invoice_confirmed`, `telex_release`, and all string fields that combine Required with Completed.

They remain in the V1 archive. Import adapters may read them, but V2 runtime does not.

## 14. Baseline and cutover strategy

`migrations_v2` is an independent Alembic lineage with root revision `v2_0001`.
It directly creates the final schema, requires an explicit absolute database
URL and refuses the V1 instance directory. Existing V1 migrations remain
untouched. Cutover follows `docs/v1_archive_cutover.md`; V2 seeds only master
data and open orders are manually re-entered.
