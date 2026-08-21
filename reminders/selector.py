"""Select the most important current task for an order."""

from dataclasses import dataclass
from datetime import datetime

from reminders.enums import TaskHealth, TaskStatus
from reminders.task_service import project_task_timing, reconcile_task_timing
from task_models import utc_now


@dataclass(frozen=True)
class NextActionResult:
    task: object
    display_priority: int
    reason: str
    relevant_at: datetime | None


def _relevant_at(task, status=None):
    status = status or task.status
    dates = []
    if task.due_at:
        dates.append(task.due_at)
    if status == TaskStatus.UPCOMING and task.activation_at:
        dates.append(task.activation_at)
    if task.next_follow_up_at:
        dates.append(task.next_follow_up_at)
    return min(dates) if dates else None


def _bucket(task, now, *, status=None, health=None):
    status = status or task.status
    health = health or task.health
    if health == TaskHealth.EXCEPTION:
        return 0, "EXCEPTION"
    if health == TaskHealth.OVERDUE and status == TaskStatus.ACTION:
        return 10, "OVERDUE_ACTION"
    if status == TaskStatus.ACTION:
        return 20, "ACTION_REQUIRED"
    if (
        status == TaskStatus.WAITING
        and task.next_follow_up_at
        and task.next_follow_up_at.date() == now.date()
    ):
        return 30, "FOLLOW_UP_DUE_TODAY"
    if status == TaskStatus.WAITING:
        return 40, "WAITING"
    if status == TaskStatus.UPCOMING:
        return 50, "UPCOMING"
    return 99, "INACTIVE"


def select_next_action(tasks, now=None, *, persist_timing=True):
    """Return the highest-ranked active task.

    Lower task.priority values rank first inside the same display bucket. The
    existing service behaviour remains the default; read-only consumers such as
    Dashboard use ``persist_timing=False`` and rank by the shared projection.
    """
    now = now or utc_now()
    candidates = list(tasks)
    if persist_timing:
        reconcile_task_timing(tasks=candidates, now=now)

    ranked = []
    for task in candidates:
        status, health, _timing_reason = (
            (task.status, task.health, None)
            if persist_timing
            else project_task_timing(task, now)
        )
        bucket, reason = _bucket(task, now, status=status, health=health)
        if bucket == 99:
            continue
        relevant_at = _relevant_at(task, status=status)
        ranked.append(
            (
                bucket,
                task.priority,
                relevant_at or datetime.max,
                task.id or 0,
                NextActionResult(task, bucket, reason, relevant_at),
            )
        )
    return min(ranked, key=lambda row: row[:4])[-1] if ranked else None
