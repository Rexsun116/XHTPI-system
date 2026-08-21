"""Transactional task state transitions for the reminder foundation.

Functions flush but do not commit. Routes or callers own the transaction boundary.
"""

from datetime import datetime

from app import PI, User, db
from reminders.enums import (
    ActivityEvent,
    ActorType,
    CompletionMode,
    TaskHealth,
    TaskScope,
    TaskSource,
    TaskStatus,
    WaitingOn,
)
from task_models import OrderTask, TaskActivity, utc_now


class TaskServiceError(ValueError):
    """Base error for invalid task input or state transitions."""


class TaskNotFoundError(TaskServiceError):
    pass


class InvalidTransitionError(TaskServiceError):
    pass


class CompletionValidationError(TaskServiceError):
    pass


def _now(value=None):
    return value or utc_now()


def _require_actor(actor_id):
    if actor_id is None or db.session.get(User, actor_id) is None:
        raise TaskServiceError("A valid authenticated user is required.")


def _require_task(task_or_id):
    task = task_or_id if isinstance(task_or_id, OrderTask) else db.session.get(OrderTask, task_or_id)
    if task is None or db.session.get(PI, task.pi_id) is None:
        raise TaskNotFoundError("Task or its PI does not exist.")
    return task


def _validate_choice(value, choices, field):
    if value not in choices:
        raise TaskServiceError(f"Invalid {field}: {value}")


def _append_activity(
    task,
    event_type,
    *,
    actor_type,
    actor_id=None,
    from_status=None,
    to_status=None,
    from_health=None,
    to_health=None,
    reason_code=None,
    note=None,
    waiting_on=None,
    next_follow_up_at=None,
    payload=None,
    created_at=None,
):
    activity = TaskActivity(
        task=task,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        from_health=from_health,
        to_health=to_health,
        reason_code=reason_code,
        note=note,
        waiting_on=waiting_on,
        next_follow_up_at=next_follow_up_at,
        payload=payload,
        created_at=created_at or utc_now(),
    )
    db.session.add(activity)
    return activity


def _calculated_health(task, now):
    if task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
        return TaskHealth.NORMAL
    if task.health == TaskHealth.EXCEPTION:
        return TaskHealth.EXCEPTION

    deadlines = []
    if task.due_at:
        deadlines.append(task.due_at)
    if task.status in (TaskStatus.UPCOMING, TaskStatus.ACTION) and task.activation_at:
        deadlines.append(task.activation_at)
    if task.status in (TaskStatus.WAITING, TaskStatus.ACTION) and task.next_follow_up_at:
        deadlines.append(task.next_follow_up_at)
    return TaskHealth.OVERDUE if any(value < now for value in deadlines) else TaskHealth.NORMAL


def project_task_timing(task, now=None):
    """Return the timing-effective status/health without mutating the task.

    Dashboard GET requests use this projection so due follow-ups and activations
    are displayed correctly without turning a read into an implicit database
    write. ``reconcile_task_timing`` is the explicit persistence path and uses
    this same calculation.
    """
    now = _now(now)
    if task.status not in TaskStatus.ACTIVE:
        return task.status, TaskHealth.NORMAL, None

    status = task.status
    reason_code = None
    if status == TaskStatus.UPCOMING and task.activation_at and task.activation_at <= now:
        status = TaskStatus.ACTION
        reason_code = "ACTIVATION_DUE"
    elif status == TaskStatus.WAITING and task.next_follow_up_at and task.next_follow_up_at <= now:
        status = TaskStatus.ACTION
        reason_code = "FOLLOW_UP_DUE"

    if task.health == TaskHealth.EXCEPTION:
        health = TaskHealth.EXCEPTION
    else:
        deadlines = []
        if task.due_at:
            deadlines.append(task.due_at)
        if status in (TaskStatus.UPCOMING, TaskStatus.ACTION) and task.activation_at:
            deadlines.append(task.activation_at)
        if status in (TaskStatus.WAITING, TaskStatus.ACTION) and task.next_follow_up_at:
            deadlines.append(task.next_follow_up_at)
        health = (
            TaskHealth.OVERDUE
            if any(value < now for value in deadlines)
            else TaskHealth.NORMAL
        )
    return status, health, reason_code


def _touch(task, now):
    task.updated_at = now


