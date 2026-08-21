"""add payment facts

Revision ID: e7a1c9d4b620
Revises: d2c4a6f8e1b0
Create Date: 2026-08-21 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "e7a1c9d4b620"
down_revision = "d2c4a6f8e1b0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pi", sa.Column("currency", sa.String(length=10), nullable=True))
    op.add_column("pi", sa.Column("advance_payment_percent", sa.Numeric(5, 2), nullable=True))
    op.add_column("pi", sa.Column("advance_payment_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("pi", sa.Column("advance_received_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("pi", sa.Column("advance_received_at", sa.DateTime(), nullable=True))
    op.add_column("pi", sa.Column("balance_payment_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("pi", sa.Column("balance_received_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("pi", sa.Column("balance_received_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("pi", "balance_received_at")
    op.drop_column("pi", "balance_received_amount")
    op.drop_column("pi", "balance_payment_amount")
    op.drop_column("pi", "advance_received_at")
    op.drop_column("pi", "advance_received_amount")
    op.drop_column("pi", "advance_payment_amount")
    op.drop_column("pi", "advance_payment_percent")
    op.drop_column("pi", "currency")
