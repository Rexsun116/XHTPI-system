"""Clean V2 rule registry. Facts only; no V1 adapters."""
DOCUMENT_RULES = (
    ("DOCUMENT_COO", "coo_required", "办理 COO", "LOADING"),
    ("DOCUMENT_APTA", "apta_required", "办理 APTA", "LOADING"),
    ("DOCUMENT_EXPORT_LICENSE", "export_license_required", "办理出口许可证", "LOADING_MINUS_5"),
    ("DOCUMENT_CUSTOMS", "customs_docs_required", "准备报关文件", "LOADING_MINUS_5"),
    ("DOCUMENT_COC", "coc_required", "办理 COC", "PRE_SHIPMENT"),
    ("DOCUMENT_ORIGINAL_BL", "original_bl_required", "取得/处理提单原件", "SHIPPED"),
    ("DOCUMENT_OBD_BL", "obd_electronic_required", "取得 OBD 提单电子版", "SHIPPED"),
    ("DOCUMENT_COA", "coa_required", "取得并准备 COA 质检单", "LOADING"),
    ("DOCUMENT_INSURANCE_ORIGINAL", "insurance_original_required", "取得保单原件", "SHIPPED"),
    ("DOCUMENT_INSURANCE_ELECTRONIC", "insurance_electronic_required", "取得保单电子版", "SHIPPED"),
)

# Linked export preparation uses customer Loading Date presence, not a date window.
LINKED_EXPORT_DOCUMENT_GATES = (
    ("DOCUMENT_EXPORT_LICENSE", "export_license_required", "Export License"),
    ("DOCUMENT_CUSTOMS", "customs_docs_required", "Customs Documents"),
)
LINKED_EXPORT_SETTLEMENT = "DOCUMENT_EXPORT_SETTLEMENT"
CUSTOMER_DOCUMENT_TASK_CODES = frozenset({
    "DOCUMENT_ORIGINAL_BL", "DOCUMENT_OBD_BL", "DOCUMENT_INSURANCE_ORIGINAL",
    "DOCUMENT_INSURANCE_ELECTRONIC", "ORIGINAL_DOCUMENTS_MAIL", "DOCUMENT_TELEX_RELEASE",
})

# A completed order is a terminal operational state. These task codes may
# retain append-only history, but unresolved records cannot remain operational
# actions after completion. FREIGHT_FORWARDER_PAYMENT is deliberately absent:
# an unpaid required freight bill can remain a genuine obligation.
COMPLETED_PROCESS_TASK_CODES = frozenset({
    "PAYMENT_ADVANCE_WAITING", "PAYMENT_EMAIL", "PAYMENT_BALANCE_FOLLOWUP",
    "STAGE_GATE_PRE_SHIPMENT", "STAGE_GATE_SHIPPED",
    "SHIPPING_CONTAINER_LOADING", "SHIPPING_FREIGHT_AGREEMENT",
    "SHIPPING_PLANNED_DATE_OVERDUE", "SHIPPING_DRIVER_INFO", "SHIPPING_ETA_MISSING",
    "SHIPPING_ACTUAL_DEPARTURE", "SHIPPING_ACTUAL_ARRIVAL", "ARRIVAL_CUSTOMER_PICKUP",
    *(code for code, _, _, _ in DOCUMENT_RULES),
    "ORIGINAL_DOCUMENTS_MAIL", "DOCUMENT_TELEX_RELEASE", "DOCUMENT_EXPORT_SETTLEMENT",
    "SETTLEMENT_DOCUMENT_ADVANCE", "SETTLEMENT_DOCUMENT_BALANCE",
    "FREIGHT_USD_AMOUNT_CAPTURE", "FREIGHT_USD_AMOUNT_CONFIRM", "FREIGHT_USD_INVOICE_ISSUED",
    "FREIGHT_USD_PAYMENT_CONFIRM", "FREIGHT_CNY_AMOUNT_CAPTURE", "FREIGHT_CNY_AMOUNT_CONFIRM",
    "FREIGHT_CNY_INVOICE_ISSUED", "FREIGHT_CNY_PAYMENT_CONFIRM",
    "FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE", "FREIGHT_INVOICE_ISSUED", "FREIGHT_PAYMENT_CONFIRM",
})