def create_manual_task(
    *,
    pi_id,
    title,
    actor_id,
    description=None,
    priority=100,
    due_at=None,
    activation_at=None,
    initial_status=None,
    completion_mode=CompletionMode.MANUAL,
    completion_schema=None,
    context_payload=None,
    now=None,
):
    now = _now(now)
    if db.session.get(PI, pi_id) is None:
        raise TaskServiceError("PI does not exist.")
    _require_actor(actor_id)
    if not title or not title.strip():
        raise TaskServiceError("Task title is required.")
    _validate_choice(completion_mode, CompletionMode.VALUES, "completion_mode")
    if completion_mode == CompletionMode.RULE_DATA:
        raise TaskServiceError("Manual tasks cannot use RULE_DATA completion mode.")
    if completion_mode == CompletionMode.MANUAL_REQUIRED_INPUT:
        _validate_required_input_schema(completion_schema)

    status = initial_status or TaskStatus.ACTION
    _validate_choice(status, (TaskStatus.ACTION, TaskStatus.UPCOMING), "initial_status")
    if activation_at and activation_at > now:
        status = TaskStatus.UPCOMING

    task = OrderTask(
        pi_id=pi_id,
        scope=TaskScope.ORDER,
        task_code="MANUAL_GENERAL",
        title=title.strip(),
        description=description,
        source=TaskSource.MANUAL,
        status=status,
        health=TaskHealth.NORMAL,
        completion_mode=completion_mode,
        completion_schema=completion_schema,
        context_payload=context_payload,
        priority=int(priority),
        activation_at=activation_at,
        due_at=due_at,
        created_by_id=actor_id,
        created_at=now,
        updated_at=now,
    )
    task.health = _calculated_health(task, now)
    db.session.add(task)
    _append_activity(
        task,
        ActivityEvent.CREATED,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        to_status=task.status,
        to_health=task.health,
        created_at=now,
    )
    db.session.flush()
    return task


def create_auto_task(
    *,
    pi_id,
    task_code,
    title,
    rule_key,
    rule_version,
    instance_key,
    dedupe_key,
    status,
    health,
    completion_mode,
    description=None,
    completion_schema=None,
    context_payload=None,
    activation_at=None,
    waiting_on=None,
    resolution_code=None,
    completion_payload=None,
    completed_at=None,
    now=None,
):
    """Create one engine-owned AUTO task and its initial audit event."""
    now = _now(now)
    if db.session.get(PI, pi_id) is None:
        raise TaskServiceError("PI does not exist.")
    _validate_choice(status, TaskStatus.VALUES, "status")
    _validate_choice(health, TaskHealth.VALUES, "health")
    _validate_choice(completion_mode, CompletionMode.VALUES, "completion_mode")
    task = OrderTask(
        pi_id=pi_id,
        scope=TaskScope.ORDER,
        task_code=task_code,
        title=title,
        description=description,
        source=TaskSource.AUTO,
        status=status,
        health=health,
        completion_mode=completion_mode,
        completion_schema=completion_schema,
        context_payload=context_payload,
        priority=100,
        activation_at=activation_at,
        waiting_on=waiting_on,
        waiting_since=now if status == TaskStatus.WAITING else None,
        resolution_code=resolution_code,
        completed_at=completed_at if status == TaskStatus.DONE else None,
        rule_key=rule_key,
        rule_version=rule_version,
        instance_key=instance_key,
        dedupe_key=dedupe_key,
        last_evaluated_at=now,
        created_at=now,
        updated_at=now,
    )
    # Most LEGACY_DONE tasks have an unknown completion time. A rule may provide a
    # verified business timestamp (for example freight_paid_at), but never the
    # reconcile/import time as a substitute.
    event_type = ActivityEvent.COMPLETED if status == TaskStatus.DONE else ActivityEvent.CREATED
    db.session.add(task)
    _append_activity(
        task,
        event_type,
        actor_type=ActorType.SYSTEM,
        to_status=status,
        to_health=health,
        reason_code=resolution_code,
        waiting_on=waiting_on,
        note=(
            "历史订单字段显示已完成，实际完成时间未知"
            if resolution_code == "LEGACY_DONE" and completed_at is None
            else (
                "历史订单字段显示已完成，完成时间来自现有业务事实"
                if resolution_code == "LEGACY_DONE"
                else "AUTO task created by rule reconcile"
            )
        ),
        payload=completion_payload,
        created_at=now,
    )
    db.session.flush()
    return task


