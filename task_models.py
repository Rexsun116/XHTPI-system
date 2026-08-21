"""SQLAlchemy models for the Phase 1 task foundation."""

from datetime import datetime, timezone

from app import db
from sqlalchemy import event
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


def utc_now():
    """Return a timezone-neutral UTC timestamp for SQLite compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _values(values):
    return ", ".join(f"'{value}'" for value in values)


class OrderTask(db.Model):
    __tablename__ = "order_task"
    __table_args__ = (
        db.CheckConstraint(f"scope IN ({_values(TaskScope.VALUES)})", name="ck_order_task_scope"),
        db.CheckConstraint(f"source IN ({_values(TaskSource.VALUES)})", name="ck_order_task_source"),
        db.CheckConstraint(f"status IN ({_values(TaskStatus.VALUES)})", name="ck_order_task_status"),
        db.CheckConstraint(f"health IN ({_values(TaskHealth.VALUES)})", name="ck_order_task_health"),
        db.CheckConstraint(
            f"completion_mode IN ({_values(CompletionMode.VALUES)})",
            name="ck_order_task_completion_mode",
        ),
        db.CheckConstraint(
            f"waiting_on IS NULL OR waiting_on IN ({_values(WaitingOn.VALUES)})",
            name="ck_order_task_waiting_on",
        ),
        db.UniqueConstraint("dedupe_key", name="uq_order_task_dedupe_key"),
        db.Index("ix_order_task_pi_status_health", "pi_id", "status", "health"),
        db.Index("ix_order_task_status_due_at", "status", "due_at"),
        db.Index("ix_order_task_status_next_follow_up_at", "status", "next_follow_up_at"),
        db.Index("ix_order_task_rule_instance", "rule_key", "instance_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    pi_id = db.Column(db.Integer, db.ForeignKey("pi.id"), nullable=False)
    scope = db.Column(db.String(20), nullable=False, default=TaskScope.ORDER)
    task_code = db.Column(db.String(100), nullable=False, default="MANUAL_GENERAL")
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    source = db.Column(db.String(20), nullable=False, default=TaskSource.MANUAL)
    status = db.Column(db.String(20), nullable=False, default=TaskStatus.ACTION)
    health = db.Column(db.String(20), nullable=False, default=TaskHealth.NORMAL)
    completion_mode = db.Column(db.String(30), nullable=False, default=CompletionMode.MANUAL)
    completion_schema = db.Column(db.JSON)
    context_payload = db.Column(db.JSON)
    priority = db.Column(db.Integer, nullable=False, default=100)
    activation_at = db.Column(db.DateTime)
    due_at = db.Column(db.DateTime)
    waiting_on = db.Column(db.String(30))
    waiting_since = db.Column(db.DateTime)
    next_follow_up_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    resolution_code = db.Column(db.String(100))
    cancelled_at = db.Column(db.DateTime)
    rule_key = db.Column(db.String(100))
    rule_version = db.Column(db.Integer)
    instance_key = db.Column(db.String(200))
    dedupe_key = db.Column(db.String(255))
    last_evaluated_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    pi = db.relationship("PI", backref=db.backref("tasks", lazy="dynamic"))
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    activities = db.relationship(
        "TaskActivity",
        back_populates="task",
        order_by="TaskActivity.created_at, TaskActivity.id",
        lazy="select",
    )


class TaskActivity(db.Model):
    __tablename__ = "task_activity"
    __table_args__ = (
        db.CheckConstraint(
            f"event_type IN ({_values(ActivityEvent.VALUES)})",
            name="ck_task_activity_event_type",
        ),
        db.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_values(TaskStatus.VALUES)})",
            name="ck_task_activity_from_status",
        ),
        db.CheckConstraint(
            f"to_status IS NULL OR to_status IN ({_values(TaskStatus.VALUES)})",
            name="ck_task_activity_to_status",
        ),
        db.CheckConstraint(
            f"from_health IS NULL OR from_health IN ({_values(TaskHealth.VALUES)})",
            name="ck_task_activity_from_health",
        ),
        db.CheckConstraint(
            f"to_health IS NULL OR to_health IN ({_values(TaskHealth.VALUES)})",
            name="ck_task_activity_to_health",
        ),
        db.CheckConstraint(
            f"actor_type IN ({_values(ActorType.VALUES)})",
            name="ck_task_activity_actor_type",
        ),
        db.CheckConstraint(
            f"waiting_on IS NULL OR waiting_on IN ({_values(WaitingOn.VALUES)})",
            name="ck_task_activity_waiting_on",
        ),
        db.Index("ix_task_activity_task_created_at", "task_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("order_task.id"), nullable=False)
    event_type = db.Column(db.String(30), nullable=False)
    from_status = db.Column(db.String(20))
    to_status = db.Column(db.String(20))
    from_health = db.Column(db.String(20))
    to_health = db.Column(db.String(20))
    actor_type = db.Column(db.String(20), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reason_code = db.Column(db.String(100))
    note = db.Column(db.Text)
    waiting_on = db.Column(db.String(30))
    next_follow_up_at = db.Column(db.DateTime)
    payload = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    task = db.relationship("OrderTask", back_populates="activities")
    actor = db.relationship("User", foreign_keys=[actor_id])


@event.listens_for(TaskActivity, "before_update")
def prevent_task_activity_update(_mapper, _connection, _target):
    raise ValueError("TaskActivity is append-only and cannot be updated.")


@event.listens_for(TaskActivity, "before_delete")
def prevent_task_activity_delete(_mapper, _connection, _target):
    raise ValueError("TaskActivity is append-only and cannot be deleted.")
