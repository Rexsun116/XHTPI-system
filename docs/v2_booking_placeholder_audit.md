# V2 Booking DOCX Placeholder Audit

Source template: `v2/templates/word/BN-Sample.docx`. The V1 template at `templates/word/BN-Sample.docx` remains unchanged. The first Booking does not require a booking number or post-loading facts. V2 renders snapshots so later master-data changes do not rewrite a historical order.

| Placeholder | Business meaning | V2 source | Source type | Lifecycle | Required for initial Booking? | Status |
|---|---|---|---|---|---|---|
| `pi_no` | PI / contract number | `PI.pi_no` | AUTO | NEW | Yes | Mapped; contract number defaults to PI No. |
| `quantity` | Total cargo quantity | `sum(PIItem.quantity)` | DERIVED | NEW | Yes | Compact Decimal; template owns its existing `MT` suffix. |
| `vessel_info` | Vessel / voyage | `PI.vessel_info` | USER_INPUT | PRE_SHIPMENT | Yes | Mapped. |
| `booking_number` | Carrier-confirmed booking number | `PI.booking_number` | USER_INPUT | PRE_SHIPMENT | No | Mapped; may be blank for the initial request. |
| exporter name/address/tax | Shipper details | PI exporter snapshots | AUTO | NEW | Yes | Mapped. |
| customer name/address/tax | Consignee details | PI customer snapshots | AUTO | NEW | Yes | Mapped. |
| notify party fields | Notify party | Customer snapshots when `same_as_consignee`; otherwise PI notify snapshots | AUTO / USER_INPUT | PRE_SHIPMENT | Yes | Mapped. |
| `loading_port` / `destination_port` | Route ports | PI ports | AUTO | NEW | Yes | Mapped. |
| `container_type_quantity` | Container requirement | `format(container_count, container_type)` | DERIVED | PRE_SHIPMENT | Yes | Mapped; e.g. `1 × 20GP`. |
| `shipping_mark` | Shipping mark | `PI.shipping_mark` | USER_INPUT | PRE_SHIPMENT | Yes | Mapped. |
| product category/brand/model | Cargo description | all PIItem product snapshots | AUTO | NEW | Yes | Mapped. |
| `product_hs_codes` | Product ↔ HS code association | each `PIItem.product_hs_code_snapshot` | AUTO / USER_INPUT | NEW/PRE_SHIPMENT | No | Mapped; blank remains blank and never falls back to `320611`. |
| `freight_term` | Freight term | `PI.freight_term` | USER_INPUT | PRE_SHIPMENT | Yes | Mapped. |
| `freight_clause` | Freight clause | `PI.freight_clause` | USER_INPUT | PRE_SHIPMENT | No | Mapped. |
| `waybill_option` | B/L / waybill instruction | `PI.waybill_option` | USER_INPUT | PRE_SHIPMENT | Yes | Mapped. |
| `quantity_units` / `total_quantity` | Package count and unit | `PI.package_count` + `PI.package_unit` | USER_INPUT / DERIVED | PRE_SHIPMENT | No | Compact joined display, e.g. `800BAGS`. |
| `gross_weight` / `total_weight` | Gross weight | canonical `PI.gross_weight_kg` + display unit | USER_INPUT / DERIVED | PRE_SHIPMENT | No | `KGS` displays canonical KG; `MT` divides by 1000; compact joined display. |
| `volume` / `total_volume` | Volume | `PI.volume_cbm` | USER_INPUT / DERIVED | PRE_SHIPMENT | No | Canonical CBM; compact `25.5CBM`. |
| `vgm` / `total_vgm` | Verified Gross Mass | canonical `PI.vgm_kg` + display unit | POST_LOADING | SHIPPED | No | KGS/MT display; blank is valid for first Booking. |
| `container_number` / `seal_number` | Container-specific identifiers | PI fields | POST_LOADING | SHIPPED | No | Blank is valid for first Booking. |

Multiline party rule: Shipper, Consignee, and Notify Party snapshot values convert both `;` and `；` into real Word line breaks. The replacement helper supports placeholders split across Word runs.