def update_auto_task(
    task_or_id,
    *,
    status,
    health,
    title,
    description,
    completion_mode,
    completion_schema,
    context_payload,
    activation_at,
    waiting_on,
    rule_version,
    reason_code="RULE_RECONCILED",
    now=None,
):
    task = _require_task(task_or_id)
    if task.source != TaskSource.AUTO or task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
        raise InvalidTransitionError("Only active AUTO tasks can be reconciled.")
    now = _now(now)
    old_status, old_health = task.status, task.health
    task.status = status
    task.health = health
    task.title = title
    task.description = description
    task.completion_mode = completion_mode
    task.completion_schema = completion_schema
    task.context_payload = context_payload
    task.activation_at = activation_at
    if task.status == TaskStatus.WAITING and waiting_on is not None:
        if old_status != TaskStatus.WAITING:
            task.waiting_since = now
        task.waiting_on = waiting_on
    elif task.status != TaskStatus.WAITING:
        task.waiting_on = None
        task.waiting_since = None
    task.rule_version = rule_version
    task.last_evaluated_at = now
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.STATUS_CHANGED,
        actor_type=ActorType.SYSTEM,
        from_status=old_status,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        reason_code=reason_code,
        waiting_on=task.waiting_on,
        created_at=now,
    )
    db.session.flush()
    return task


def cancel_auto_task(task_or_id, *, reason_code, now=None):
    task = _require_task(task_or_id)
    if task.source != TaskSource.AUTO or task.status not in TaskStatus.ACTIVE:
        raise InvalidTransitionError("Only active AUTO tasks can be cancelled by the engine.")
    now = _now(now)
    old_status, old_health = task.status, task.health
    task.status = TaskStatus.CANCELLED
    task.health = TaskHealth.NORMAL
    task.cancelled_at = now
    task.last_evaluated_at = now
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.CANCELLED,
        actor_type=ActorType.SYSTEM,
        from_status=old_status,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        reason_code=reason_code,
        created_at=now,
    )
    db.session.flush()
    return task


def reactivate_auto_task(
    task_or_id,
    *,
    status,
    health,
    title,
    description,
    completion_mode,
    completion_schema,
    context_payload,
    activation_at,
    waiting_on,
    rule_version,
    now=None,
):
    task = _require_task(task_or_id)
    if task.source != TaskSource.AUTO or task.status != TaskStatus.CANCELLED:
        raise InvalidTransitionError("Only CANCELLED AUTO tasks can be reactivated by the engine.")
    now = _now(now)
    old_health = task.health
    task.status = status
    task.health = health
    task.title = title
    task.description = description
    task.completion_mode = completion_mode
    task.completion_schema = completion_schema
    task.context_payload = context_payload
    task.activation_at = activation_at
    task.waiting_on = waiting_on if status == TaskStatus.WAITING else None
    task.waiting_since = now if status == TaskStatus.WAITING else None
    task.rule_version = rule_version
    task.cancelled_at = None
    task.last_evaluated_at = now
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.REACTIVATED,
        actor_type=ActorType.SYSTEM,
        from_status=TaskStatus.CANCELLED,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        reason_code="REQUIREMENT_RESTORED",
        waiting_on=task.waiting_on,
        created_at=now,
    )
    db.session.flush()
    return task


def reactivate_rule_data_task(
    task_or_id,
    *,
    status,
    health,
    title,
    description,
    completion_mode,
    completion_schema,
    context_payload,
    activation_at,
    waiting_on,
    rule_version,
    now=None,
):
    """System-only reactivation when a previously resolved data fact becomes invalid."""
    task = _require_task(task_or_id)
    if (
        task.source != TaskSource.AUTO
        or task.completion_mode != CompletionMode.RULE_DATA
        or task.status != TaskStatus.DONE
    ):
        raise InvalidTransitionError("Only resolved AUTO RULE_DATA tasks can be reactivated.")
    now = _now(now)
    old_health = task.health
    task.status = status
    task.health = health
    task.title = title
    task.description = description
    task.completion_mode = completion_mode
    task.completion_schema = completion_schema
    task.context_payload = context_payload
    task.activation_at = activation_at
    task.waiting_on = waiting_on if status == TaskStatus.WAITING else None
    task.waiting_since = now if status == TaskStatus.WAITING else None
    task.rule_version = rule_version
    task.completed_at = None
    task.completed_by_id = None
    task.resolution_code = None
    task.last_evaluated_at = now
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.REACTIVATED,
        actor_type=ActorType.SYSTEM,
        from_status=TaskStatus.DONE,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        reason_code="RULE_REACTIVATED",
        waiting_on=task.waiting_on,
        note="Previously resolved order data is incomplete again.",
        created_at=now,
    )
    db.session.flush()
    return task


