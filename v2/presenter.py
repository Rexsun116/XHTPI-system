"""User-facing task/activity presentation without exposing raw JSON."""

from decimal import Decimal, InvalidOperation
from .business_time import business_event_time
from .selector import sort_key


DOCUMENT_PRESENTATION = (
    ("coo_required", "COO / Certificate of Origin", ("DOCUMENT_COO",)),
    ("apta_required", "APTA Certificate", ("DOCUMENT_APTA",)),
    ("export_license_required", "Export License", ("DOCUMENT_EXPORT_LICENSE",)),
    ("customs_docs_required", "Customs Documents", ("DOCUMENT_CUSTOMS",)),
    ("coc_required", "COC", ("DOCUMENT_COC",)),
    ("coa_required", "COA", ("DOCUMENT_COA",)),
    ("original_bl_required", "Original B/L", ("DOCUMENT_ORIGINAL_BL",)),
    ("obd_electronic_required", "OBD Electronic B/L", ("DOCUMENT_OBD_BL",)),
    ("insurance_original_required", "Original Insurance Policy", ("DOCUMENT_INSURANCE_ORIGINAL",)),
    ("insurance_electronic_required", "Electronic Insurance Policy", ("DOCUMENT_INSURANCE_ELECTRONIC",)),
    ("original_documents_mail_required", "Original Documents Mail", ("DOCUMENT_ORIGINALS_MAIL",)),
    ("telex_release_required", "Telex Release", ("DOCUMENT_TELEX_RELEASE",)),
    ("settlement_documents_required", "Settlement Documents", (
        "DOCUMENT_EXPORT_SETTLEMENT", "SETTLEMENT_DOCUMENT_ADVANCE", "SETTLEMENT_DOCUMENT_BALANCE",
    )),
)


def format_task_datetime(value):
    return business_event_time(value).strftime("%Y-%m-%d %H:%M") if value else "—"


def money(currency, amount):
    if amount in (None, ""):
        return None
    try:
        value = Decimal(str(amount))
        rendered = f"{value:,.2f}"
    except (InvalidOperation, ValueError):
        rendered = str(amount)
    return f"{currency} {rendered}" if currency else f"{rendered} · Currency not confirmed"


def shipping_dates_for_display(pi):
    """Return the physical-shipment dates for one read-only dashboard row.

    An EXPORT_ORDER never becomes a second shipment record: it presents the
    existing CUSTOMER_ORDER shipment owner when the structured pair is valid.
    A malformed pair deliberately has no owner or inferred dates.
    """
    from .shipment_ownership import shipment_owner_for

    owner = shipment_owner_for(pi)
    if owner is None:
        return {"owner": None, "dates": (("ETD", None), ("ETA", None))}
    if owner.actual_arrival_date:
        dates = (("ATD", owner.actual_departure_date), ("ATA", owner.actual_arrival_date))
    elif owner.actual_departure_date:
        dates = (("ATD", owner.actual_departure_date), ("ETA", owner.eta))
    else:
        dates = (("ETD", owner.etd), ("ETA", owner.eta))
    return {"owner": owner, "dates": dates}


def advance_payment_summary(pi):
    """Read-only projection of the PI's aggregate advance-payment facts."""
    expected = pi.advance_payment_amount or Decimal("0")
    received = pi.advance_received_amount or Decimal("0")
    outstanding = max(expected - received, Decimal("0"))
    if expected <= 0:
        status = "无需预付款"
    elif outstanding == 0:
        status = "已足额到账"
    elif received > 0:
        status = "部分到账"
    else:
        status = "待到账"
    received_at = pi.advance_received_at
    if received_at and hasattr(received_at, "date"):
        received_at = received_at.date()
    return {
        "expected": money(pi.currency, expected),
        "received": money(pi.currency, received),
        "outstanding": money(pi.currency, outstanding),
        "received_at": received_at.isoformat() if received_at else "—",
        "status": status,
    }


