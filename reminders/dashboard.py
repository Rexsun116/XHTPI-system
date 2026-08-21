"""Read-only Dashboard query and grouping service."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import joinedload, selectinload

from reminders.enums import TaskHealth, TaskStatus
from reminders.presenter import present_task
from reminders.selector import select_next_action
from reminders.task_service import project_task_timing
from task_models import OrderTask, TaskActivity, utc_now


def _sort_key(item):
    task = item["task"]
    status = item["status"]
    relevant = (
        task.next_follow_up_at
        or task.due_at
        or task.activation_at
        or task.updated_at
        or task.created_at
    )
    return (task.priority, relevant or datetime.max, task.id)


def get_dashboard_tasks(*, now=None, upcoming_days=7, upcoming_limit=10, done_limit=10):
    """Return presented Task groups without reconciling rules or writing timing state."""
    now = now or utc_now()
    tasks = (
        OrderTask.query.options(
            joinedload(OrderTask.pi),
            selectinload(OrderTask.activities).joinedload(TaskActivity.actor),
        )
        .filter(OrderTask.status != TaskStatus.CANCELLED)
        .all()
    )

    projected = []
    tasks_by_pi = defaultdict(list)
    for task in tasks:
        status, health, timing_reason = project_task_timing(task, now)
        item = {
            "task": task,
            "status": status,
            "health": health,
            "timing_reason": timing_reason,
        }
        projected.append(item)
        tasks_by_pi[task.pi_id].append(task)

    exception_items = sorted(
        [item for item in projected if item["status"] in TaskStatus.ACTIVE and item["health"] == TaskHealth.EXCEPTION],
        key=_sort_key,
    )
    action_items = sorted(
        [item for item in projected if item["status"] == TaskStatus.ACTION and item["health"] != TaskHealth.EXCEPTION],
        key=lambda item: (0 if item["health"] == TaskHealth.OVERDUE else 1, *_sort_key(item)),
    )
    waiting_items = sorted(
        [item for item in projected if item["status"] == TaskStatus.WAITING and item["health"] != TaskHealth.EXCEPTION],
        key=_sort_key,
    )

    upcoming_cutoff = now + timedelta(days=upcoming_days)
    all_upcoming = sorted(
        [
            item
            for item in projected
            if item["status"] == TaskStatus.UPCOMING
            and (item["task"].activation_at is None or item["task"].activation_at <= upcoming_cutoff)
        ],
        key=_sort_key,
    )
    upcoming_items = all_upcoming[:upcoming_limit]
    done_items = sorted(
        [item for item in projected if item["status"] == TaskStatus.DONE],
        key=lambda item: (
            item["task"].completed_at or item["task"].updated_at or item["task"].created_at,
            item["task"].id,
        ),
        reverse=True,
    )[:done_limit]

    def present(items):
        return [
            present_task(
                item["task"],
                now=now,
                effective_status=item["status"],
                effective_health=item["health"],
            )
            for item in items
        ]

    next_actions = {}
    for pi_id, pi_tasks in tasks_by_pi.items():
        selected = select_next_action(pi_tasks, now=now, persist_timing=False)
        if selected:
            status, health, _reason = project_task_timing(selected.task, now)
            next_actions[pi_id] = present_task(
                selected.task,
                now=now,
                effective_status=status,
                effective_health=health,
            )

    return {
        "exception": present(exception_items),
        "action": present(action_items),
        "waiting": present(waiting_items),
        "upcoming": present(upcoming_items),
        "done": present(done_items),
        "upcoming_hidden_count": max(0, len(all_upcoming) - len(upcoming_items)),
        "counts": {
            "exception": len(exception_items),
            "action": len(action_items),
            "waiting": len(waiting_items),
            "upcoming": len(all_upcoming),
            "done": len(done_items),
        },
        "next_actions": next_actions,
        "read_only_timing_projection": True,
    }
