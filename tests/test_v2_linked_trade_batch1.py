"""Batch 1 linked-trade schema, relationships, display, and delete safety."""

from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest import TestCase

from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, PI, PIItem, TradeGroup, User, db
from v2.order_deletion import OrderDeletionNotAllowed, delete_new_order


ROOT = Path(__file__).resolve().parents[1]


class LinkedTradeModelAndUiTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'linked.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="linked", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="CUS", name="Customer")
        self.exporter = Exporter(code="EXP", name="Exporter")
        db.session.add_all((self.user, self.customer, self.exporter)); db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def pi(self, number, *, group=None, role=None):
        pi = PI(
            pi_no=number, pi_date=date(2026, 9, 5), order_type="SALES", status="NEW",
            customer_id=self.customer.id, exporter_id=self.exporter.id,
            customer_name_snapshot=self.customer.name, exporter_name_snapshot=self.exporter.name,
            currency="USD", payment_terms="OA90", planned_shipment_date=date(2026, 10, 1),
            trade_group=group, trade_role=role,
        )
        pi.items.append(PIItem(unit_price=Decimal("10"), quantity=Decimal("1"), quantity_unit="MT", line_total=Decimal("10")))
        db.session.add(pi); db.session.flush()
        return pi

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def test_independent_orders_and_xht_like_numbers_remain_normal(self):
        independent = self.pi("XHT260901")
        self.assertFalse(independent.is_linked_trade)
        self.assertIsNone(independent.trade_group_id)
        self.assertIsNone(independent.trade_role)
        self.assertTrue(independent.include_in_business_stats)
        self.assertIsNone(independent.linked_peer)
        page = self.client().get(f"/v2/orders/{independent.id}").get_data(as_text=True)
        self.assertNotIn('id="linked-trade"', page)

    def test_role_membership_and_symmetric_peer_resolution(self):
        group = TradeGroup(group_no="TRI202609001")
        db.session.add(group); db.session.flush()
        customer_order = self.pi("WU260901", group=group, role="CUSTOMER_ORDER")
        export_order = self.pi("XHT260901", group=group, role="EXPORT_ORDER")
        db.session.commit()
        self.assertEqual(group.customer_order.id, customer_order.id)
        self.assertEqual(group.export_order.id, export_order.id)
        self.assertEqual(customer_order.linked_peer.id, export_order.id)
        self.assertEqual(export_order.linked_peer.id, customer_order.id)
        self.assertEqual(export_order.linked_customer_order.id, customer_order.id)
        self.assertEqual(customer_order.linked_export_order.id, export_order.id)
        page = self.client().get(f"/v2/orders/{customer_order.id}").get_data(as_text=True)
        self.assertIn('Triangular Trade · TRI202609001', page)
        self.assertIn('Role: Customer Order', page)
        self.assertIn(f'/v2/orders/{export_order.id}', page)
        reverse = self.client().get(f"/v2/orders/{export_order.id}").get_data(as_text=True)
        self.assertIn('Role: Export Order', reverse)
        self.assertIn(f'/v2/orders/{customer_order.id}', reverse)

    def test_incomplete_group_is_explicit_and_role_pairing_is_enforced(self):
        group = TradeGroup(group_no="TRI202609002")
        db.session.add(group); db.session.flush()
        customer_order = self.pi("WU260902", group=group, role="CUSTOMER_ORDER")
        db.session.commit()
        self.assertIsNone(customer_order.linked_peer)
        page = self.client().get(f"/v2/orders/{customer_order.id}").get_data(as_text=True)
        self.assertIn('Linked Order: not created yet', page)
        with self.assertRaises(IntegrityError):
            self.pi("WU260903", group=group, role="CUSTOMER_ORDER")
        db.session.rollback()
        with self.assertRaises(IntegrityError):
            self.pi("BAD-ROLE", group=group, role="PRIMARY")
        db.session.rollback()
        with self.assertRaises(IntegrityError):
            self.pi("MISSING-ROLE", group=group)
        db.session.rollback()
        with self.assertRaises(IntegrityError):
            self.pi("MISSING-GROUP", role="EXPORT_ORDER")

    def test_linked_new_order_delete_is_rejected_without_touching_group_or_peer(self):
        group = TradeGroup(group_no="TRI202609003")
        db.session.add(group); db.session.flush()
        customer_order = self.pi("WU260904", group=group, role="CUSTOMER_ORDER")
        export_order = self.pi("XHT260904", group=group, role="EXPORT_ORDER")
        db.session.commit()
        with self.assertRaisesRegex(OrderDeletionNotAllowed, "Linked-trade orders"):
            delete_new_order(customer_order, customer_order.pi_no)
        self.assertIsNotNone(db.session.get(PI, customer_order.id))
        self.assertIsNotNone(db.session.get(PI, export_order.id))
        self.assertIsNotNone(db.session.get(TradeGroup, group.id))
        response = self.client().post(f"/v2/orders/{customer_order.id}/delete", data={"confirmation": customer_order.pi_no})
        self.assertEqual(response.status_code, 409)


