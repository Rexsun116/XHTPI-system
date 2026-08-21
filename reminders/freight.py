"""Dual-currency freight facts and coordinated manual completion operations."""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app import PI, db
from reminders.enums import ActivityEvent, TaskStatus
from reminders.task_service import InvalidTransitionError, TaskServiceError, mark_done
from task_models import OrderTask


MONEY_QUANTUM = Decimal("0.01")
COMPONENTS = {
    "USD": {
        "required": "freight_usd_bill_required",
        "amount": "freight_usd_amount",
        "confirmed": "freight_usd_confirmed",
        "confirm_task": "FREIGHT_USD_AMOUNT_CONFIRM",
    },
    "CNY": {
        "required": "freight_cny_bill_required",
        "amount": "freight_cny_amount",
        "confirmed": "freight_cny_confirmed",
        "confirm_task": "FREIGHT_CNY_AMOUNT_CONFIRM",
    },
}
FREIGHT_MANUAL_TASK_CODES = {
    "FREIGHT_USD_AMOUNT_CONFIRM",
    "FREIGHT_CNY_AMOUNT_CONFIRM",
    "FREIGHT_INVOICE_ISSUED",
}


def money(value):
    if value is None:
        return None
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def money_text(value):
    value = money(value)
    return format(value, ".2f") if value is not None else None


def freight_forwarder_name(pi):
    try:
        forwarder = pi.freight_forwarder
    except (AttributeError, TypeError, ValueError):
        forwarder = None
    return forwarder.name if forwarder else None


def _parse_money(value, field):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"Invalid decimal value for {field}.") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be a finite non-negative amount.")
    return parsed.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def update_freight_bill_amounts(
    pi,
    *,
    usd_required=None,
    usd_amount=None,
    cny_required=None,
    cny_amount=None,
    update_usd_required=False,
    update_usd_amount=False,
    update_cny_required=False,
    update_cny_amount=False,
):
    """Update freight facts; changing a confirmed amount invalidates that confirmation fact."""
    values = {
        "USD": (usd_required, usd_amount, update_usd_required, update_usd_amount),
        "CNY": (cny_required, cny_amount, update_cny_required, update_cny_amount),
    }
    for currency, (required, amount, change_required, change_amount) in values.items():
        fields = COMPONENTS[currency]
        if change_required:
            if required not in (True, False, None):
                raise ValueError(f"Invalid {fields['required']} value.")
            setattr(pi, fields["required"], required)
        if change_amount:
            parsed = _parse_money(amount, fields["amount"])
            previous = money(getattr(pi, fields["amount"]))
            setattr(pi, fields["amount"], parsed)
            if previous != parsed and getattr(pi, fields["confirmed"]) is True:
                setattr(pi, fields["confirmed"], False)
    return pi


def update_freight_facts_from_form(pi, form):
    tri_state = {"": None, "true": True, "false": False}
    kwargs = {}
    for currency in ("usd", "cny"):
        required_field = f"freight_{currency}_bill_required"
        amount_field = f"freight_{currency}_amount"
        if required_field in form:
            raw = form.get(required_field, "")
            if raw not in tri_state:
                raise ValueError(f"Invalid required fact value for {required_field}.")
            kwargs[f"{currency}_required"] = tri_state[raw]
            kwargs[f"update_{currency}_required"] = True
        if amount_field in form:
            kwargs[f"{currency}_amount"] = form.get(amount_field)
            kwargs[f"update_{currency}_amount"] = True
    update_freight_bill_amounts(pi, **kwargs)

    if "freight_paid_at" in form:
        raw_paid_at = (form.get("freight_paid_at") or "").strip()
        pi.freight_paid_at = (
            datetime.strptime(raw_paid_at, "%Y-%m-%dT%H:%M") if raw_paid_at else None
        )
    return pi


def latest_confirmation_payload(task, currency):
    for activity in reversed(task.activities):
        payload = activity.payload or {}
        if (
            activity.event_type == ActivityEvent.COMPLETED
            and payload.get("currency") == currency
            and payload.get("confirmed_amount") is not None
        ):
            return payload
    return None


def confirmation_snapshot_matches(task, currency, current_amount):
    payload = latest_confirmation_payload(task, currency)
    return bool(payload and payload.get("confirmed_amount") == money_text(current_amount))


def _task(task_or_id):
    task = task_or_id if isinstance(task_or_id, OrderTask) else db.session.get(OrderTask, task_or_id)
    if task is None or db.session.get(PI, task.pi_id) is None:
        raise TaskServiceError("Task or its PI does not exist.")
    return task


def _confirm_component(task_or_id, *, actor_id, currency, note=None, now=None):
    task = _task(task_or_id)
    fields = COMPONENTS[currency]
    if task.task_code != fields["confirm_task"]:
        raise InvalidTransitionError(f"Task is not the {currency} freight confirmation task.")
    pi = task.pi
    amount = getattr(pi, fields["amount"])
    if getattr(pi, fields["required"]) is not True or amount is None:
        raise TaskServiceError(f"{currency} freight amount and Required=True are needed.")
    payload = {"currency": currency, "confirmed_amount": money_text(amount)}
    setattr(pi, fields["confirmed"], True)
    return mark_done(task, actor_id=actor_id, note=note, payload=payload, now=now)


def confirm_usd_freight_amount(task_or_id, *, actor_id, note=None, now=None):
    return _confirm_component(task_or_id, actor_id=actor_id, currency="USD", note=note, now=now)


def confirm_cny_freight_amount(task_or_id, *, actor_id, note=None, now=None):
    return _confirm_component(task_or_id, actor_id=actor_id, currency="CNY", note=note, now=now)


def confirm_freight_invoice_issued(task_or_id, *, actor_id, note=None, now=None):
    task = _task(task_or_id)
    if task.task_code != "FREIGHT_INVOICE_ISSUED":
        raise InvalidTransitionError("Task is not the freight invoice confirmation task.")
    task.pi.freight_invoice_issued = "已开具"
    return mark_done(
        task,
        actor_id=actor_id,
        note=note,
        payload={"freight_invoice_issued": "已开具"},
        now=now,
    )


def complete_freight_manual_task(task_or_id, *, actor_id, note=None, now=None):
    task = _task(task_or_id)
    if task.task_code == "FREIGHT_USD_AMOUNT_CONFIRM":
        return confirm_usd_freight_amount(task, actor_id=actor_id, note=note, now=now)
    if task.task_code == "FREIGHT_CNY_AMOUNT_CONFIRM":
        return confirm_cny_freight_amount(task, actor_id=actor_id, note=note, now=now)
    if task.task_code == "FREIGHT_INVOICE_ISSUED":
        return confirm_freight_invoice_issued(task, actor_id=actor_id, note=note, now=now)
    return mark_done(task, actor_id=actor_id, note=note, now=now)


def update_freight_payment_status(pi, *, status, paid_at=None):
    if status not in (None, "", "已付款", "未付款"):
        raise ValueError("Invalid freight payment status.")
    pi.freight_payment_status = status or None
    pi.freight_paid_at = paid_at
    return pi
