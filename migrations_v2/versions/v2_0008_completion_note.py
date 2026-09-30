"""Add an optional final completion note to the order record.

Revision ID: v2_0008
Revises: v2_0007
"""
from alembic import op
import sqlalchemy as sa


revision = "v2_0008"
down_revision = "v2_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pi", sa.Column("completion_note", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("pi", "completion_note")