def reactivate_completed_auto_task(
    task_or_id,
    *,
    status,
    health,
    title,
    description,
    completion_mode,
    completion_schema,
    context_payload,
    activation_at,
    waiting_on,
    rule_version,
    reason_code,
    now=None,
):
    """System reactivation when a completed manual AUTO task's fact changed."""
    task = _require_task(task_or_id)
    if task.source != TaskSource.AUTO or task.status != TaskStatus.DONE:
        raise InvalidTransitionError("Only completed AUTO tasks can be rule-reactivated.")
    now = _now(now)
    old_health = task.health
    task.status = status
    task.health = health
    task.title = title
    task.description = description
    task.completion_mode = completion_mode
    task.completion_schema = completion_schema
    task.context_payload = context_payload
    task.activation_at = activation_at
    task.waiting_on = waiting_on if status == TaskStatus.WAITING else None
    task.waiting_since = now if status == TaskStatus.WAITING else None
    task.completed_at = None
    task.completed_by_id = None
    task.resolution_code = None
    task.rule_version = rule_version
    task.last_evaluated_at = now
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.REACTIVATED,
        actor_type=ActorType.SYSTEM,
        from_status=TaskStatus.DONE,
        to_status=status,
        from_health=old_health,
        to_health=health,
        reason_code=reason_code,
        note="Confirmed business fact changed; confirmation is required again.",
        created_at=now,
    )
    db.session.flush()
    return task


def refresh_completed_auto_task_context(task_or_id, *, context_payload, reason_code, now=None):
    """Append-only audit update for warnings on an already completed AUTO task."""
    task = _require_task(task_or_id)
    if task.source != TaskSource.AUTO or task.status != TaskStatus.DONE:
        raise InvalidTransitionError("Only completed AUTO task context can be refreshed.")
    now = _now(now)
    task.context_payload = context_payload
    task.last_evaluated_at = now
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.NOTE,
        actor_type=ActorType.SYSTEM,
        from_status=TaskStatus.DONE,
        to_status=TaskStatus.DONE,
        from_health=task.health,
        to_health=task.health,
        reason_code=reason_code,
        note="Completed task context refreshed after a downstream fact changed.",
        payload={"warnings": context_payload.get("warnings", []) if context_payload else []},
        created_at=now,
    )
    db.session.flush()
    return task


def legacy_complete_auto_task(task_or_id, *, context_payload=None, completion_payload=None, now=None):
    task = _require_task(task_or_id)
    if task.source != TaskSource.AUTO or task.status == TaskStatus.DONE:
        raise InvalidTransitionError("Only unfinished AUTO tasks can import legacy completion.")
    now = _now(now)
    old_status, old_health = task.status, task.health
    task.status = TaskStatus.DONE
    task.health = TaskHealth.NORMAL
    task.completed_at = None
    task.completed_by_id = None
    task.resolution_code = "LEGACY_DONE"
    task.cancelled_at = None
    task.context_payload = context_payload
    task.last_evaluated_at = now
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.COMPLETED,
        actor_type=ActorType.SYSTEM,
        from_status=old_status,
        to_status=TaskStatus.DONE,
        from_health=old_health,
        to_health=TaskHealth.NORMAL,
        reason_code="LEGACY_DONE",
        note="历史订单字段显示已完成，实际完成时间未知",
        payload=completion_payload,
        created_at=now,
    )
    db.session.flush()
    return task


def move_to_waiting(task_or_id, *, actor_id, waiting_on, next_follow_up_at=None, note=None, now=None):
    task = _require_task(task_or_id)
    _require_actor(actor_id)
    _validate_choice(waiting_on, WaitingOn.VALUES, "waiting_on")
    if task.status != TaskStatus.ACTION:
        raise InvalidTransitionError("Only ACTION tasks can move to WAITING.")

    now = _now(now)
    old_status, old_health = task.status, task.health
    task.status = TaskStatus.WAITING
    task.waiting_on = waiting_on
    task.waiting_since = now
    task.next_follow_up_at = next_follow_up_at
    task.health = _calculated_health(task, now)
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.WAITING_STARTED,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        from_status=old_status,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        waiting_on=waiting_on,
        next_follow_up_at=next_follow_up_at,
        note=note,
        created_at=now,
    )
    db.session.flush()
    return task


