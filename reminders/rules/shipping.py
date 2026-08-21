"""Phase 2B shipping facts and RULE_DATA reminder definitions."""

from datetime import datetime, time, timedelta

from reminders.definitions import ACTION, UPCOMING, RuleDefinition, ensure, ignore, resolve
from reminders.enums import CompletionMode, TaskHealth


# Confirmed business rule: driver information enters ACTION 24 hours before loading.
DRIVER_INFO_REMINDER_LEAD_HOURS = 24


def loading_moment(pi):
    """Return the best available loading moment and its precision."""
    if pi.container_loading_at is not None:
        return pi.container_loading_at, "DATETIME"
    if pi.container_date is not None:
        return datetime.combine(pi.container_date, time.min), "DATE"
    return None, None


def driver_info_complete(pi):
    return all(
        isinstance(value, str) and bool(value.strip())
        for value in (pi.driver_name, pi.driver_phone, pi.vehicle_number)
    )


def _existing(rule_context, task_code):
    return task_code in rule_context.task_statuses


def make_driver_rule(lead_hours=DRIVER_INFO_REMINDER_LEAD_HOURS):
    def evaluate(pi, now, rule_context):
        loading_at, precision = loading_moment(pi)
        context = {
            "action_target": "UPDATE_LOADING_INFO",
            "driver_info_complete": driver_info_complete(pi),
            "loading_at": loading_at.isoformat() if loading_at else None,
            "loading_precision": precision,
            "driver_reminder_lead_hours": lead_hours,
            "business_parameter_status": (
                "CONFIRMED"
                if lead_hours == DRIVER_INFO_REMINDER_LEAD_HOURS
                else "TEST_OVERRIDE"
            ),
        }
        if driver_info_complete(pi):
            return resolve("DRIVER_INFO_COMPLETE", context=context)
        if loading_at is None:
            if _existing(rule_context, "SHIPPING_DRIVER_INFO"):
                return ensure(
                    UPCOMING,
                    reason_code="RULE_DEFERRED",
                    context=context,
                )
            return ignore()

        activation_at = loading_at - timedelta(hours=lead_hours)
        context["activation_at"] = activation_at.isoformat()
        if now < activation_at:
            return ensure(
                UPCOMING,
                reason_code="TRIGGER_IN_FUTURE",
                activation_at=activation_at,
                context=context,
            )
        if now >= loading_at:
            context.update(
                {
                    "health_reason_code": "DRIVER_INFO_MISSING",
                    "health_message": "装柜时间已到，但司机信息尚未完整填写",
                }
            )
            return ensure(
                ACTION,
                health=TaskHealth.EXCEPTION,
                reason_code="DRIVER_INFO_MISSING",
                activation_at=activation_at,
                context=context,
            )
        return ensure(
            ACTION,
            reason_code="DRIVER_INFO_WINDOW_OPEN",
            activation_at=activation_at,
            context=context,
        )

    return RuleDefinition(
        "shipping.driver_info",
        1,
        "SHIPPING_DRIVER_INFO",
        "确认司机信息",
        CompletionMode.RULE_DATA,
        evaluate,
    )


def _actual_date_rule(*, date_field, actual_field, task_code, rule_key, title, missing_code, label, action_target):
    def evaluate(pi, now, rule_context):
        expected = getattr(pi, date_field)
        actual = getattr(pi, actual_field)
        context = {
            "action_target": action_target,
            date_field: expected.isoformat() if expected else None,
            actual_field: actual.isoformat() if actual else None,
        }
        if actual is not None:
            return resolve(f"{actual_field.upper()}_RECORDED", context=context)
        if expected is None:
            if _existing(rule_context, task_code):
                return ensure(UPCOMING, reason_code="RULE_DEFERRED", context=context)
            return ignore()
        activation_at = datetime.combine(expected, time.min)
        if now.date() < expected:
            return ensure(
                UPCOMING,
                reason_code="TRIGGER_IN_FUTURE",
                activation_at=activation_at,
                context=context,
            )

        overdue_days = (now.date() - expected).days
        message = (
            f"{label} 已过 {overdue_days} 天，尚未记录实际日期"
            if overdue_days > 0
            else f"{label} 已到，尚未记录实际日期"
        )
        context.update(
            {
                "overdue_days": overdue_days,
                "health_reason_code": missing_code,
                "health_message": message,
            }
        )
        return ensure(
            ACTION,
            health=TaskHealth.EXCEPTION,
            reason_code=missing_code,
            activation_at=activation_at,
            context=context,
        )

    return RuleDefinition(
        rule_key,
        1,
        task_code,
        title,
        CompletionMode.RULE_DATA,
        evaluate,
    )


def build_shipping_rules(driver_lead_hours=DRIVER_INFO_REMINDER_LEAD_HOURS):
    return (
        make_driver_rule(driver_lead_hours),
        _actual_date_rule(
            date_field="etd",
            actual_field="actual_departure_date",
            task_code="SHIPPING_ACTUAL_DEPARTURE",
            rule_key="shipping.actual_departure",
            title="确认船舶实际开航情况",
            missing_code="ACTUAL_DEPARTURE_MISSING",
            label="ETD",
            action_target="UPDATE_SHIPPING_INFO",
        ),
        _actual_date_rule(
            date_field="eta",
            actual_field="actual_arrival_date",
            task_code="SHIPPING_ACTUAL_ARRIVAL",
            rule_key="shipping.actual_arrival",
            title="确认实际到港日期",
            missing_code="ACTUAL_ARRIVAL_MISSING",
            label="ETA",
            action_target="UPDATE_ARRIVAL_INFO",
        ),
    )


SHIPPING_RULES = build_shipping_rules()
