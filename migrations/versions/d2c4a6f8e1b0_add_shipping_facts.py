"""add shipping facts

Revision ID: d2c4a6f8e1b0
Revises: b81e4f2c6a19
Create Date: 2026-08-21 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "d2c4a6f8e1b0"
down_revision = "b81e4f2c6a19"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pi", sa.Column("container_loading_at", sa.DateTime(), nullable=True))
    op.add_column("pi", sa.Column("driver_name", sa.String(length=100), nullable=True))
    op.add_column("pi", sa.Column("driver_phone", sa.String(length=50), nullable=True))
    op.add_column("pi", sa.Column("vehicle_number", sa.String(length=50), nullable=True))


def downgrade():
    op.drop_column("pi", "vehicle_number")
    op.drop_column("pi", "driver_phone")
    op.drop_column("pi", "driver_name")
    op.drop_column("pi", "container_loading_at")
