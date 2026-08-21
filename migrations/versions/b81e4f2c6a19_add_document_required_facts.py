"""add document required facts

Revision ID: b81e4f2c6a19
Revises: 9f3c1b2a7d40
Create Date: 2026-08-21 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b81e4f2c6a19"
down_revision = "9f3c1b2a7d40"
branch_labels = None
depends_on = None


DOCUMENT_FACT_COLUMNS = (
    "coc_required",
    "coa_required",
    "original_bl_required",
    "obd_electronic_required",
    "insurance_original_required",
    "insurance_electronic_required",
    "original_documents_mail_required",
    "telex_release_required",
)


def upgrade():
    for column_name in DOCUMENT_FACT_COLUMNS:
        op.add_column("pi", sa.Column(column_name, sa.Boolean(), nullable=True))


def downgrade():
    for column_name in reversed(DOCUMENT_FACT_COLUMNS):
        op.drop_column("pi", column_name)
