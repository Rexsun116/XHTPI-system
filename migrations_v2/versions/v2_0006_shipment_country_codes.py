"""Add explicit structured shipment-country codes to V2 PI rows.

Revision ID: v2_0006
Revises: v2_0005
"""

from alembic import op
import sqlalchemy as sa


revision = "v2_0006"
down_revision = "v2_0005"
branch_labels = None
depends_on = None


def upgrade():
    # Additive nullable fields preserve all historical free-text route data.
    # ISO membership and route-pair semantics remain application validation.
    op.add_column("pi", sa.Column("loading_country_code", sa.String(length=2), nullable=True))
    op.add_column("pi", sa.Column("destination_country_code", sa.String(length=2), nullable=True))


def downgrade():
    op.drop_column("pi", "destination_country_code")
    op.drop_column("pi", "loading_country_code")
