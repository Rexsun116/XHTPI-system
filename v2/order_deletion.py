"""Permanent deletion for V2 orders that should never have existed."""

from sqlalchemy import delete, select, update

from .models import (
    FreightSettlement,
    OrderCorrectionSession,
    OrderFreightAgreement,
    OrderTask,
    PI,
    PIItem,
    ProductBatch,
    TaskActivity,
    TradeGroup,
    db,
)


class OrderDeletionError(ValueError):
    """Base class for an expected delete-order rejection."""


class OrderDeletionNotAllowed(OrderDeletionError):
    pass


class OrderDeletionConfirmationError(OrderDeletionError):
    pass


def delete_new_order(pi, submitted_confirmation):
    """Delete one NEW order and all order-owned rows in one transaction."""
    if pi.status != "NEW":
        raise OrderDeletionNotAllowed("Only NEW orders can be permanently deleted.")
    if pi.trade_group_id is not None:
        raise OrderDeletionNotAllowed(
            "Linked-trade orders cannot be permanently deleted; use a future group-aware unlink workflow."
        )
    if submitted_confirmation != pi.pi_no:
        raise OrderDeletionConfirmationError("Enter the exact PI Number to confirm permanent deletion.")

    pi_no = pi.pi_no
    try:
        _delete_owned_order(pi.id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return pi_no


def _delete_owned_order(pi_id):
    """Delete owned rows only; the caller controls the transaction."""
    task_ids = select(OrderTask.id).where(OrderTask.pi_id == pi_id)
    item_ids = select(PIItem.id).where(PIItem.pi_id == pi_id)
    db.session.execute(delete(TaskActivity).where(TaskActivity.task_id.in_(task_ids)))
    db.session.execute(delete(OrderTask).where(OrderTask.pi_id == pi_id))
    db.session.execute(delete(ProductBatch).where(ProductBatch.pi_item_id.in_(item_ids)))
    db.session.execute(delete(PIItem).where(PIItem.pi_id == pi_id))
    db.session.execute(delete(OrderCorrectionSession).where(OrderCorrectionSession.pi_id == pi_id))
    db.session.execute(delete(FreightSettlement).where(FreightSettlement.pi_id == pi_id))
    db.session.execute(delete(OrderFreightAgreement).where(OrderFreightAgreement.pi_id == pi_id))
    db.session.execute(delete(PI).where(PI.id == pi_id))


def linked_delete_pair(pi):
    """Read authoritative membership without flushing pending ORM state."""
    with db.session.no_autoflush:
        group_id = db.session.scalar(select(PI.trade_group_id).where(PI.id == pi.id))
        if group_id is None or db.session.get(TradeGroup, group_id) is None:
            raise OrderDeletionNotAllowed("A complete linked trade is required.")
        rows = db.session.execute(select(PI.id, PI.trade_role, PI.status).where(PI.trade_group_id == group_id)).all()
        if (len(rows) != 2 or {r.trade_role for r in rows} != {"CUSTOMER_ORDER", "EXPORT_ORDER"}
                or pi.id not in {r.id for r in rows} or any(r.status != "NEW" for r in rows)):
            raise OrderDeletionNotAllowed("Delete Linked Trade requires exactly one CUSTOMER_ORDER and one EXPORT_ORDER, both NEW.")
        return tuple(db.session.get(PI, next(r.id for r in rows if r.trade_role == role))
                     for role in ("CUSTOMER_ORDER", "EXPORT_ORDER"))


def linked_delete_available(pi):
    try:
        linked_delete_pair(pi)
        return True
    except OrderDeletionNotAllowed:
        return False


def delete_linked_trade(pi, customer_confirmation, export_confirmation):
    """Claim, revalidate and delete the complete NEW pair with one commit."""
    try:
        with db.session.no_autoflush:
            pair = linked_delete_pair(pi)
            group_id = db.session.scalar(select(PI.trade_group_id).where(PI.id == pi.id))
            for row in sorted(pair, key=lambda row: row.id):
                role = "CUSTOMER_ORDER" if row.id == pair[0].id else "EXPORT_ORDER"
                claimed = db.session.execute(update(PI).where(
                    PI.id == row.id, PI.trade_group_id == group_id,
                    PI.trade_role == role, PI.status == "NEW",
                ).values(updated_at=PI.updated_at).execution_options(synchronize_session=False))
                if claimed.rowcount != 1:
                    raise OrderDeletionNotAllowed("Linked trade changed; reload before deleting.")
            for row in sorted(pair, key=lambda row: row.id):
                db.session.refresh(row, with_for_update=True)
            refreshed = linked_delete_pair(pi)
            if tuple(r.id for r in refreshed) != tuple(r.id for r in pair) or any(r.trade_group_id != group_id for r in refreshed):
                raise OrderDeletionNotAllowed("Linked trade membership changed; reload before deleting.")
            customer, export = refreshed
            if customer_confirmation != customer.pi_no or export_confirmation != export.pi_no:
                raise OrderDeletionConfirmationError("Enter both exact current PI Numbers to confirm permanent deletion.")
            names = (customer.pi_no, export.pi_no)
            for row in sorted(pair, key=lambda row: row.id):
                _delete_owned_order(row.id)
            if db.session.scalar(select(PI.id).where(PI.trade_group_id == group_id).limit(1)) is not None:
                raise OrderDeletionNotAllowed("TradeGroup is not empty; deletion cancelled.")
            db.session.execute(delete(TradeGroup).where(TradeGroup.id == group_id))
        db.session.commit()
        return names
    except Exception:
        db.session.rollback()
        raise
