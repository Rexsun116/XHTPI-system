"""Add the first qualifying bill-of-lading business receipt date.

Revision ID: v2_0009
Revises: v2_0008
"""
from alembic import op
import sqlalchemy as sa


revision = "v2_0009"
down_revision = "v2_0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pi", sa.Column("bill_of_lading_received_on", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("pi", "bill_of_lading_received_on")