class LinkedTradeMigrationTest(TestCase):
    def command(self, path, *args, check=True):
        env = os.environ | {"XHTPI_V2_DATABASE_URL": f"sqlite:///{path}"}
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "migrations_v2/alembic.ini", *args],
            cwd=ROOT, env=env, check=check, capture_output=True, text=True,
        )

    def test_upgrade_preserves_rows_enforces_pairing_and_guarded_downgrade(self):
        with tempfile.TemporaryDirectory(prefix="xhtpi-v2-0005-") as directory:
            path = Path(directory) / "migration.db"
            self.command(path, "upgrade", "v2_0004")
            now = "2026-09-05 12:00:00"
            with sqlite3.connect(path) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("INSERT INTO customer (id, code, name, active, created_at, updated_at) VALUES (1, 'C1', 'Customer', 1, ?, ?)", (now, now))
                conn.execute("INSERT INTO pi (id, pi_no, pi_date, order_type, status, customer_id, customer_name_snapshot, currency, created_at, updated_at) VALUES (1, 'XHT-NORMAL', '2026-09-05', 'SALES', 'NEW', 1, 'Customer', 'USD', ?, ?)", (now, now))
                conn.execute("INSERT INTO pi_item (pi_id, unit_price, quantity, quantity_unit, line_total, created_at, updated_at) VALUES (1, 10, 1, 'MT', 10, ?, ?)", (now, now))
                conn.commit()
            self.command(path, "upgrade", "v2_0005")
            with sqlite3.connect(path) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                self.assertEqual(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0], "v2_0005")
                self.assertEqual(conn.execute("SELECT pi_no, trade_group_id, trade_role, include_in_business_stats FROM pi").fetchone(), ("XHT-NORMAL", None, None, 1))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM pi_item WHERE pi_id=1").fetchone()[0], 1)
                self.assertTrue(any(row[2] == "trade_group" for row in conn.execute("PRAGMA foreign_key_list(pi)")))
                conn.execute("INSERT INTO trade_group (id, group_no, created_at) VALUES (1, 'TRI-MIGRATION', ?)", (now,))
                conn.execute("INSERT INTO pi (id, pi_no, pi_date, order_type, status, customer_id, customer_name_snapshot, currency, trade_group_id, trade_role, created_at, updated_at) VALUES (2, 'WU-LINKED', '2026-09-05', 'SALES', 'NEW', 1, 'Customer', 'USD', 1, 'CUSTOMER_ORDER', ?, ?)", (now, now))
                conn.execute("INSERT INTO pi (id, pi_no, pi_date, order_type, status, customer_id, customer_name_snapshot, currency, trade_group_id, trade_role, created_at, updated_at) VALUES (3, 'XHT-LINKED', '2026-09-05', 'SALES', 'NEW', 1, 'Customer', 'USD', 1, 'EXPORT_ORDER', ?, ?)", (now, now))
                for role, pi_no in (("CUSTOMER_ORDER", "WU-DUP"), ("EXPORT_ORDER", "XHT-DUP"), ("PRIMARY", "BAD-ROLE")):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute("INSERT INTO pi (pi_no, pi_date, order_type, status, customer_id, customer_name_snapshot, currency, trade_group_id, trade_role, created_at, updated_at) VALUES (?, '2026-09-05', 'SALES', 'NEW', 1, 'Customer', 'USD', 1, ?, ?, ?)", (pi_no, role, now, now))
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("UPDATE pi SET trade_role=NULL WHERE id=2")
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("UPDATE pi SET trade_role='EXPORT_ORDER' WHERE id=1")
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("DELETE FROM trade_group WHERE id=1")
                conn.commit()
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            failed = self.command(path, "downgrade", "v2_0004", check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("Cannot downgrade v2_0005", failed.stderr + failed.stdout)
            with sqlite3.connect(path) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("DELETE FROM pi WHERE id IN (2, 3)")
                conn.execute("DELETE FROM trade_group WHERE id=1")
                conn.commit()
            self.command(path, "downgrade", "v2_0004")
            with sqlite3.connect(path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(pi)")}
                self.assertNotIn("trade_group_id", columns)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM pi_item WHERE pi_id=1").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
