"""booking display units over canonical kilogram facts

Revision ID: v2_0003
Revises: v2_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "v2_0003"
down_revision = "v2_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pure SQLite additive migration: no parent-table reconstruction and no
    # FK/cascade exposure. Valid values are enforced by V2 application code.
    op.add_column("pi", sa.Column("gross_weight_display_unit", sa.String(length=10), nullable=True))
    op.add_column("pi", sa.Column("vgm_display_unit", sa.String(length=10), nullable=True))


def downgrade() -> None:
    # Development/disposable only. SQLite may rebuild PI to drop columns; a
    # persistent rollback must restore the verified pre-upgrade file backup.
    op.drop_column("pi", "vgm_display_unit")
    op.drop_column("pi", "gross_weight_display_unit")
