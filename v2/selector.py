"""Read-only task projection and Next Action ordering."""

from datetime import date, datetime

from .business_time import arrival_schedule_projection, business_today
from .models import utcnow


def projected_details(task, now=None):
    """Read-only task state plus presentation context for the current business day."""
    now = now or utcnow()
    today = business_today(now)
    status, health = task.status, task.health
    context = task.context_payload or {}
    if status == "UPCOMING" and task.activation_at and task.activation_at.date() <= today:
        status = "ACTION"
    if status == "WAITING" and task.next_follow_up_at and task.next_follow_up_at.date() <= today:
        status, health = "ACTION", "OVERDUE"
    if status == "ACTION" and task.due_at and task.due_at.date() < today and health != "EXCEPTION":
        health = "OVERDUE"
    if task.task_code == "SHIPPING_ACTUAL_ARRIVAL" and status not in {"DONE", "CANCELLED"}:
        raw_eta = context.get("eta")
        try:
            eta = date.fromisoformat(raw_eta) if raw_eta else None
        except ValueError:
            eta = None
        if eta:
            schedule = arrival_schedule_projection(eta, today)
            context = {
                **context,
                "eta": eta.isoformat(),
                "trigger_date": schedule["trigger_date"].isoformat(),
                "action_target": "ENTER_ARRIVED",
                "message": schedule["message"],
            }
            if task.status == "WAITING" and task.next_follow_up_at and task.next_follow_up_at.date() > today:
                status = "WAITING"
            else:
                status = schedule["status"]
            health = schedule["health"]
    return status, health, context


def projected(task, now=None):
    status, health, _ = projected_details(task, now)
    return status, health


def sort_key(task, now=None):
    now = now or utcnow()
    today = business_today(now)
    status, health = projected(task, now)
    due_followup = task.next_follow_up_at and task.next_follow_up_at.date() <= today
    bucket = (0 if health == "EXCEPTION" and status not in {"DONE", "CANCELLED"}
              else 10 if health == "OVERDUE" and status == "ACTION"
              else 20 if status == "ACTION"
              else 30 if due_followup
              else 40 if status == "WAITING"
              else 50 if status == "UPCOMING"
              else 99)
    relevant = task.due_at or task.next_follow_up_at or task.activation_at or datetime.max
    return bucket, task.priority, relevant, task.id


def select_next_action(tasks, now=None):
    active = [task for task in tasks if projected(task, now)[0] not in {"DONE", "CANCELLED"}]
    return min(active, key=lambda task: sort_key(task, now)) if active else None
