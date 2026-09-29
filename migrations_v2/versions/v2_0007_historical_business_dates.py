"""Add explicit historical business dates without rewriting audit timestamps.

Revision ID: v2_0007
Revises: v2_0006
"""
from alembic import op
import sqlalchemy as sa
revision = "v2_0007"
down_revision = "v2_0006"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("pi", sa.Column("payment_email_sent_on", sa.Date(), nullable=True))
    op.add_column("order_freight_agreement", sa.Column("agreed_on", sa.Date(), nullable=True))
def downgrade():
    op.drop_column("order_freight_agreement", "agreed_on")
    op.drop_column("pi", "payment_email_sent_on")
