"""booking facts: loading period, notify snapshot, item HS code

Revision ID: v2_0002
Revises: v2_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "v2_0002"
down_revision = "v2_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only. No V1 lineage/table is touched.
    op.add_column("pi", sa.Column("container_loading_date", sa.Date(), nullable=True))
    op.add_column("pi", sa.Column("container_loading_period", sa.String(length=12), nullable=True))
    op.add_column("pi", sa.Column("notify_party_same_as_consignee", sa.Boolean(), nullable=True))
    op.add_column("pi", sa.Column("notify_party_name_snapshot", sa.String(length=150), nullable=True))
    op.add_column("pi", sa.Column("notify_party_address_snapshot", sa.Text(), nullable=True))
    op.add_column("pi", sa.Column("notify_party_tax_code_snapshot", sa.String(length=100), nullable=True))
    op.add_column("product", sa.Column("hs_code", sa.String(length=32), nullable=True))
    op.add_column("pi_item", sa.Column("product_hs_code_snapshot", sa.String(length=32), nullable=True))


def downgrade() -> None:
    # Exercised only against disposable databases; never run on local trial data.
    op.drop_column("pi_item", "product_hs_code_snapshot")
    op.drop_column("product", "hs_code")
    op.drop_column("pi", "notify_party_tax_code_snapshot")
    op.drop_column("pi", "notify_party_address_snapshot")
    op.drop_column("pi", "notify_party_name_snapshot")
    op.drop_column("pi", "notify_party_same_as_consignee")
    op.drop_column("pi", "container_loading_period")
    op.drop_column("pi", "container_loading_date")
