"""Validated V2 task mutations with append-only activity history."""

from datetime import datetime

from .models import TaskActivity, db, utcnow


class TaskOperationError(ValueError):
    pass


def _activity(task, event, actor_id, *, old=None, note=None, payload=None):
    db.session.add(TaskActivity(
        task_id=task.id, event_type=event, from_status=old, to_status=task.status,
        actor_type="USER", actor_id=actor_id, note=note, payload=payload,
    ))


def mark_done(task, actor_id, *, note=None, payload=None):
    if task.completion_mode == "RULE_DATA":
        raise TaskOperationError("This task is resolved by business data and cannot be completed manually.")
    payload = dict(payload or {})
    if task.completion_mode == "MANUAL_REQUIRED_INPUT":
        tracking = str(payload.get("tracking_number") or "").strip()
        if not tracking:
            raise TaskOperationError("Tracking Number is required.")
        payload["tracking_number"] = tracking
    if task.status not in {"ACTION", "WAITING", "UPCOMING"}:
        raise TaskOperationError("Only an active task can be completed.")
    old = task.status
    task.status = "DONE"
    task.health = "NORMAL"
    task.completed_at = utcnow()
    task.completed_by_id = actor_id
    task.resolution_code = "MANUAL_DONE"
    task.waiting_on = task.waiting_since = task.next_follow_up_at = None
    _activity(task, "COMPLETED", actor_id, old=old, note=note, payload=payload or None)


def move_to_waiting(task, actor_id, *, waiting_on, next_follow_up_at=None, note=None, event="WAITING_STARTED"):
    if task.status not in {"ACTION", "WAITING"}:
        raise TaskOperationError("Only ACTION or WAITING tasks can be moved to waiting.")
    if not waiting_on:
        raise TaskOperationError("Waiting On is required.")
    old = task.status
    task.status = "WAITING"
    task.waiting_on = waiting_on
    task.waiting_since = utcnow()
    task.next_follow_up_at = next_follow_up_at
    task.health = "NORMAL"
    _activity(task, event, actor_id, old=old, note=note, payload={
        "waiting_on": waiting_on,
        "next_follow_up_at": next_follow_up_at.isoformat() if next_follow_up_at else None,
    })


def follow_up(task, actor_id, *, waiting_on, next_follow_up_at=None, note=None, continue_waiting=True):
    if not note or not note.strip():
        raise TaskOperationError("Follow-up note is required.")
    if continue_waiting:
        move_to_waiting(task, actor_id, waiting_on=waiting_on, next_follow_up_at=next_follow_up_at,
                        note=note.strip(), event="FOLLOW_UP")
    else:
        old = task.status
        task.status, task.health = "ACTION", "NORMAL"
        task.waiting_on = task.waiting_since = task.next_follow_up_at = None
        _activity(task, "FOLLOW_UP", actor_id, old=old, note=note.strip())


def reopen(task, actor_id, *, reason):
    if task.completion_mode == "RULE_DATA":
        raise TaskOperationError("Data-driven tasks can only be reactivated by changed business facts.")
    if task.status != "DONE" or not reason or not reason.strip():
        raise TaskOperationError("A completed task and reopen reason are required.")
    old = task.status
    task.status, task.health = "ACTION", "NORMAL"
    task.completed_at = task.completed_by_id = task.resolution_code = None
    _activity(task, "REOPENED", actor_id, old=old, note=reason.strip())


def cancel_manual(task, actor_id, *, reason):
    if task.source != "MANUAL":
        raise TaskOperationError("AUTO tasks are cancelled only by the rule engine.")
    if task.status in {"DONE", "CANCELLED"} or not reason or not reason.strip():
        raise TaskOperationError("An active manual task and cancellation reason are required.")
    old = task.status
    task.status, task.health, task.resolution_code = "CANCELLED", "NORMAL", "MANUAL_CANCELLED"
    _activity(task, "CANCELLED", actor_id, old=old, note=reason.strip())


def parse_datetime(value):
    return datetime.fromisoformat(value) if value else None
