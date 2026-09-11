"""V2 physical shipment ownership; commercial/document identities stay local."""

from .linked_trade import is_export_order
from .models import PI, db


# Persisted for existing booking/packing rendering and local loading context.
# ETD uses its own locked command. Planned date remains a contract snapshot.
SHARED_PHYSICAL_FIELDS = (
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
    "SHIPPING_DRIVER_INFO", "SHIPPING_ACTUAL_DEPARTURE", "SHIPPING_ACTUAL_ARRIVAL", "ARRIVAL_CUSTOMER_PICKUP",
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
