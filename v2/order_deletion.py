"""Permanent deletion for V2 orders that should never have existed."""

from sqlalchemy import delete, select

from .models import (
    FreightSettlement,
    OrderCorrectionSession,
    OrderFreightAgreement,
    OrderTask,
    PI,
    PIItem,
    ProductBatch,
    TaskActivity,
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

    pi_id = pi.id
    pi_no = pi.pi_no
    task_ids = select(OrderTask.id).where(OrderTask.pi_id == pi_id)
    item_ids = select(PIItem.id).where(PIItem.pi_id == pi_id)

    try:
        # Explicit ordering makes ownership visible and does not rely on ORM
        # relationships or SQLite cascade behavior.
        db.session.execute(delete(TaskActivity).where(TaskActivity.task_id.in_(task_ids)))
        db.session.execute(delete(OrderTask).where(OrderTask.pi_id == pi_id))
        db.session.execute(delete(ProductBatch).where(ProductBatch.pi_item_id.in_(item_ids)))
        db.session.execute(delete(PIItem).where(PIItem.pi_id == pi_id))
        db.session.execute(delete(OrderCorrectionSession).where(OrderCorrectionSession.pi_id == pi_id))
        db.session.execute(delete(FreightSettlement).where(FreightSettlement.pi_id == pi_id))
        db.session.execute(delete(OrderFreightAgreement).where(OrderFreightAgreement.pi_id == pi_id))
        db.session.execute(delete(PI).where(PI.id == pi_id))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return pi_no
