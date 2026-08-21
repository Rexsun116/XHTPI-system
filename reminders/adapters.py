"""Compatibility adapters from legacy PI strings to normalized required facts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredFact:
    required: bool | None
    legacy_done: bool = False
    source: str = "UNKNOWN"
    raw_value: object = None


LEGACY_REQUIRED_MAPPING = {
    "需要": RequiredFact(True, False, "LEGACY"),
    "不需要": RequiredFact(False, False, "LEGACY"),
    "已完成": RequiredFact(True, True, "LEGACY"),
}

LEGACY_MAIL_MAPPING = {
    "未邮寄": RequiredFact(True, False, "LEGACY"),
    "已邮寄": RequiredFact(True, True, "LEGACY"),
    "不需要": RequiredFact(False, False, "LEGACY"),
}

LEGACY_SETTLEMENT_MAPPING = {
    "需要": RequiredFact(True, False, "LEGACY"),
    "不需要": RequiredFact(False, False, "LEGACY"),
    # One historic value cannot safely be split into advance and balance tasks.
    "已完成": RequiredFact(True, True, "LEGACY_AMBIGUOUS_DONE"),
}

LEGACY_TELEX_DONE_VALUES = {"已电放"}


def _legacy_value(value, mapping):
    normalized = value.strip() if isinstance(value, str) else value
    if normalized in (None, ""):
        return RequiredFact(None, False, "UNKNOWN", value)
    result = mapping.get(normalized)
    if result is None:
        return RequiredFact(None, False, "UNKNOWN", value)
    return RequiredFact(result.required, result.legacy_done, result.source, value)


def legacy_required_fact(value):
    """Map only values confirmed in current forms/data; unknown text stays unknown."""
    return _legacy_value(value, LEGACY_REQUIRED_MAPPING)


def legacy_mail_fact(value):
    """Map the existing original-document shipping status without changing it."""
    return _legacy_value(value, LEGACY_MAIL_MAPPING)


def legacy_settlement_fact(value):
    return _legacy_value(value, LEGACY_SETTLEMENT_MAPPING)


def legacy_telex_done(value):
    normalized = value.strip() if isinstance(value, str) else value
    return normalized in LEGACY_TELEX_DONE_VALUES


def boolean_required_fact(value):
    if value is None:
        return RequiredFact(None, False, "UNKNOWN", value)
    return RequiredFact(bool(value), False, "BOOLEAN", value)


def resolve_required_fact(pi, *, new_field=None, legacy_field=None, legacy_adapter=legacy_required_fact):
    if new_field is not None:
        current = getattr(pi, new_field)
        if current is not None:
            return boolean_required_fact(current)
    if legacy_field is not None:
        return legacy_adapter(getattr(pi, legacy_field))
    return RequiredFact(None, False, "UNKNOWN", None)


def document_facts(pi):
    """Return all Phase 2A document facts using explicit, safe fallback rules."""
    return {
        "COO": resolve_required_fact(pi, legacy_field="coo_required"),
        "APTA": resolve_required_fact(pi, legacy_field="apta_required"),
        "EXPORT_LICENSE": resolve_required_fact(pi, legacy_field="export_license_required"),
        "CUSTOMS": resolve_required_fact(pi, legacy_field="customs_docs_required"),
        "COC": resolve_required_fact(pi, new_field="coc_required"),
        "ORIGINAL_BL": resolve_required_fact(pi, new_field="original_bl_required"),
        "OBD_BL": resolve_required_fact(pi, new_field="obd_electronic_required"),
        "COA": resolve_required_fact(pi, new_field="coa_required", legacy_field="coa_status"),
        # Legacy insurance_status cannot identify original versus electronic policy.
        # Neither new fact falls back, preventing duplicate or misclassified tasks.
        "INSURANCE_ORIGINAL": resolve_required_fact(pi, new_field="insurance_original_required"),
        "INSURANCE_ELECTRONIC": resolve_required_fact(pi, new_field="insurance_electronic_required"),
        "ORIGINAL_DOCUMENTS_MAIL": resolve_required_fact(
            pi,
            new_field="original_documents_mail_required",
            legacy_field="document_shipping_status",
            legacy_adapter=legacy_mail_fact,
        ),
        "TELEX_RELEASE": resolve_required_fact(pi, new_field="telex_release_required"),
    }
