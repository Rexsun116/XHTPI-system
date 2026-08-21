"""Minimal authenticated JSON endpoints for task mutations."""

from datetime import datetime, timezone
import hmac
import secrets

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import db
from reminders.enums import CompletionMode, TaskStatus
from reminders.freight import FREIGHT_MANUAL_TASK_CODES, complete_freight_manual_task
from reminders.engine import reconcile_order_tasks_for_pi
from reminders.task_service import (
    TaskServiceError,
    add_follow_up,
    add_note,
    cancel_task,
    create_manual_task,
    mark_done,
    move_to_waiting,
    reopen_task,
)
from task_models import OrderTask


reminders_bp = Blueprint("reminders", __name__, url_prefix="/tasks")


def ensure_task_csrf_token():
    token = session.get("_task_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_task_csrf_token"] = token
    return token


@reminders_bp.before_request
def protect_task_mutations():
    if request.method != "POST" or not current_user.is_authenticated:
        return None
    expected = session.get("_task_csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    return None


def _data():
    return request.get_json(silent=True) or request.form.to_dict()


def _datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _task_json(task):
    return {
        "id": task.id,
        "pi_id": task.pi_id,
        "title": task.title,
        "source": task.source,
        "status": task.status,
        "health": task.health,
        "completion_mode": task.completion_mode,
        "priority": task.priority,
        "waiting_on": task.waiting_on,
        "next_follow_up_at": task.next_follow_up_at.isoformat() if task.next_follow_up_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _commit(task, status_code=200, *, reconcile_rules=False):
    try:
        if reconcile_rules:
            db.session.flush()
            reconcile_order_tasks_for_pi(task.pi)
        db.session.commit()
        return jsonify({"task": _task_json(task)}), status_code
    except (TaskServiceError, IntegrityError, ValueError):
        raise
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Targeted reminder reconcile failed after Task mutation for task id=%s",
            task.id,
        )
        return jsonify(
            {"error": "Task 操作失败，Reminder 同步未完成；本次修改已回滚。"}
        ), 500


@reminders_bp.errorhandler(TaskServiceError)
def handle_task_service_error(error):
    db.session.rollback()
    return jsonify({"error": str(error)}), 400


@reminders_bp.errorhandler(IntegrityError)
def handle_integrity_error(_error):
    db.session.rollback()
    return jsonify({"error": "Task data conflicts with a database constraint."}), 409


@reminders_bp.errorhandler(ValueError)
def handle_value_error(error):
    db.session.rollback()
    return jsonify({"error": str(error)}), 400


@reminders_bp.post("")
@login_required
def create_task_route():
    data = _data()
    task = create_manual_task(
        pi_id=int(data["pi_id"]),
        title=data.get("title"),
        description=data.get("description"),
        priority=int(data.get("priority", 100)),
        due_at=_datetime(data.get("due_at")),
        activation_at=_datetime(data.get("activation_at")),
        initial_status=data.get("status", TaskStatus.ACTION),
        completion_mode=data.get("completion_mode", CompletionMode.MANUAL),
        completion_schema=data.get("completion_schema"),
        context_payload=data.get("context_payload"),
        actor_id=current_user.id,
    )
    return _commit(task, 201)


@reminders_bp.post("/<int:task_id>/done")
@login_required
def done_task_route(task_id):
    data = _data()
    existing = db.session.get(OrderTask, task_id)
    if existing is not None and existing.task_code in FREIGHT_MANUAL_TASK_CODES:
        task = complete_freight_manual_task(
            existing,
            actor_id=current_user.id,
            note=data.get("note"),
        )
    else:
        task = mark_done(
            task_id,
            actor_id=current_user.id,
            note=data.get("note"),
            payload=data.get("completion_payload") or data.get("payload"),
        )
    return _commit(task, reconcile_rules=True)


@reminders_bp.post("/<int:task_id>/waiting")
@login_required
def waiting_task_route(task_id):
    data = _data()
    task = move_to_waiting(
        task_id,
        actor_id=current_user.id,
        waiting_on=data.get("waiting_on"),
        next_follow_up_at=_datetime(data.get("next_follow_up_at")),
        note=data.get("note"),
    )
    return _commit(task)


@reminders_bp.post("/<int:task_id>/follow-up")
@login_required
def follow_up_task_route(task_id):
    data = _data()
    continue_waiting = data.get("continue_waiting", True)
    if isinstance(continue_waiting, str):
        continue_waiting = continue_waiting.lower() not in ("0", "false", "no")
    task = add_follow_up(
        task_id,
        actor_id=current_user.id,
        note=data.get("note"),
        next_follow_up_at=_datetime(data.get("next_follow_up_at")),
        waiting_on=data.get("waiting_on"),
        continue_waiting=continue_waiting,
    )
    return _commit(task)


@reminders_bp.post("/<int:task_id>/notes")
@login_required
def add_task_note_route(task_id):
    data = _data()
    return _commit(add_note(task_id, actor_id=current_user.id, note=data.get("note")))


@reminders_bp.post("/<int:task_id>/reopen")
@login_required
def reopen_task_route(task_id):
    data = _data()
    return _commit(
        reopen_task(task_id, actor_id=current_user.id, reason=data.get("reason")),
        reconcile_rules=True,
    )


@reminders_bp.post("/<int:task_id>/cancel")
@login_required
def cancel_task_route(task_id):
    data = _data()
    return _commit(cancel_task(task_id, actor_id=current_user.id, reason=data.get("reason")))
