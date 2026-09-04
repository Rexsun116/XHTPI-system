"""v2_0004 migration and independent USD/CNY freight settlement workflow."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, FreightSettlement, OrderTask, PI, User, db
from v2.services import (apply_manual_task_business_fact, reconcile_order_tasks_for_pi,
                         required_freight_settlements_paid)
from v2.task_service import mark_done


ROOT = Path(__file__).resolve().parents[1]


class FreightCurrencyWorkflowTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'workflow.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="freight", password_hash=generate_password_hash("secret"))
        customer = Customer(code="CUS001", name="Customer")
        exporter = Exporter(code="EXP001", name="Exporter")
        db.session.add_all([self.user, customer, exporter]); db.session.flush()
        self.pi = PI(pi_no="FREIGHT-SPLIT", pi_date=date(2026, 9, 4), order_type="SALES",
                     status="SHIPPED", customer_id=customer.id, exporter_id=exporter.id,
                     customer_name_snapshot=customer.name, exporter_name_snapshot=exporter.name,
                     actual_departure_date=date(2026, 8, 20), currency="USD")
        db.session.add(self.pi); db.session.flush()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def task(self, code):
        return db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == self.pi.id, OrderTask.task_code == code))

    def reconcile(self):
        reconcile_order_tasks_for_pi(self.pi, now=datetime(2026, 9, 4, 12))
        db.session.flush()

    def confirm_invoice(self, currency):
        task = self.task(f"FREIGHT_{currency}_INVOICE_ISSUED")
        payload = apply_manual_task_business_fact(task)
        mark_done(task, self.user.id, payload=payload)
        self.reconcile()

    def test_independent_invoice_and_payment_branches(self):
        settlement = FreightSettlement(pi_id=self.pi.id, usd_bill_required=True, cny_bill_required=True,
            usd_bill_amount=Decimal("100"), usd_bill_confirmed=True,
            cny_bill_amount=Decimal("700"), cny_bill_confirmed=False)
        db.session.add(settlement); self.reconcile()
        self.assertEqual(self.task("FREIGHT_USD_INVOICE_ISSUED").status, "ACTION")
        self.assertIsNone(self.task("FREIGHT_CNY_INVOICE_ISSUED"))
        self.confirm_invoice("USD")
        self.assertTrue(settlement.usd_invoice_issued)
        self.assertIsNone(settlement.cny_invoice_issued)
        self.assertEqual(self.task("FREIGHT_USD_PAYMENT_CONFIRM").status, "ACTION")
        self.assertIsNone(self.task("FREIGHT_CNY_PAYMENT_CONFIRM"))
        settlement.usd_payment_status = "PAID"; settlement.usd_paid_at = datetime(2026, 9, 4, 12)
        self.reconcile()
        self.assertEqual(self.task("FREIGHT_USD_PAYMENT_CONFIRM").status, "DONE")
        self.assertFalse(required_freight_settlements_paid(settlement))
        settlement.cny_bill_confirmed = True; self.reconcile()
        self.confirm_invoice("CNY")
        self.assertEqual(self.task("FREIGHT_CNY_PAYMENT_CONFIRM").status, "ACTION")
        settlement.cny_payment_status = "PAID"; settlement.cny_paid_at = datetime(2026, 9, 4, 12)
        self.reconcile()
        self.assertTrue(required_freight_settlements_paid(settlement))

    def test_payment_reversal_reactivates_same_task_and_requirement_cancel_is_scoped(self):
        settlement = FreightSettlement(pi_id=self.pi.id, usd_bill_required=True, cny_bill_required=True,
            usd_bill_amount=Decimal("100"), usd_bill_confirmed=True, usd_invoice_issued=True,
            usd_payment_status="UNPAID", cny_bill_amount=Decimal("700"), cny_bill_confirmed=True,
            cny_invoice_issued=True, cny_payment_status="UNPAID")
        db.session.add(settlement); self.reconcile()
        usd = self.task("FREIGHT_USD_PAYMENT_CONFIRM"); cny = self.task("FREIGHT_CNY_PAYMENT_CONFIRM")
        self.assertEqual(usd.status, "ACTION"); self.assertEqual(cny.status, "ACTION")
        usd_id = usd.id; settlement.usd_payment_status = "PAID"; settlement.usd_paid_at = datetime(2026, 9, 4, 12); self.reconcile()
        self.assertEqual(self.task("FREIGHT_USD_PAYMENT_CONFIRM").status, "DONE")
        settlement.usd_payment_status = "UNPAID"; settlement.usd_paid_at = None; self.reconcile()
        self.assertEqual((self.task("FREIGHT_USD_PAYMENT_CONFIRM").id, self.task("FREIGHT_USD_PAYMENT_CONFIRM").status), (usd_id, "ACTION"))
        settlement.usd_bill_required = False; self.reconcile()
        self.assertEqual(self.task("FREIGHT_USD_PAYMENT_CONFIRM").status, "CANCELLED")
        self.assertEqual(self.task("FREIGHT_CNY_PAYMENT_CONFIRM").status, "ACTION")

    def test_cny_paid_does_not_resolve_or_write_usd_branch(self):
        settlement = FreightSettlement(
            pi_id=self.pi.id,
            usd_bill_required=True, usd_bill_amount=Decimal("100"), usd_bill_confirmed=True,
            usd_invoice_issued=True, usd_payment_status="UNPAID", usd_paid_at=None,
            cny_bill_required=True, cny_bill_amount=Decimal("700"), cny_bill_confirmed=True,
            cny_invoice_issued=True, cny_payment_status="UNPAID", cny_paid_at=None,
        )
        db.session.add(settlement); self.reconcile()
        usd = self.task("FREIGHT_USD_PAYMENT_CONFIRM")
        cny = self.task("FREIGHT_CNY_PAYMENT_CONFIRM")
        settlement.cny_payment_status = "PAID"
        settlement.cny_paid_at = datetime(2026, 9, 4, 12)
        self.reconcile()
        self.assertEqual(usd.status, "ACTION")
        self.assertEqual(cny.status, "DONE")
        self.assertIsNone(settlement.usd_paid_at)
        self.assertEqual(settlement.cny_paid_at, datetime(2026, 9, 4, 12))
        self.assertFalse(required_freight_settlements_paid(settlement))
        task_ids = (usd.id, cny.id)
        self.reconcile()
        self.assertEqual((self.task("FREIGHT_USD_PAYMENT_CONFIRM").id,
                          self.task("FREIGHT_CNY_PAYMENT_CONFIRM").id), task_ids)
        self.assertEqual(self.task("FREIGHT_USD_PAYMENT_CONFIRM").status, "ACTION")

    def test_legacy_active_tasks_are_cancelled_and_done_history_is_preserved(self):
        settlement = FreightSettlement(pi_id=self.pi.id, usd_bill_required=True)
        active = OrderTask(pi_id=self.pi.id, task_code="FREIGHT_INVOICE_ISSUED", title="old", source="AUTO",
            status="ACTION", health="NORMAL", completion_mode="MANUAL", dedupe_key=f"v2:order:{self.pi.id}:freight_invoice_issued")
        done = OrderTask(pi_id=self.pi.id, task_code="FREIGHT_PAYMENT_CONFIRM", title="old done", source="AUTO",
            status="DONE", health="NORMAL", completion_mode="RULE_DATA", dedupe_key=f"v2:order:{self.pi.id}:freight_payment_confirm")
        db.session.add_all([settlement, active, done]); self.reconcile()
        self.assertEqual(active.status, "CANCELLED")
        self.assertEqual(active.activities[-1].note, "Superseded by currency-specific freight settlement workflow.")
        self.assertEqual(done.status, "DONE")

    def test_crafted_non_required_payment_post_is_rejected(self):
        settlement = FreightSettlement(pi_id=self.pi.id, usd_bill_required=True)
        db.session.add(settlement); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        self.assertEqual(client.get(f"/v2/orders/{self.pi.id}").status_code, 200)
        response = client.post(f"/v2/orders/{self.pi.id}/facts", data={"cny_payment_status": "PAID"})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(settlement.cny_payment_status)

    def test_amount_only_form_post_preserves_unset_payment_facts(self):
        settlement = FreightSettlement(pi_id=self.pi.id, usd_bill_required=True, cny_bill_required=True)
        db.session.add(settlement); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        page = client.get(f"/v2/orders/{self.pi.id}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'name="usd_payment_status"', page.data)
        self.assertNotIn(b'name="cny_payment_status"', page.data)
        response = client.post(f"/v2/orders/{self.pi.id}/facts", data={
            "usd_bill_amount": "100.00", "cny_bill_amount": "700.00",
        })
        self.assertEqual(response.status_code, 302)
        db.session.refresh(settlement)
        self.assertEqual(settlement.usd_bill_amount, Decimal("100.00"))
        self.assertEqual(settlement.cny_bill_amount, Decimal("700.00"))
        self.assertIsNone(settlement.usd_payment_status)
        self.assertIsNone(settlement.cny_payment_status)
        self.assertIsNone(settlement.usd_paid_at)
        self.assertIsNone(settlement.cny_paid_at)

    def test_route_allows_only_invoiced_currency_payment_updates(self):
        settlement = FreightSettlement(pi_id=self.pi.id, usd_bill_required=True, cny_bill_required=True,
            usd_invoice_issued=None, cny_invoice_issued=None)
        db.session.add(settlement); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        self.assertEqual(client.post(f"/v2/orders/{self.pi.id}/facts", data={
            "usd_payment_status": "PAID",
        }).status_code, 400)
        settlement.usd_invoice_issued = True; db.session.commit()
        self.assertEqual(client.post(f"/v2/orders/{self.pi.id}/facts", data={
            "usd_payment_status": "UNPAID",
        }).status_code, 302)
        db.session.refresh(settlement)
        self.assertEqual(settlement.usd_payment_status, "UNPAID")
        self.assertIsNone(settlement.cny_payment_status)
        self.assertEqual(client.post(f"/v2/orders/{self.pi.id}/facts", data={
            "cny_payment_status": "PAID",
        }).status_code, 400)
        settlement.cny_invoice_issued = True; db.session.commit()
        self.assertEqual(client.post(f"/v2/orders/{self.pi.id}/facts", data={
            "cny_payment_status": "UNPAID",
        }).status_code, 302)
        db.session.refresh(settlement)
        self.assertEqual(settlement.cny_payment_status, "UNPAID")


class FreightCurrencyMigrationTest(TestCase):
    def test_upgrade_downgrade_and_data_policy(self):
        with tempfile.TemporaryDirectory(prefix="xhtpi-v2-0004-") as directory:
            path = Path(directory) / "migration.db"
            env = os.environ | {"XHTPI_V2_DATABASE_URL": f"sqlite:///{path}"}
            command = [sys.executable, "-m", "alembic", "-c", "migrations_v2/alembic.ini"]
            subprocess.run(command + ["upgrade", "v2_0003"], cwd=ROOT, env=env, check=True, capture_output=True)
            with sqlite3.connect(path) as conn:
                now = "2026-09-04 12:00:00"
                for pi_id, usd, cny, status in ((1, 1, 0, "PAID"), (2, 0, 1, "UNPAID"), (3, 1, 1, "PAID"), (4, 0, 0, None)):
                    conn.execute("INSERT INTO customer (id, code, name, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)", (pi_id, f"C{pi_id}", f"C{pi_id}", now, now))
                    conn.execute("INSERT INTO pi (id, pi_no, pi_date, order_type, status, customer_id, customer_name_snapshot, currency, created_at, updated_at) VALUES (?, ?, '2026-09-04', 'SALES', 'NEW', ?, ?, 'USD', ?, ?)", (pi_id, f"P{pi_id}", pi_id, f"C{pi_id}", now, now))
                    conn.execute("INSERT INTO freight_settlement (pi_id, usd_bill_required, cny_bill_required, invoice_issued, invoice_issued_at, payment_status, paid_at, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)", (pi_id, usd, cny, now, status, now if status else None, now, now))
                conn.commit()
            subprocess.run(command + ["upgrade", "v2_0004"], cwd=ROOT, env=env, check=True, capture_output=True)
            with sqlite3.connect(path) as conn:
                rows = conn.execute("SELECT usd_invoice_issued, usd_payment_status, cny_invoice_issued, cny_payment_status FROM freight_settlement ORDER BY pi_id").fetchall()
                self.assertEqual(rows[0], (1, "PAID", None, None))
                self.assertEqual(rows[1], (None, None, 1, "UNPAID"))
                self.assertEqual(rows[2], (None, None, None, None))
                self.assertEqual(rows[3], (None, None, None, None))
            subprocess.run(command + ["downgrade", "v2_0003"], cwd=ROOT, env=env, check=True, capture_output=True)
            with sqlite3.connect(path) as conn:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(freight_settlement)")}
                self.assertIn("payment_status", columns); self.assertNotIn("usd_payment_status", columns)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            subprocess.run(command + ["upgrade", "v2_0004"], cwd=ROOT, env=env, check=True, capture_output=True)
