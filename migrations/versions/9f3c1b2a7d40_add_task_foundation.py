"""add task foundation

Revision ID: 9f3c1b2a7d40
Revises: 06df15a0fb70
Create Date: 2026-08-21 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "9f3c1b2a7d40"
down_revision = "06df15a0fb70"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_task",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pi_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("task_code", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("health", sa.String(length=20), nullable=False),
        sa.Column("completion_mode", sa.String(length=30), nullable=False),
        sa.Column("completion_schema", sa.JSON(), nullable=True),
        sa.Column("context_payload", sa.JSON(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("activation_at", sa.DateTime(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("waiting_on", sa.String(length=30), nullable=True),
        sa.Column("waiting_since", sa.DateTime(), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by_id", sa.Integer(), nullable=True),
        sa.Column("resolution_code", sa.String(length=100), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("rule_key", sa.String(length=100), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("instance_key", sa.String(length=200), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "completion_mode IN ('MANUAL', 'RULE_DATA', 'MANUAL_REQUIRED_INPUT')",
            name="ck_order_task_completion_mode",
        ),
        sa.CheckConstraint(
            "health IN ('NORMAL', 'OVERDUE', 'EXCEPTION')",
            name="ck_order_task_health",
        ),
        sa.CheckConstraint(
            "scope IN ('ORDER', 'SHIPMENT')",
            name="ck_order_task_scope",
        ),
        sa.CheckConstraint(
            "source IN ('AUTO', 'MANUAL')",
            name="ck_order_task_source",
        ),
        sa.CheckConstraint(
            "status IN ('UPCOMING', 'ACTION', 'WAITING', 'DONE', 'CANCELLED')",
            name="ck_order_task_status",
        ),
        sa.CheckConstraint(
            "waiting_on IS NULL OR waiting_on IN "
            "('CUSTOMER', 'FACTORY', 'FREIGHT_FORWARDER', 'BANK', 'INTERNAL', 'OTHER')",
            name="ck_order_task_waiting_on",
        ),
        sa.ForeignKeyConstraint(["completed_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["pi_id"], ["pi.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_order_task_dedupe_key"),
    )
    op.create_index(
        "ix_order_task_pi_status_health",
        "order_task",
        ["pi_id", "status", "health"],
        unique=False,
    )
    op.create_index(
        "ix_order_task_rule_instance",
        "order_task",
        ["rule_key", "instance_key"],
        unique=False,
    )
    op.create_index(
        "ix_order_task_status_due_at",
        "order_task",
        ["status", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_order_task_status_next_follow_up_at",
        "order_task",
        ["status", "next_follow_up_at"],
        unique=False,
    )

    op.create_table(
        "task_activity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("from_health", sa.String(length=20), nullable=True),
        sa.Column("to_health", sa.String(length=20), nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("waiting_on", sa.String(length=30), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('USER', 'SYSTEM')",
            name="ck_task_activity_actor_type",
        ),
        sa.CheckConstraint(
            "event_type IN ('CREATED', 'STATUS_CHANGED', 'FOLLOW_UP', 'WAITING_STARTED', "
            "'COMPLETED', 'AUTO_RESOLVED', 'REOPENED', 'NOTE', 'CANCELLED', 'REACTIVATED')",
            name="ck_task_activity_event_type",
        ),
        sa.CheckConstraint(
            "from_health IS NULL OR from_health IN ('NORMAL', 'OVERDUE', 'EXCEPTION')",
            name="ck_task_activity_from_health",
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('UPCOMING', 'ACTION', 'WAITING', 'DONE', 'CANCELLED')",
            name="ck_task_activity_from_status",
        ),
        sa.CheckConstraint(
            "to_health IS NULL OR to_health IN ('NORMAL', 'OVERDUE', 'EXCEPTION')",
            name="ck_task_activity_to_health",
        ),
        sa.CheckConstraint(
            "to_status IS NULL OR to_status IN "
            "('UPCOMING', 'ACTION', 'WAITING', 'DONE', 'CANCELLED')",
            name="ck_task_activity_to_status",
        ),
        sa.CheckConstraint(
            "waiting_on IS NULL OR waiting_on IN "
            "('CUSTOMER', 'FACTORY', 'FREIGHT_FORWARDER', 'BANK', 'INTERNAL', 'OTHER')",
            name="ck_task_activity_waiting_on",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["order_task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_activity_task_created_at",
        "task_activity",
        ["task_id", "created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_task_activity_task_created_at", table_name="task_activity")
    op.drop_table("task_activity")
    op.drop_index("ix_order_task_status_next_follow_up_at", table_name="order_task")
    op.drop_index("ix_order_task_status_due_at", table_name="order_task")
    op.drop_index("ix_order_task_rule_instance", table_name="order_task")
    op.drop_index("ix_order_task_pi_status_health", table_name="order_task")
    op.drop_table("order_task")