def order_payment_summary(pi):
    """Present the recorded customer payment plan without using task state.

    ``pi`` is the financial owner selected by the caller.  Planned advance and
    balance values, and their respective received values, are the existing
    order-payment facts; the registry deliberately does not infer payment from
    reminders or lifecycle state.
    """
    if pi is None:
        return {"state": "UNAVAILABLE", "label": "Unavailable", "detail": "Financial owner unavailable"}
    advance_due = pi.advance_payment_amount or Decimal("0")
    balance_due = pi.balance_payment_amount or Decimal("0")
    advance_received = pi.advance_received_amount or Decimal("0")
    balance_received = pi.balance_received_amount or Decimal("0")
    due = advance_due + balance_due
    received = advance_received + balance_received
    if due <= 0:
        return {"state": "NOT_APPLICABLE", "label": "Not applicable", "detail": "No recorded payment plan"}
    if received >= due:
        return {"state": "PAID", "label": "Paid", "detail": "Advance and balance received"}
    if received > 0:
        detail = "Advance received · balance outstanding" if advance_received >= advance_due else "Partially received"
        return {"state": "PARTIAL", "label": "Partial", "detail": detail}
    return {"state": "OUTSTANDING", "label": "Outstanding", "detail": "No customer payment received"}


def freight_payment_summary(pi, settlement):
    """Present required freight-payment branches from authoritative facts."""
    if pi is None:
        return {"state": "UNAVAILABLE", "label": "Unavailable", "detail": "Financial owner unavailable"}
    if settlement is None:
        return {"state": "NOT_REQUIRED", "label": "Not required", "detail": "No freight settlement required"}
    branches = []
    for currency in ("USD", "CNY"):
        if getattr(settlement, f"{currency.lower()}_bill_required") is True:
            paid = getattr(settlement, f"{currency.lower()}_payment_status") == "PAID"
            branches.append((currency, paid))
    if not branches:
        return {"state": "NOT_REQUIRED", "label": "Not required", "detail": "No required freight branch"}
    if all(paid for _, paid in branches):
        return {"state": "PAID", "label": "Paid", "detail": " · ".join(f"{currency} paid" for currency, _ in branches)}
    paid_labels = [f"{currency} paid" for currency, paid in branches if paid]
    pending_labels = [f"{currency} pending" for currency, paid in branches if not paid]
    return {
        "state": "OUTSTANDING",
        "label": "Outstanding",
        "detail": " · ".join(paid_labels + pending_labels),
    }


def present_task(task):
    context = getattr(task, "_dashboard_context", task.context_payload) or {}
    lines = []
    for key, label in (("expected_amount", "Expected"), ("received_amount", "Received"),
                       ("outstanding_amount", "Outstanding"), ("amount", "Amount"),
                       ("agreed_amount", "Agreed"), ("actual_amount", "Actual"),
                       ("difference", "Difference")):
        if context.get(key) is not None:
            lines.append(f"{label}: {money(context.get('currency'), context[key])}")
    for key, label in (("container_loading_at", "Loading"), ("planned_shipment_date", "Planned shipment"),
                       ("etd", "ETD"), ("eta", "ETA"), ("trigger_date", "Trigger"),
                       ("advance_received_at", "Advance received")):
        if context.get(key):
            lines.append(f"{label}: {context[key]}")
    if context.get("message"):
        lines.append(context["message"])
    if context.get("days_remaining") is not None:
        days = context["days_remaining"]
        lines.append(f"Days remaining: {days}" if days >= 0 else f"Days overdue: {abs(days)}")
    if context.get("days_overdue") is not None:
        lines.append(f"Days overdue: {context['days_overdue']}")
    return {"title": getattr(task, "_dashboard_title", task.title), "lines": lines,
            "warning": context.get("warning"), "customer": task.pi.customer_name_snapshot,
            "actions": task_actions(task), "waiting": waiting_summary(task),
            "display_status": getattr(task, "_dashboard_status", task.status)}


def waiting_summary(task):
    """Presentation-only waiting facts; never mutate timing state on GET."""
    activity = next((row for row in reversed(task.activities) if row.event_type == "FOLLOW_UP"), None)
    return {
        "waiting_on": task.waiting_on,
        "waiting_since": task.waiting_since,
        "last_follow_up_at": activity.created_at if activity else None,
        "last_follow_up_note": activity.note if activity else None,
        "next_follow_up_at": task.next_follow_up_at,
    }


