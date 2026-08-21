"""add dual currency freight facts

Revision ID: f4b6d8e0a2c1
Revises: e7a1c9d4b620
Create Date: 2026-08-21 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "f4b6d8e0a2c1"
down_revision = "e7a1c9d4b620"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pi", sa.Column("freight_usd_bill_required", sa.Boolean(), nullable=True))
    op.add_column("pi", sa.Column("freight_usd_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("pi", sa.Column("freight_usd_confirmed", sa.Boolean(), nullable=True))
    op.add_column("pi", sa.Column("freight_cny_bill_required", sa.Boolean(), nullable=True))
    op.add_column("pi", sa.Column("freight_cny_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("pi", sa.Column("freight_cny_confirmed", sa.Boolean(), nullable=True))
    op.add_column("pi", sa.Column("freight_paid_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("pi", "freight_paid_at")
    op.drop_column("pi", "freight_cny_confirmed")
    op.drop_column("pi", "freight_cny_amount")
    op.drop_column("pi", "freight_cny_bill_required")
    op.drop_column("pi", "freight_usd_confirmed")
    op.drop_column("pi", "freight_usd_amount")
    op.drop_column("pi", "freight_usd_bill_required")
