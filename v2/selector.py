"""Read-only task projection and Next Action ordering."""

from datetime import datetime


def projected(task, now=None):
    now = now or datetime.now()
    status, health = task.status, task.health
    if status == "UPCOMING" and task.activation_at and task.activation_at <= now:
        status = "ACTION"
    if status == "WAITING" and task.next_follow_up_at and task.next_follow_up_at <= now:
        status, health = "ACTION", "OVERDUE"
    if status == "ACTION" and task.due_at and task.due_at < now and health != "EXCEPTION":
        health = "OVERDUE"
    return status, health


def sort_key(task, now=None):
    now = now or datetime.now()
    status, health = projected(task, now)
    due_followup = task.next_follow_up_at and task.next_follow_up_at.date() <= now.date()
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
