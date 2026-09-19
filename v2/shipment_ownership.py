"""V2 physical shipment ownership; commercial/document identities stay local."""

from .linked_trade import is_export_order
from .iso3166 import ISO_3166_1_ALPHA2, country_code_for_name
from .models import PI, db


# Persisted for existing booking/packing rendering and local loading context.
# ETD uses its own locked command. Planned date remains a contract snapshot.
# Route geography is owned by the CUSTOMER_ORDER and copied to its linked
# EXPORT_ORDER for compatible per-PI document rendering.
ROUTE_GEOGRAPHY_FIELDS = (
    "loading_port", "loading_country_code",
    "destination_port", "destination_country_code",
)

SHARED_PHYSICAL_FIELDS = (
    *ROUTE_GEOGRAPHY_FIELDS,
    "container_loading_date", "container_loading_period", "container_location",
    "container_type", "container_count", "freight_forwarder_id", "booking_number", "vessel_info",
    "package_count", "package_unit", "gross_weight_kg", "gross_weight_display_unit", "volume_cbm",
)

PHYSICAL_FORM_FIELDS = frozenset(SHARED_PHYSICAL_FIELDS) | {
    "planned_shipment_date", "loading_port", "destination_port", "container_loading_at",
    "driver_name", "driver_phone", "vehicle_number", "gross_weight", "volume",
    "etd", "eta", "actual_departure_date", "actual_arrival_date",
    "shipping_company", "bill_of_lading_number", "container_number", "seal_number", "vgm", "vgm_display_unit",
}

PHYSICAL_TASK_CODES = frozenset({
    "SHIPPING_PLANNED_DATE_OVERDUE", "SHIPPING_CONTAINER_LOADING", "SHIPPING_FREIGHT_AGREEMENT",
    "SHIPPING_ETA_MISSING", "SHIPPING_DRIVER_INFO", "SHIPPING_ACTUAL_DEPARTURE", "SHIPPING_ACTUAL_ARRIVAL", "ARRIVAL_CUSTOMER_PICKUP",
})


def is_physical_shipment_task(code):
    return code in PHYSICAL_TASK_CODES or code.startswith("STAGE_GATE_")


def shipment_owner_for(pi):
    """Read-only resolution, including completed pairs; fail closed on malformed links."""
    if not is_export_order(pi):
        return pi
    members = list(db.session.scalars(db.select(PI).where(PI.trade_group_id == pi.trade_group_id)))
    customers = [row for row in members if row.trade_role == "CUSTOMER_ORDER"]
    exports = [row for row in members if row.trade_role == "EXPORT_ORDER"]
    return customers[0] if len(members) == 2 and len(customers) == len(exports) == 1 else None


def copy_physical_facts(customer, export):
    """Caller owns claims/validation/transaction; never copy commercial fields."""
    for field in SHARED_PHYSICAL_FIELDS:
        setattr(export, field, getattr(customer, field))


class RouteOwnershipError(ValueError):
    """A linked pair cannot safely accept a route-owner write."""


class RouteGeographyValidationError(ValueError):
    """An explicit shipment-country value is not a valid ISO route fact."""


def normalize_country_code(value):
    """Return an uppercase ISO 3166-1 alpha-2 code, or ``None`` for blank."""
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if not normalized:
        return None
    if normalized not in ISO_3166_1_ALPHA2:
        raise RouteGeographyValidationError("Country code must be a valid ISO 3166-1 alpha-2 code.")
    return normalized


def validate_route_geography(*, loading_port=None, loading_country_code=None,
                             destination_port=None, destination_country_code=None):
    """Normalize explicit codes and validate a country-aware route write.

    Existing persisted rows are deliberately not validated on read.  Call this
    only when a future country-aware write contract supplies route data.
    """
    loading_country_code = normalize_country_code(loading_country_code)
    destination_country_code = normalize_country_code(destination_country_code)
    if (loading_port or "").strip() and not loading_country_code:
        raise RouteGeographyValidationError("Loading country is required when Loading Port is set.")
    if (destination_port or "").strip() and not destination_country_code:
        raise RouteGeographyValidationError("Destination country is required when Destination Port is set.")
    return loading_country_code, destination_country_code


def resolve_country_submission(*, name, code, label):
    """Validate one C1 visible-name/hidden-code country submission.

    A code is accepted only when its accompanying visible English country name
    resolves exactly to the same alpha-2 code.  This prevents a stale hidden
    value from silently surviving a user edit to the visible combobox text.
    """
    visible_name = " ".join(str(name or "").split())
    normalized_code = normalize_country_code(code)
    if not visible_name:
        if normalized_code:
            raise RouteGeographyValidationError(f"Please select a valid {label.lower()}.")
        return None
    resolved_code = country_code_for_name(visible_name)
    if resolved_code is None:
        raise RouteGeographyValidationError(f"Please select a valid {label.lower()}.")
    if normalized_code and normalized_code != resolved_code:
        raise RouteGeographyValidationError(f"{label} does not match the selected country name.")
    return resolved_code


def linked_export_for_route_owner(customer):
    """Return a valid linked export for a CUSTOMER_ORDER, if one exists.

    This resolver deliberately fails closed for partial or malformed link
    metadata.  It does not claim rows or commit; callers retain their own
    transaction and concurrency contracts.
    """
    if customer.trade_group_id is None and customer.trade_role is None:
        return None
    if customer.trade_group_id is None or customer.trade_role != "CUSTOMER_ORDER":
        raise RouteOwnershipError("Linked route geography requires a valid CUSTOMER_ORDER owner.")
    members = list(db.session.scalars(db.select(PI).where(PI.trade_group_id == customer.trade_group_id)))
    customers = [row for row in members if row.trade_role == "CUSTOMER_ORDER"]
    exports = [row for row in members if row.trade_role == "EXPORT_ORDER"]
    if (len(members) != 2 or len(customers) != 1 or customers[0].id != customer.id
            or len(exports) != 1):
        raise RouteOwnershipError("Linked route geography requires exactly one CUSTOMER_ORDER and one EXPORT_ORDER.")
    return exports[0]


def synchronize_route_geography(customer):
    """Project the authoritative CUSTOMER_ORDER route onto its export peer."""
    with db.session.no_autoflush:
        export = linked_export_for_route_owner(customer)
        if export is not None:
            for field in ROUTE_GEOGRAPHY_FIELDS:
                setattr(export, field, getattr(customer, field))
    return export