def add_follow_up(
    task_or_id,
    *,
    actor_id,
    note,
    next_follow_up_at=None,
    waiting_on=None,
    continue_waiting=True,
    now=None,
):
    task = _require_task(task_or_id)
    _require_actor(actor_id)
    if task.status not in (TaskStatus.ACTION, TaskStatus.WAITING):
        raise InvalidTransitionError("Follow-up requires an ACTION or WAITING task.")
    if not note or not note.strip():
        raise TaskServiceError("Follow-up note is required.")
    if waiting_on is not None:
        _validate_choice(waiting_on, WaitingOn.VALUES, "waiting_on")

    now = _now(now)
    old_status, old_health = task.status, task.health
    effective_waiting_on = waiting_on or task.waiting_on
    if continue_waiting and effective_waiting_on is None:
        raise TaskServiceError("Waiting On is required when follow-up continues waiting.")
    task.waiting_on = effective_waiting_on if continue_waiting else None
    task.waiting_since = now if continue_waiting else None
    task.next_follow_up_at = next_follow_up_at
    task.status = TaskStatus.WAITING if continue_waiting else TaskStatus.ACTION
    task.health = _calculated_health(task, now)
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.FOLLOW_UP,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        from_status=old_status,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        waiting_on=effective_waiting_on,
        next_follow_up_at=next_follow_up_at,
        note=note.strip(),
        created_at=now,
    )
    db.session.flush()
    return task


def add_note(task_or_id, *, actor_id, note, now=None):
    task = _require_task(task_or_id)
    _require_actor(actor_id)
    if not note or not note.strip():
        raise TaskServiceError("Note is required.")
    now = _now(now)
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.NOTE,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        from_status=task.status,
        to_status=task.status,
        from_health=task.health,
        to_health=task.health,
        note=note.strip(),
        created_at=now,
    )
    db.session.flush()
    return task


def _validate_required_input_schema(schema):
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list) or not any(
        isinstance(field, dict) and field.get("key") and field.get("required") is True
        for field in fields
    ):
        raise TaskServiceError("MANUAL_REQUIRED_INPUT needs at least one required completion field.")


def _validate_completion_payload(task, payload):
    if task.completion_mode != CompletionMode.MANUAL_REQUIRED_INPUT:
        return
    fields = task.completion_schema.get("fields", []) if isinstance(task.completion_schema, dict) else []
    missing = []
    for field in fields:
        if field.get("required") is True:
            value = (payload or {}).get(field.get("key"))
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field.get("label") or field.get("key"))
    if missing:
        raise CompletionValidationError(f"Missing required completion fields: {', '.join(missing)}")


def mark_done(task_or_id, *, actor_id, note=None, payload=None, now=None):
    task = _require_task(task_or_id)
    _require_actor(actor_id)
    if task.completion_mode == CompletionMode.RULE_DATA:
        raise InvalidTransitionError(
            "This task is resolved by order data and cannot be completed manually."
        )
    if task.status not in (TaskStatus.ACTION, TaskStatus.WAITING):
        raise InvalidTransitionError("Only ACTION or WAITING tasks can be completed.")
    _validate_completion_payload(task, payload)

    now = _now(now)
    old_status, old_health = task.status, task.health
    activity_waiting_on = task.waiting_on
    activity_follow_up = task.next_follow_up_at
    task.status = TaskStatus.DONE
    task.health = TaskHealth.NORMAL
    task.completed_at = now
    task.completed_by_id = actor_id
    task.resolution_code = "MANUAL_DONE"
    task.cancelled_at = None
    task.waiting_on = None
    task.waiting_since = None
    task.next_follow_up_at = None
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.COMPLETED,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        from_status=old_status,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        waiting_on=activity_waiting_on,
        next_follow_up_at=activity_follow_up,
        note=note,
        payload=payload,
        created_at=now,
    )
    db.session.flush()
    return task


