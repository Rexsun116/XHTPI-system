"""Read-only linked-trade role resolution shared by V2 workflow code."""

from dataclasses import dataclass

from sqlalchemy import and_, case, func
from sqlalchemy.orm import aliased

from .models import PI, db


@dataclass(frozen=True)
class FinancialOwnerResolution:
    owner: object | None
    error: str | None = None

    @property
    def valid(self):
        return self.owner is not None and self.error is None

    @property
    def managed_by_peer(self):
        return self.owner is not None and self.owner is not self.subject

    # Assigned by the resolver to avoid callers deriving ownership from names,
    # PI prefixes, or party identity.
    subject: object | None = None


def is_export_order(pi):
    return bool(pi.trade_group_id is not None and pi.trade_role == "EXPORT_ORDER")


def financial_owner_for(pi):
    """Return the only permitted financial owner without mutating either PI."""
    if not is_export_order(pi):
        return FinancialOwnerResolution(owner=pi, subject=pi)
    members = list(pi.trade_group.members) if pi.trade_group is not None else []
    customers = [member for member in members if member.trade_role == "CUSTOMER_ORDER"]
    exports = [member for member in members if member.trade_role == "EXPORT_ORDER"]
    if len(members) != 2 or len(customers) != 1 or len(exports) != 1 or exports[0].id != pi.id:
        return FinancialOwnerResolution(
            owner=None,
            error="Linked EXPORT_ORDER has no CUSTOMER_ORDER financial owner in a valid structured pair.",
            subject=pi,
        )
    return FinancialOwnerResolution(owner=customers[0], subject=pi)


def financial_owner_id_for_query(subject):
    """SQL counterpart of :func:`financial_owner_for` for read-only queries.

    The scalar expression resolves a financial owner only for a structurally
    valid CUSTOMER_ORDER / EXPORT_ORDER pair.  Callers can join the returned
    id without restating the linked-trade ownership contract or multiplying
    rows when legacy link metadata is malformed.
    """
    member = aliased(PI)
    customer = aliased(PI)
    group_members = db.select(func.count(member.id)).where(
        member.trade_group_id == subject.trade_group_id
    ).scalar_subquery()
    customer_members = db.select(func.count(member.id)).where(
        member.trade_group_id == subject.trade_group_id,
        member.trade_role == "CUSTOMER_ORDER",
    ).scalar_subquery()
    export_members = db.select(func.count(member.id)).where(
        member.trade_group_id == subject.trade_group_id,
        member.trade_role == "EXPORT_ORDER",
    ).scalar_subquery()
    customer_id = db.select(customer.id).where(
        customer.trade_group_id == subject.trade_group_id,
        customer.trade_role == "CUSTOMER_ORDER",
    ).limit(1).scalar_subquery()
    valid_export = and_(
        subject.trade_role == "EXPORT_ORDER",
        subject.trade_group_id.is_not(None),
        group_members == 2,
        customer_members == 1,
        export_members == 1,
    )
    is_linked_export = and_(
        subject.trade_role == "EXPORT_ORDER",
        subject.trade_group_id.is_not(None),
    )
    return case(
        (valid_export, customer_id),
        (is_linked_export, None),
        else_=subject.id,
    )
