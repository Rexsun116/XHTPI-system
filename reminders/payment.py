"""Structured Phase 2C payment calculations.

All calculations use Decimal. PIItem.total_price remains a legacy Float, so it is
converted through ``str`` before Decimal arithmetic.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
PERCENT_QUANTUM = Decimal("0.01")
ZERO = Decimal("0.00")


def decimal_value(value, *, quantum=MONEY_QUANTUM):
    if value is None:
        return None
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)


def contract_total(pi):
    values = (item.total_price for item in (pi.products or []))
    return sum((decimal_value(value) or ZERO for value in values), ZERO).quantize(MONEY_QUANTUM)


def suggested_advance_amount(total, percent):
    total = decimal_value(total)
    percent = decimal_value(percent, quantum=PERCENT_QUANTUM)
    if total is None or percent is None:
        return None
    return (total * percent / Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def suggested_balance_amount(total, advance_amount):
    total = decimal_value(total)
    advance_amount = decimal_value(advance_amount)
    if total is None or advance_amount is None:
        return None
    return max(total - advance_amount, ZERO).quantize(MONEY_QUANTUM)


def _has_structured_facts(pi):
    fields = (
        "currency",
        "advance_payment_percent",
        "advance_payment_amount",
        "advance_received_amount",
        "advance_received_at",
        "balance_payment_amount",
        "balance_received_amount",
        "balance_received_at",
    )
    return any(getattr(pi, field, None) is not None for field in fields)


@dataclass(frozen=True)
class PaymentAssessment:
    currency: str | None
    contract_total: Decimal
    advance_percent: Decimal | None
    advance_expected: Decimal | None
    advance_received: Decimal | None
    advance_outstanding: Decimal | None
    balance_expected: Decimal | None
    balance_received: Decimal | None
    balance_outstanding: Decimal | None
    total_received: Decimal | None
    outstanding: Decimal | None
    fully_received: bool
    full_payment_source: str
    structured_plan_complete: bool
    warnings: tuple[str, ...]

    def context(self):
        def text(value):
            return format(value, ".2f") if value is not None else None

        return {
            "currency": self.currency,
            "contract_total": text(self.contract_total),
            "advance_percent": text(self.advance_percent),
            "advance_expected": text(self.advance_expected),
            "advance_received": text(self.advance_received),
            "advance_outstanding": text(self.advance_outstanding),
            "balance_expected": text(self.balance_expected),
            "balance_received": text(self.balance_received),
            "balance_outstanding": text(self.balance_outstanding),
            "total_received": text(self.total_received),
            "outstanding": text(self.outstanding),
            "fully_received": self.fully_received,
            "full_payment_source": self.full_payment_source,
            "structured_plan_complete": self.structured_plan_complete,
            "warnings": list(self.warnings),
        }


def assess_payment(pi):
    total = contract_total(pi)
    percent = decimal_value(getattr(pi, "advance_payment_percent", None), quantum=PERCENT_QUANTUM)
    advance_expected = decimal_value(getattr(pi, "advance_payment_amount", None))
    if advance_expected is None and percent is not None:
        advance_expected = suggested_advance_amount(total, percent)

    balance_expected = decimal_value(getattr(pi, "balance_payment_amount", None))
    if balance_expected is None and advance_expected is not None:
        balance_expected = suggested_balance_amount(total, advance_expected)

    advance_received = decimal_value(getattr(pi, "advance_received_amount", None))
    balance_received = decimal_value(getattr(pi, "balance_received_amount", None))
    plan_complete = advance_expected is not None and balance_expected is not None
    structured = _has_structured_facts(pi)
    received_total = None
    outstanding = None
    advance_outstanding = None
    balance_outstanding = None
    if plan_complete:
        advance_received_for_calc = advance_received or ZERO
        balance_received_for_calc = balance_received or ZERO
        received_total = (advance_received_for_calc + balance_received_for_calc).quantize(MONEY_QUANTUM)
        advance_outstanding = (advance_expected - advance_received_for_calc).quantize(MONEY_QUANTUM)
        balance_outstanding = (balance_expected - balance_received_for_calc).quantize(MONEY_QUANTUM)
        outstanding = (advance_expected + balance_expected - received_total).quantize(MONEY_QUANTUM)

    structured_full = bool(plan_complete and outstanding is not None and outstanding <= ZERO)
    legacy_full = (getattr(pi, "payment_received", None) or "").strip() == "已收齐"
    warnings = []
    if structured and not getattr(pi, "currency", None):
        warnings.append("CURRENCY_MISSING")
    if advance_expected is not None and advance_received is not None and advance_received > advance_expected:
        warnings.append("ADVANCE_RECEIVED_EXCEEDS_EXPECTED")
    if balance_expected is not None and balance_received is not None and balance_received > balance_expected:
        warnings.append("BALANCE_RECEIVED_EXCEEDS_EXPECTED")
    if plan_complete and advance_expected + balance_expected != total:
        warnings.append("EXPECTED_TOTAL_DIFFERS_FROM_CONTRACT_TOTAL")
    if received_total is not None and received_total > total:
        warnings.append("TOTAL_RECEIVED_EXCEEDS_CONTRACT_TOTAL")
    if structured and plan_complete and legacy_full != structured_full:
        warnings.append("LEGACY_STRUCTURED_PAYMENT_CONFLICT")
    if advance_received and advance_received > ZERO and getattr(pi, "advance_received_at", None) is None:
        warnings.append("ADVANCE_RECEIVED_DATE_MISSING")
    if balance_received and balance_received > ZERO and getattr(pi, "balance_received_at", None) is None:
        warnings.append("BALANCE_RECEIVED_DATE_MISSING")

    if structured and plan_complete:
        fully_received = structured_full
        source = "STRUCTURED"
    elif not structured and legacy_full:
        fully_received = True
        source = "LEGACY_PAID_IN_FULL"
    else:
        fully_received = False
        source = "STRUCTURED_INCOMPLETE" if structured else "UNKNOWN"

    return PaymentAssessment(
        currency=(getattr(pi, "currency", None) or "").strip().upper() or None,
        contract_total=total,
        advance_percent=percent,
        advance_expected=advance_expected,
        advance_received=advance_received,
        advance_outstanding=advance_outstanding,
        balance_expected=balance_expected,
        balance_received=balance_received,
        balance_outstanding=balance_outstanding,
        total_received=received_total,
        outstanding=outstanding,
        fully_received=fully_received,
        full_payment_source=source,
        structured_plan_complete=plan_complete,
        warnings=tuple(warnings),
    )


def is_payment_fully_received(pi):
    """Single trusted full-payment predicate used by all Phase 2C rules."""
    return assess_payment(pi).fully_received


def stage_received(pi, stage):
    if stage == "advance":
        amount = decimal_value(getattr(pi, "advance_received_amount", None))
        received_at = getattr(pi, "advance_received_at", None)
    elif stage == "balance":
        amount = decimal_value(getattr(pi, "balance_received_amount", None))
        received_at = getattr(pi, "balance_received_at", None)
    else:
        raise ValueError(f"Unknown payment stage: {stage}")
    return bool(amount is not None and amount > ZERO and received_at is not None)