def task_actions(task):
    """Return UI actions by task semantics, keeping task-code knowledge out of Jinja."""
    from .linked_trade import is_export_order
    from .shipment_ownership import is_physical_shipment_task
    from .rules import CUSTOMER_DOCUMENT_TASK_CODES
    if is_export_order(task.pi) and task.task_code in CUSTOMER_DOCUMENT_TASK_CODES:
        return [{"kind": "history", "label": "History"}]
    if is_export_order(task.pi) and is_physical_shipment_task(task.task_code):
        return [{"kind": "history", "label": "History"}]
    context = getattr(task, "_dashboard_context", task.context_payload) or {}
    actions = []
    target = context.get("action_target")
    status = getattr(task, "_dashboard_status", task.status)
    health = getattr(task, "_dashboard_health", task.health)
    if status in {"ACTION", "WAITING"}:
        if task.task_code == "SHIPPING_ACTUAL_DEPARTURE" or context.get("shipment_clock") == "ETD":
            if context.get("etd") or task.pi.is_linked_trade:
                actions.append({"kind": "enter_shipped", "label": "Record Actual Departure"})
            actions.append({"kind": "edit_schedule", "label": "Edit ETD"})
        elif task.task_code == "STAGE_GATE_SHIPPED":
            if context.get("missing_preparation"):
                if "工厂装柜日期尚未确认" in context["missing_preparation"]:
                    actions.append({"kind": "edit_shipment", "label": "填写装柜信息"})
                if "货代船期/最终报价尚未确认" in context["missing_preparation"]:
                    actions.append({"kind": "edit_freight", "label": "查看/录入报价"})
            else:
                actions.append({"kind": "enter_shipped", "label": "更新为已发运"})
        elif task.task_code in {"PAYMENT_ADVANCE_WAITING", "PAYMENT_BALANCE_FOLLOWUP"}:
            actions.append({"kind": "followup", "label": "Follow-up"})
            actions.append({"kind": "advance_receipt" if task.task_code == "PAYMENT_ADVANCE_WAITING" else "view_order",
                            "label": "登记预付款到账" if task.task_code == "PAYMENT_ADVANCE_WAITING" else "更新付款信息"})
            if health == "EXCEPTION" and task.pi.status == "NEW":
                actions.append({"kind": "edit_shipment", "label": "修改计划发运日期"})
        elif task.task_code == "SHIPPING_ETA_MISSING":
            actions.append({"kind": "edit_schedule", "label": "Update ETA"})
        elif task.task_code == "SHIPPING_ACTUAL_ARRIVAL":
            actions.append({"kind": "enter_arrived", "label": "确认货物已到港"})
            actions.append({"kind": "followup", "label": "Follow-up"})
        elif task.task_code == "FREIGHT_FORWARDER_PAYMENT":
            actions.append({"kind": "edit_freight_settlement", "label": "确认付款"})
        elif task.completion_mode == "MANUAL_REQUIRED_INPUT":
            actions.append({"kind": "required_done", "label": "完成邮寄"})
        elif task.completion_mode == "MANUAL":
            actions.append({"kind": "done", "label": "Done"})
        elif target in {"UPDATE_LOADING_INFO", "UPDATE_SHIPPING_INFO"}:
            actions.append({"kind": "edit_shipment", "label": "填写装柜日期" if target == "UPDATE_LOADING_INFO" else "更新订单信息"})
        elif target == "UPDATE_FREIGHT_AGREEMENT":
            actions.append({"kind": "edit_freight", "label": "查看/录入报价"})
        elif target == "UPDATE_ADVANCE_RECEIPT":
            actions.append({"kind": "advance_receipt", "label": "登记预付款到账"})
        elif target == "ENTER_PRE_SHIPMENT":
            actions.append({"kind": "enter_pre_shipment", "label": "进入待发运"})
        else:
            actions.append({"kind": "view_order", "label": "查看订单"})
    elif status == "UPCOMING":
        actions.append({"kind": "view_order", "label": "查看订单"})
        if task.pi.is_linked_trade and task.pi.status == "PRE_SHIPMENT" and (
            task.task_code == "SHIPPING_ACTUAL_DEPARTURE" or context.get("shipment_clock") == "ETD"
        ):
            actions.append({"kind": "enter_shipped", "label": "Record Actual Departure"})
    elif status == "DONE":
        actions.append({"kind": "history", "label": "History"})
        if task.completion_mode != "RULE_DATA":
            actions.append({"kind": "reopen", "label": "Reopen"})
    actions.append({"kind": "history", "label": "History"})
    # Preserve order while removing duplicate History actions.
    unique = []
    for action in actions:
        if action not in unique:
            unique.append(action)
    return unique


