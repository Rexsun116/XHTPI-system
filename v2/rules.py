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
