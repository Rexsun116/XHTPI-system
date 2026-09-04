"""User-facing task/activity presentation without exposing raw JSON."""

from decimal import Decimal, InvalidOperation


def format_task_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def money(currency, amount):
    if amount in (None, ""):
        return None
    try:
        value = Decimal(str(amount))
        rendered = f"{value:,.2f}"
    except (InvalidOperation, ValueError):
        rendered = str(amount)
    return f"{currency} {rendered}" if currency else f"{rendered} · Currency not confirmed"


def present_task(task):
    context = task.context_payload or {}
    lines = []
    for key, label in (("expected_amount", "Expected"), ("received_amount", "Received"),
                       ("outstanding_amount", "Outstanding"), ("amount", "Amount"),
                       ("agreed_amount", "Agreed"), ("actual_amount", "Actual"),
                       ("difference", "Difference")):
        if context.get(key) is not None:
            lines.append(f"{label}: {money(context.get('currency'), context[key])}")
    for key, label in (("container_loading_at", "Loading"), ("planned_shipment_date", "Planned shipment"),
                       ("etd", "ETD"), ("eta", "ETA"), ("trigger_date", "Trigger")):
        if context.get(key):
            lines.append(f"{label}: {context[key]}")
    if context.get("message"):
        lines.append(context["message"])
    if context.get("days_remaining") is not None:
        days = context["days_remaining"]
        lines.append(f"Days remaining: {days}" if days >= 0 else f"Days overdue: {abs(days)}")
    if context.get("days_overdue") is not None:
        lines.append(f"Days overdue: {context['days_overdue']}")
    return {"lines": lines, "warning": context.get("warning"), "customer": task.pi.customer_name_snapshot,
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
    context = task.context_payload or {}
    actions = []
    target = context.get("action_target")
    status = getattr(task, "_dashboard_status", task.status)
    health = getattr(task, "_dashboard_health", task.health)
    if status in {"ACTION", "WAITING"}:
        if task.task_code == "STAGE_GATE_SHIPPED":
            if context.get("missing_preparation"):
                if "工厂装柜日期尚未确认" in context["missing_preparation"]:
                    actions.append({"kind": "edit_shipment", "label": "填写装柜信息"})
                if "货代船期/最终报价尚未确认" in context["missing_preparation"]:
                    actions.append({"kind": "edit_freight", "label": "查看/录入报价"})
            else:
                actions.append({"kind": "enter_shipped", "label": "更新为已发运"})
            actions.append({"kind": "edit_shipment", "label": "修改计划发运日期"})
        elif task.task_code in {"PAYMENT_ADVANCE_WAITING", "PAYMENT_BALANCE_FOLLOWUP"}:
            actions.append({"kind": "followup", "label": "Follow-up"})
            actions.append({"kind": "advance_receipt" if task.task_code == "PAYMENT_ADVANCE_WAITING" else "view_order",
                            "label": "登记预付款到账" if task.task_code == "PAYMENT_ADVANCE_WAITING" else "更新付款信息"})
            if health == "EXCEPTION":
                actions.append({"kind": "edit_shipment", "label": "修改计划发运日期"})
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
}


def present_activity(activity):
    payload = activity.payload or {}
    details = []
    if payload.get("tracking_number"):
        details.append(f"Tracking: {payload['tracking_number']}")
    if payload.get("carrier"):
        details.append(f"Carrier: {payload['carrier']}")
    if payload.get("confirmed_amount"):
        details.append(f"Confirmed: {money(payload.get('currency'), payload['confirmed_amount'])}")
    if payload.get("waiting_on"):
        details.append(f"Waiting on: {payload['waiting_on']}")
    if payload.get("next_follow_up_at"):
        details.append(f"Next follow-up: {payload['next_follow_up_at']}")
    return {"label": EVENT_LABELS.get(activity.event_type, activity.event_type.replace("_", " ").title()),
            "details": details, "when": format_task_datetime(activity.created_at)}
