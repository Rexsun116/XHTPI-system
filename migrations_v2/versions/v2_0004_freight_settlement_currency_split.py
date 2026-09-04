"""Split freight invoice and payment facts by currency.

Revision ID: v2_0004
Revises: v2_0003
"""

from alembic import op
import sqlalchemy as sa


revision = "v2_0004"
down_revision = "v2_0003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("freight_settlement") as batch:
        batch.add_column(sa.Column("usd_invoice_issued", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("usd_invoice_issued_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("usd_payment_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("usd_paid_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("cny_invoice_issued", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("cny_invoice_issued_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("cny_payment_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("cny_paid_at", sa.DateTime(), nullable=True))

    # A shared legacy fact is unambiguous only if exactly one currency is required.
    op.execute("""
        UPDATE freight_settlement
        SET usd_invoice_issued = invoice_issued,
            usd_invoice_issued_at = invoice_issued_at,
            usd_payment_status = payment_status,
            usd_paid_at = paid_at
        WHERE usd_bill_required = 1
          AND COALESCE(cny_bill_required, 0) != 1
    """)
    op.execute("""
        UPDATE freight_settlement
        SET cny_invoice_issued = invoice_issued,
            cny_invoice_issued_at = invoice_issued_at,
            cny_payment_status = payment_status,
            cny_paid_at = paid_at
        WHERE cny_bill_required = 1
          AND COALESCE(usd_bill_required, 0) != 1
    """)


def downgrade():
    with op.batch_alter_table("freight_settlement") as batch:
        batch.drop_column("cny_paid_at")
        batch.drop_column("cny_payment_status")
        batch.drop_column("cny_invoice_issued_at")
        batch.drop_column("cny_invoice_issued")
        batch.drop_column("usd_paid_at")
        batch.drop_column("usd_payment_status")
        batch.drop_column("usd_invoice_issued_at")
        batch.drop_column("usd_invoice_issued")
