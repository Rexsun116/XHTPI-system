"""Business-calendar helpers for V2 date-based workflow rules."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def business_today(now=None):
    """Return today's date in the V2 Asia/Shanghai business calendar.

    V2 persists event timestamps as naive UTC for SQLite compatibility.  A
    supplied naive datetime is therefore interpreted as UTC before converting
    it to the business timezone.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(BUSINESS_TIMEZONE).date()


def arrival_schedule_projection(eta, today):
    """Return the calendar-driven state for an unresolved ETA reminder.

    Both reconciliation and read-only dashboard projection use this one
    definition so their Shanghai-date boundaries cannot diverge.
    """
    activation_date = eta - timedelta(days=2)
    reached_eta = today >= eta
    return {
        "status": "ACTION" if today >= activation_date else "UPCOMING",
        "health": "EXCEPTION" if reached_eta else "NORMAL",
        "trigger_date": activation_date,
        "message": (
            f"ETA 已过 {(today - eta).days} 天，尚未记录实际到港日期"
            if reached_eta else None
        ),
    }


def departure_schedule_projection(etd, today):
    """Return the ETD-based state for an unresolved actual-departure task."""
    if etd is None:
        return {"title": "录入 ETD", "status": "ACTION", "health": "NORMAL",
                "message": "尚未确认 ETD，请向货代确认预计开航日期。", "activation_at": None}
    if today < etd:
        return {"title": "确认船舶实际开航情况", "status": "UPCOMING", "health": "NORMAL",
                "message": "ETD 尚未到期，等待货代确认实际发运。", "activation_at": etd}
    if today == etd:
        return {"title": "确认船舶实际开航情况", "status": "ACTION", "health": "NORMAL",
                "message": "ETD 今日，尚未确认实际发运。", "activation_at": etd}
    return {"title": "确认船舶实际开航情况", "status": "ACTION", "health": "EXCEPTION",
            "message": "ETD 已过，尚未确认实际发运。", "activation_at": etd,
            "days_overdue": (today - etd).days}
