"""Pure V2 domain formatting/validation helpers used by specifications and tests."""

from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")


def format_container_requirement(container_type, container_count):
    if not container_type or container_count is None:
        return None
    count = int(container_count)
    if count <= 0 or Decimal(str(container_count)) != Decimal(count):
        raise ValueError("container_count must be a positive whole number.")
    return f"{count} × {str(container_type).strip().upper()}"


def format_batch_numbers(batch_numbers, *, separator=" / "):
    values = [str(value).strip() for value in batch_numbers if str(value).strip()]
    return separator.join(values)


def compare_freight_quote_to_bill(*, quote_amount, quote_currency, bill_amount, bill_currency):
    """Compare only like currencies; never invent a cross-currency total."""
    if quote_amount is None or bill_amount is None:
        return {"comparable": False, "reason": "AMOUNT_MISSING", "difference": None}
    quote_currency = (quote_currency or "").strip().upper()
    bill_currency = (bill_currency or "").strip().upper()
    if not quote_currency or not bill_currency:
        return {"comparable": False, "reason": "CURRENCY_MISSING", "difference": None}
    if quote_currency != bill_currency:
        return {"comparable": False, "reason": "CURRENCY_MISMATCH", "difference": None}
    quote = Decimal(str(quote_amount)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    bill = Decimal(str(bill_amount)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    difference = (bill - quote).quantize(MONEY_QUANTUM)
    return {
        "comparable": True,
        "reason": "MATCH" if difference == 0 else "AMOUNT_DIFFERENCE",
        "difference": difference,
    }


def calculate_commission_amount(calculation_base, rate_percent):
    if calculation_base is None or rate_percent is None:
        return None
    base = Decimal(str(calculation_base))
    rate = Decimal(str(rate_percent))
    if base < 0 or rate < 0:
        raise ValueError("Commission base and rate cannot be negative.")
    return (base * rate / Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
