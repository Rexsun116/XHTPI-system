"""Add non-owning linked-trade groups without rebuilding the PI table.

Revision ID: v2_0005
Revises: v2_0004
"""

from alembic import op
import sqlalchemy as sa


revision = "v2_0005"
down_revision = "v2_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trade_group",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_no", sa.String(length=50), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    # SQLite can add nullable columns, including this FK, without recreating
    # pi.  Recreating pi would temporarily detach its cascade-linked children.
    # Alembic's SQLite implementation refuses an add-column ForeignKey even
    # though SQLite supports this exact additive ALTER TABLE form.  Execute
    # the native statement so the physical FK exists without table rebuild.
    op.execute(
        "ALTER TABLE pi ADD COLUMN trade_group_id INTEGER "
        "REFERENCES trade_group(id) ON DELETE RESTRICT"
    )
    op.add_column("pi", sa.Column("trade_role", sa.String(length=20), nullable=True))
    op.add_column(
        "pi",
        sa.Column("include_in_business_stats", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )
    op.create_index("ix_pi_trade_group_id", "pi", ["trade_group_id"])
    op.create_index("uq_pi_trade_group_role", "pi", ["trade_group_id", "trade_role"], unique=True)

    # SQLite cannot add a CHECK constraint without rebuilding pi.  These
    # triggers enforce the same pairing rule while preserving all child rows.
    pairing = """
        NOT (
            (NEW.trade_group_id IS NULL AND NEW.trade_role IS NULL)
            OR
            (NEW.trade_group_id IS NOT NULL AND NEW.trade_role IS NOT NULL
             AND NEW.trade_role IN ('CUSTOMER_ORDER', 'EXPORT_ORDER'))
        )
    """
    op.execute(f"""
        CREATE TRIGGER trg_pi_trade_link_pairing_insert
        BEFORE INSERT ON pi FOR EACH ROW WHEN {pairing}
        BEGIN SELECT RAISE(ABORT, 'trade_group_id and trade_role must form a valid linked-trade pair'); END
    """)
    op.execute(f"""
        CREATE TRIGGER trg_pi_trade_link_pairing_update
        BEFORE UPDATE OF trade_group_id, trade_role ON pi FOR EACH ROW WHEN {pairing}
        BEGIN SELECT RAISE(ABORT, 'trade_group_id and trade_role must form a valid linked-trade pair'); END
    """)
    op.execute("""
        CREATE TRIGGER trg_trade_group_member_protect_delete
        BEFORE DELETE ON trade_group FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM pi WHERE trade_group_id = OLD.id)
        BEGIN SELECT RAISE(ABORT, 'cannot delete a trade group with linked PI rows'); END
    """)


def downgrade():
    connection = op.get_bind()
    linked_count = connection.execute(sa.text(
        "SELECT COUNT(*) FROM pi WHERE trade_group_id IS NOT NULL"
    )).scalar_one()
    if linked_count:
        raise RuntimeError(
            "Cannot downgrade v2_0005 while linked-trade PI rows exist; unlink them through a future group-aware workflow first."
        )
    op.execute("DROP TRIGGER IF EXISTS trg_trade_group_member_protect_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_pi_trade_link_pairing_update")
    op.execute("DROP TRIGGER IF EXISTS trg_pi_trade_link_pairing_insert")
    op.drop_index("uq_pi_trade_group_role", table_name="pi")
    op.drop_index("ix_pi_trade_group_id", table_name="pi")
    op.drop_column("pi", "include_in_business_stats")
    op.drop_column("pi", "trade_role")
    op.drop_column("pi", "trade_group_id")
    op.drop_table("trade_group")
