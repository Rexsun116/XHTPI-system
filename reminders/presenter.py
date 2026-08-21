"""User-facing adapters for Dashboard task cards and activity history."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from reminders.enums import ActivityEvent, CompletionMode, TaskHealth, TaskSource, TaskStatus


WAITING_LABELS = {
    "CUSTOMER": "客户",
    "FACTORY": "工厂",
    "FREIGHT_FORWARDER": "货代",
    "BANK": "银行",
    "INTERNAL": "内部",
    "OTHER": "其他",
}

EVENT_LABELS = {
    ActivityEvent.CREATED: "创建任务",
    ActivityEvent.STATUS_CHANGED: "状态更新",
    ActivityEvent.FOLLOW_UP: "跟进记录",
    ActivityEvent.WAITING_STARTED: "开始等待",
    ActivityEvent.COMPLETED: "已完成",
    ActivityEvent.AUTO_RESOLVED: "订单数据已补齐，自动解决",
    ActivityEvent.REOPENED: "重新打开",
    ActivityEvent.NOTE: "备注",
    ActivityEvent.CANCELLED: "已取消",
    ActivityEvent.REACTIVATED: "重新激活",
}

ACTION_TARGETS = {
    "UPDATE_LOADING_INFO": "更新装柜信息",
    "UPDATE_SHIPPING_INFO": "更新发运信息",
    "UPDATE_ARRIVAL_INFO": "更新到港信息",
    "UPDATE_PAYMENT_INFO": "更新付款信息",
    "UPDATE_FREIGHT_INFO": "更新货代账单",
    "UPDATE_FREIGHT_PAYMENT": "更新付款状态",
}


def _customer_name(pi):
    return (
        getattr(pi, "customer_name_snapshot", None)
        or (pi.customer.name if getattr(pi, "customer", None) else None)
        or "客户未填写"
    )


def _is_commission(pi):
    return bool(getattr(pi, "commission_factory_id", None) or getattr(pi, "commission_exporter_id", None))


def _status_url(pi):
    if _is_commission(pi):
        return f"/commission-pi/{pi.id}/update-status"
    return f"/pi/{pi.id}/update-status"


def _date_value(value):
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def format_date(value, *, include_time=False):
    parsed = _date_value(value)
    if parsed is None:
        return None
    if isinstance(parsed, datetime) and include_time:
        return parsed.strftime("%Y-%m-%d %H:%M")
    return parsed.strftime("%Y-%m-%d")


def relative_date(value, now):
    parsed = _date_value(value)
    if parsed is None:
        return None
    target = parsed.date() if isinstance(parsed, datetime) else parsed
    delta = (target - now.date()).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == -1:
        return "逾期 1 天"
    if delta < 0:
        return f"逾期 {-delta} 天"
    return f"还有 {delta} 天"


def format_money(value, currency=None):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    number = f"{amount:,.2f}"
    return f"{currency} {number}" if currency else f"{number} · 币种未确认"


def _context_lines(task, context):
    amounts = []
    dates = []
    currency = context.get("currency")

    if context.get("outstanding_amount") is not None:
        amounts.append(("待收", format_money(context["outstanding_amount"], currency)))
    elif context.get("expected_amount") is not None:
        amounts.append(("应收", format_money(context["expected_amount"], currency)))
    elif context.get("amount") is not None:
        amounts.append(("账单金额", format_money(context["amount"], currency)))

    if context.get("received_amount") not in (None, "0", "0.00"):
        amounts.append(("已收", format_money(context["received_amount"], currency)))
    if context.get("usd_freight") is not None:
        amounts.append(("美元海运费", format_money(context["usd_freight"], "USD")))
    if context.get("cny_charges") is not None:
        amounts.append(("人民币费用", format_money(context["cny_charges"], "CNY")))

    date_fields = (
        ("装柜", "loading_at", True),
        ("ETD", "etd", False),
        ("ETA", "eta", False),
        ("触发日", "trigger_date", False),
        ("实际发运", "actual_departure_date", False),
        ("实际到港", "actual_arrival_date", False),
    )
    for label, key, include_time in date_fields:
        formatted = format_date(context.get(key), include_time=include_time)
        if formatted:
            dates.append((label, formatted))
    return amounts, dates


def _activity_payload_lines(activity):
    payload = activity.payload or {}
    lines = []
    if payload.get("tracking_number"):
        lines.append(("运单号", str(payload["tracking_number"])))
    if payload.get("carrier"):
        lines.append(("物流公司", str(payload["carrier"])))
    if payload.get("confirmed_amount") is not None:
        lines.append(
            (
                "确认金额",
                format_money(payload["confirmed_amount"], payload.get("currency")),
            )
        )
    if payload.get("freight_invoice_issued"):
        lines.append(("货代发票", str(payload["freight_invoice_issued"])))
    return lines


def present_activity(activity):
    actor = "系统" if activity.actor_type == "SYSTEM" else (
        activity.actor.username if activity.actor else "用户"
    )
    return {
        "id": activity.id,
        "event": EVENT_LABELS.get(activity.event_type, activity.event_type),
        "created_at": format_date(activity.created_at, include_time=True),
        "actor": actor,
        "note": activity.note,
        "waiting_on": WAITING_LABELS.get(activity.waiting_on, activity.waiting_on),
        "next_follow_up_at": format_date(activity.next_follow_up_at, include_time=True),
        "payload_lines": _activity_payload_lines(activity),
    }


def _recent_follow_up(task):
    for activity in reversed(task.activities):
        if activity.event_type in (ActivityEvent.FOLLOW_UP, ActivityEvent.WAITING_STARTED) and activity.note:
            return {
                "note": activity.note,
                "at": format_date(activity.created_at, include_time=True),
            }
    return None


def _latest_completion(task):
    for activity in reversed(task.activities):
        if activity.event_type in (ActivityEvent.COMPLETED, ActivityEvent.AUTO_RESOLVED):
            return activity
    return None


def _completion_label(task):
    if task.resolution_code == "LEGACY_DONE":
        return "历史已完成"
    if task.resolution_code == "MANUAL_DONE":
        return "人工完成"
    if task.completion_mode == CompletionMode.RULE_DATA:
        return "订单数据自动解决"
    return "已完成"


def present_task(task, *, now, effective_status=None, effective_health=None):
    pi = task.pi
    context = task.context_payload or {}
    status = effective_status or task.status
    health = effective_health or task.health
    amount_lines, date_lines = _context_lines(task, context)
    latest_completion = _latest_completion(task)
    action_target = context.get("action_target")
    update_url = _status_url(pi)

    if health == TaskHealth.EXCEPTION:
        badge = "Exception"
    else:
        badge = {
            TaskStatus.ACTION: "Action",
            TaskStatus.WAITING: "Waiting",
            TaskStatus.UPCOMING: "Upcoming",
            TaskStatus.DONE: "Done",
        }.get(status, status.title())

    primary_action = None
    if status in TaskStatus.ACTIVE:
        if task.completion_mode == CompletionMode.RULE_DATA and task.task_code == "PAYMENT_BALANCE_FOLLOWUP":
            primary_action = {"kind": "follow_up", "label": "记录跟进"}
        elif task.completion_mode == CompletionMode.RULE_DATA:
            primary_action = {
                "kind": "navigate",
                "label": ACTION_TARGETS.get(action_target, "更新订单信息"),
                "url": update_url,
            }
        elif status == TaskStatus.WAITING:
            primary_action = {"kind": "follow_up", "label": "Follow-up"}
        elif status == TaskStatus.ACTION and task.completion_mode == CompletionMode.MANUAL_REQUIRED_INPUT:
            primary_action = {"kind": "required_done", "label": "完成并填写信息"}
        elif status == TaskStatus.ACTION:
            primary_action = {"kind": "done", "label": "Done"}

    warnings = list(context.get("warnings") or [])
    health_message = context.get("health_message")
    if health == TaskHealth.OVERDUE and not health_message:
        relevant = task.next_follow_up_at or task.due_at or task.activation_at
        health_message = relative_date(relevant, now)

    return {
        "id": task.id,
        "task_code": task.task_code,
        "pi_id": pi.id,
        "pi_no": pi.pi_no,
        "customer_name": _customer_name(pi),
        "order_status": pi.status or "状态未填写",
        "order_url": f"/pi/{pi.id}",
        "title": task.title,
        "description": task.description,
        "status": status,
        "stored_status": task.status,
        "health": health,
        "badge": badge,
        "amount_lines": [line for line in amount_lines if line[1]],
        "date_lines": date_lines,
        "health_message": health_message,
        "warnings": warnings,
        "waiting_on": WAITING_LABELS.get(task.waiting_on, task.waiting_on),
        "waiting_since": format_date(task.waiting_since, include_time=True),
        "next_follow_up_at": format_date(task.next_follow_up_at, include_time=True),
        "next_follow_up_relative": relative_date(task.next_follow_up_at, now),
        "recent_follow_up": _recent_follow_up(task),
        "primary_action": primary_action,
        "can_move_waiting": status == TaskStatus.ACTION and task.completion_mode != CompletionMode.RULE_DATA,
        "can_reopen": status == TaskStatus.DONE and task.completion_mode != CompletionMode.RULE_DATA,
        "can_cancel": status in TaskStatus.ACTIVE and task.source == TaskSource.MANUAL,
        "completion_schema": task.completion_schema or {},
        "completed_at": format_date(task.completed_at, include_time=True),
        "completion_label": _completion_label(task),
        "completion_payload_lines": _activity_payload_lines(latest_completion) if latest_completion else [],
        "activities": [present_activity(activity) for activity in reversed(task.activities)],
        "source_label": "系统任务" if task.source == TaskSource.AUTO else "手工任务",
    }