EVENT_LABELS = {
    "CREATED": "Created", "COMPLETED": "Completed", "AUTO_RESOLVED": "Auto-resolved",
    "WAITING_STARTED": "Waiting started", "FOLLOW_UP": "Follow-up", "REOPENED": "Reopened",
    "CANCELLED": "Cancelled", "REACTIVATED": "Reactivated", "RULE_REACTIVATED": "Rule reactivated",
    "RULE_DEFERRED": "Deferred", "STATUS_CHANGED": "Status changed",
    "ADVANCE_RECEIPT_RECORDED": "Advance receipt confirmation",
}


def present_activity(activity):
    payload = activity.payload or {}
    details = []
    if payload.get("tracking_number"):
        details.append(f"Tracking: {payload['tracking_number']}")
    if payload.get("carrier"):
        details.append(f"Carrier: {payload['carrier']}")
    if payload.get("confirmed_amount"):
        details.append(f"Confirmed cumulative advance: {money(payload.get('currency'), payload['confirmed_amount'])}")
    if payload.get("previous_amount") is not None:
        details.append(f"Previous cumulative advance: {money(payload.get('currency'), payload['previous_amount'])}")
    if payload.get("received_at"):
        details.append(f"Receipt date: {payload['received_at']}")
    if payload.get("previous_received_at"):
        details.append(f"Previous receipt date: {payload['previous_received_at']}")
    if payload.get("waiting_on"):
        details.append(f"Waiting on: {payload['waiting_on']}")
    if payload.get("next_follow_up_at"):
        details.append(f"Next follow-up: {payload['next_follow_up_at']}")
    return {"label": EVENT_LABELS.get(activity.event_type, activity.event_type.replace("_", " ").title()),
            "details": details, "when": format_task_datetime(activity.created_at)}


def order_action_summary(tasks):
    """Build the read-only order focus panel from already projected tasks."""
    if tasks and tasks[0].pi.status == "COMPLETED":
        return {
            "items": [], "primary": None, "secondary": [], "active_count": 0,
            "exception_count": 0, "action_count": 0, "waiting_count": 0,
        }
    active = [task for task in tasks
              if getattr(task, "_dashboard_status", task.status) not in {"DONE", "CANCELLED"}]
    active.sort(key=sort_key)
    items = []
    for task in active:
        presentation = present_task(task)
        primary_action = next(
            (action for action in presentation["actions"] if action["kind"] != "history"),
            None,
        )
        items.append({"task": task, "presentation": presentation, "primary_action": primary_action})
    return {
        "items": items,
        "primary": items[0] if items else None,
        "secondary": items[1:],
        "active_count": len(items),
        "exception_count": sum(
            getattr(task, "_dashboard_health", task.health) == "EXCEPTION" for task in active
        ),
        "action_count": sum(
            getattr(task, "_dashboard_status", task.status) == "ACTION"
            and getattr(task, "_dashboard_health", task.health) != "EXCEPTION"
            for task in active
        ),
        "waiting_count": sum(
            getattr(task, "_dashboard_status", task.status) == "WAITING" for task in active
        ),
    }


def order_activity_feed(tasks):
    """Return one chronological presentation feed across the order's tasks."""
    rows = [
        {"task": task, "activity": activity, "presentation": present_activity(activity)}
        for task in tasks
        for activity in task.activities
    ]
    rows.sort(key=lambda row: (row["activity"].created_at, row["activity"].id or 0), reverse=True)
    return rows


def order_document_rows(pi, tasks):
    """Keep requirement facts separate from the task-based processing state."""
    by_code = {}
    for task in tasks:
        by_code.setdefault(task.task_code, []).append(task)
    rows = []
    for field, label, task_codes in DOCUMENT_PRESENTATION:
        required = getattr(pi, field)
        related = [task for code in task_codes for task in by_code.get(code, ())]
        related.sort(key=sort_key)
        if related:
            statuses = []
            for task in related:
                status = getattr(task, "_dashboard_status", task.status)
                if status not in statuses:
                    statuses.append(status)
            processing = " / ".join(statuses)
        else:
            processing = "尚未启动" if required is True else "—"
        rows.append({
            "field": field,
            "label": label,
            "requirement": "需要" if required is True else "不需要" if required is False else "尚未确认",
            "processing": processing,
        })
    return rows
