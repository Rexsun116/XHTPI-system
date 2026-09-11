"""Atomic commands for the two orders representing one physical shipment."""

from datetime import date

from sqlalchemy import inspect, select, update

from .models import PI, TradeGroup, db
from .services import reconcile_order_tasks_for_pi
from .shipment_ownership import SHARED_PHYSICAL_FIELDS, copy_physical_facts


class LinkedShipmentError(ValueError):
    """Invalid or stale linked shipment; the command leaves no partial writes."""


def has_trade_link(pi):
    # Incomplete metadata must not fall through to an independent transition.
    return pi.trade_group_id is not None or pi.trade_role is not None


def shipment_pair(pi):
    """Resolve fresh membership, never prefixes or a first-match relationship."""
    with db.session.no_autoflush:
        if pi.trade_group_id is None or db.session.get(TradeGroup, pi.trade_group_id) is None:
            raise LinkedShipmentError("Linked shipment has no valid TradeGroup.")
        members = list(db.session.scalars(select(PI).where(PI.trade_group_id == pi.trade_group_id)))
        customers = [row for row in members if row.trade_role == "CUSTOMER_ORDER"]
        exports = [row for row in members if row.trade_role == "EXPORT_ORDER"]
        if len(members) != 2 or len(customers) != 1 or len(exports) != 1 or pi.id not in {row.id for row in members}:
            raise LinkedShipmentError("Linked shipment requires exactly one CUSTOMER_ORDER and one EXPORT_ORDER.")
        customer, export = customers[0], exports[0]
        if any(row.status != "PRE_SHIPMENT" or row.actual_departure_date is not None for row in members):
            raise LinkedShipmentError("Both linked orders must be in PRE_SHIPMENT with no Actual Departure recorded.")
        return customer, export


def _claim_pair(customer, export):
    """Acquire write locks in ID order, checking persisted states before flushing.

    Conditional updates also protect against a stale ORM instance and a repeat
    request waiting behind a departure transaction. No intermediate commit.
    SQLite serializes writers; other SQL databases lock the matching rows.
    """
    with db.session.no_autoflush:
        for row in sorted((customer, export), key=lambda pi: pi.id):
            result = db.session.execute(update(PI).where(
                PI.id == row.id, PI.trade_group_id == row.trade_group_id,
                PI.trade_role == row.trade_role, PI.status == "PRE_SHIPMENT",
                PI.actual_departure_date.is_(None),
            ).values(status=PI.status, updated_at=PI.updated_at).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise LinkedShipmentError("Linked shipment changed; reload both orders before submitting again.")


def _save_pair(customer, export):
    # Do not use save_order_with_reconcile: it commits and derives commercial
    # amounts. This command changes shipment facts only, with one commit.
    db.session.flush()
    reconcile_order_tasks_for_pi(export)
    reconcile_order_tasks_for_pi(customer)
    db.session.commit()


def record_linked_actual_departure(pi, actual_departure, *, carrier=None, bill=None):
    try:
        if type(actual_departure) is not date:
            raise LinkedShipmentError("Actual Departure Date is required.")
        customer, export = shipment_pair(pi)
        _claim_pair(customer, export)
        customer.actual_departure_date = export.actual_departure_date = actual_departure
        customer.status, export.status = "SHIPPED", "COMPLETED"
        # Optional document details stay local; only the approved physical
        # dates are shared. Empty inputs do not clear existing details.
        if carrier:
            pi.shipping_company = carrier
        if bill:
            pi.bill_of_lading_number = bill
        _save_pair(customer, export)
    except Exception:
        db.session.rollback()
        raise


def save_linked_etd(pi, etd=..., *, eta=...):
    """Save a customer PRE facts patch; omitted ETD leaves persisted ETDs alone."""
    try:
        if etd is not ... and etd is not None and type(etd) is not date:
            raise LinkedShipmentError("ETD must be a calendar date.")
        with db.session.no_autoflush:
            customer, export = shipment_pair(pi)
            _claim_pair(customer, export)
            # Refresh only shipment validation fields; preserve other pending
            # facts in this request. Lock claims precede all shared-date writes.
            for row in (customer, export):
                db.session.refresh(row, attribute_names=[
                    "status", "trade_group_id", "trade_role", "eta", "etd", "actual_departure_date",
                ], with_for_update=True)
            refreshed = shipment_pair(pi)
            if tuple(row.id for row in refreshed) != (customer.id, export.id):
                raise LinkedShipmentError("Linked shipment membership changed; reload before submitting again.")
            for row in (customer, export):
                effective_eta = eta if row is pi and eta is not ... else row.eta
                effective_etd = row.etd if etd is ... else etd
                if effective_etd and effective_eta and effective_eta < effective_etd:
                    raise LinkedShipmentError("ETD cannot be later than either linked order's ETA.")
            if etd is not ...:
                customer.etd = export.etd = etd
            if eta is not ...:
                pi.eta = eta  # Submitted ETA remains local to the submitting order.
            # Do not copy stale, untouched source fields over a newer peer copy.
            untouched = [field for field in SHARED_PHYSICAL_FIELDS
                         if not inspect(customer).attrs[field].history.has_changes()]
            if untouched:
                db.session.refresh(customer, attribute_names=untouched, with_for_update=True)
            copy_physical_facts(customer, export)
        _save_pair(customer, export)
    except Exception:
        db.session.rollback()
        raise


def enter_linked_pre_shipment(customer):
    """A pair created in NEW follows its customer's single preparation gate."""
    from .models import OrderTask
    try:
        with db.session.no_autoflush:
            members = list(db.session.scalars(select(PI).where(PI.trade_group_id == customer.trade_group_id)))
            exports = [row for row in members if row.trade_role == "EXPORT_ORDER"]
            if customer.trade_role != "CUSTOMER_ORDER" or len(members) != 2 or len(exports) != 1:
                raise LinkedShipmentError("A valid customer/export pair is required.")
            export = exports[0]
            for row in sorted(members, key=lambda member: member.id):
                claimed = db.session.execute(update(PI).where(
                    PI.id == row.id, PI.trade_group_id == customer.trade_group_id,
                    PI.trade_role == row.trade_role, PI.status == "NEW",
                ).values(status=PI.status, updated_at=PI.updated_at).execution_options(synchronize_session=False))
                if claimed.rowcount != 1:
                    raise LinkedShipmentError("Both linked orders must still be NEW.")
            gate = db.session.scalar(select(OrderTask.id).where(
                OrderTask.pi_id == customer.id, OrderTask.task_code == "STAGE_GATE_PRE_SHIPMENT",
                OrderTask.status == "ACTION",
            ))
            if gate is None:
                raise LinkedShipmentError("Customer preparation gate is not available.")
            customer.status = export.status = "PRE_SHIPMENT"
            copy_physical_facts(customer, export)
        _save_pair(customer, export)
    except Exception:
        db.session.rollback()
        raise
