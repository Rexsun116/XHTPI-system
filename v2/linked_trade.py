"""Read-only linked-trade role resolution shared by V2 workflow code."""

from dataclasses import dataclass


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
    owner = pi.linked_customer_order
    if owner is None:
        return FinancialOwnerResolution(
            owner=None,
            error="Linked EXPORT_ORDER has no CUSTOMER_ORDER financial owner.",
            subject=pi,
        )
    return FinancialOwnerResolution(owner=owner, subject=pi)