def resolve_from_data(
    task_or_id,
    *,
    reason_code,
    note=None,
    payload=None,
    context_payload=None,
    now=None,
):
    """Internal Phase 2 hook; no business rule invokes it in Phase 1."""
    task = _require_task(task_or_id)
    if task.completion_mode != CompletionMode.RULE_DATA:
        raise InvalidTransitionError("Only RULE_DATA tasks can be resolved from order data.")
    if task.status not in TaskStatus.ACTIVE:
        raise InvalidTransitionError("Only active tasks can be auto-resolved.")
    if not reason_code:
        raise TaskServiceError("System resolution reason is required.")

    now = _now(now)
    old_status, old_health = task.status, task.health
    task.status = TaskStatus.DONE
    task.health = TaskHealth.NORMAL
    task.completed_at = now
    task.completed_by_id = None
    task.resolution_code = reason_code
    task.context_payload = context_payload
    task.waiting_on = None
    task.waiting_since = None
    task.next_follow_up_at = None
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.AUTO_RESOLVED,
        actor_type=ActorType.SYSTEM,
        from_status=old_status,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        reason_code=reason_code,
        note=note,
        payload=payload,
        created_at=now,
    )
    db.session.flush()
    return task


def reopen_task(task_or_id, *, actor_id, reason, now=None):
    task = _require_task(task_or_id)
    _require_actor(actor_id)
    if task.completion_mode == CompletionMode.RULE_DATA:
        raise InvalidTransitionError("RULE_DATA tasks cannot be reopened manually.")
    if task.status != TaskStatus.DONE:
        raise InvalidTransitionError("Only DONE tasks can be reopened.")
    if not reason or not reason.strip():
        raise TaskServiceError("Reopen reason is required.")

    now = _now(now)
    old_health = task.health
    task.status = TaskStatus.ACTION
    task.health = TaskHealth.NORMAL
    task.completed_at = None
    task.completed_by_id = None
    task.resolution_code = None
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.REOPENED,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        from_status=TaskStatus.DONE,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        note=reason.strip(),
        created_at=now,
    )
    db.session.flush()
    return task


def cancel_task(task_or_id, *, actor_id, reason, now=None):
    task = _require_task(task_or_id)
    _require_actor(actor_id)
    if task.source != TaskSource.MANUAL:
        raise InvalidTransitionError("AUTO task cancellation is reserved for the rule engine.")
    if task.status not in TaskStatus.ACTIVE:
        raise InvalidTransitionError("Only active tasks can be cancelled.")
    if not reason or not reason.strip():
        raise TaskServiceError("Cancellation reason is required.")

    now = _now(now)
    old_status, old_health = task.status, task.health
    task.status = TaskStatus.CANCELLED
    task.health = TaskHealth.NORMAL
    task.cancelled_at = now
    task.waiting_on = None
    task.waiting_since = None
    task.next_follow_up_at = None
    _touch(task, now)
    _append_activity(
        task,
        ActivityEvent.CANCELLED,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        from_status=old_status,
        to_status=task.status,
        from_health=old_health,
        to_health=task.health,
        note=reason.strip(),
        created_at=now,
    )
    db.session.flush()
    return task


def reconcile_task_timing(*, tasks=None, now=None):
    """Idempotently activate due tasks and persist their single-source health."""
    now = _now(now)
    if tasks is None:
        tasks = OrderTask.query.filter(OrderTask.status.in_(TaskStatus.ACTIVE)).all()
    changed = []
    for task in tasks:
        if task.status not in TaskStatus.ACTIVE:
            continue
        old_status, old_health = task.status, task.health
        projected_status, projected_health, reason_code = project_task_timing(task, now)
        event_type = (
            ActivityEvent.REACTIVATED if projected_status != old_status else None
        )
        task.status = projected_status
        task.health = projected_health
        if event_type or task.health != old_health:
            _touch(task, now)
            _append_activity(
                task,
                event_type or ActivityEvent.STATUS_CHANGED,
                actor_type=ActorType.SYSTEM,
                from_status=old_status,
                to_status=task.status,
                from_health=old_health,
                to_health=task.health,
                reason_code=reason_code or "HEALTH_RECALCULATED",
                waiting_on=task.waiting_on,
                next_follow_up_at=task.next_follow_up_at,
                created_at=now,
            )
            changed.append(task)
    if changed:
        db.session.flush()
    return changed
