"""Audit coverage for user-confirmed aggregate advance-payment facts."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, OrderTask, PI, PIItem, TaskActivity, TradeGroup, User, db
from v2.services import reconcile_order_tasks_for_pi, save_order_with_reconcile


class AdvanceReceiptAuditTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="advance-audit", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="CUS", name="Customer")
        self.exporter = Exporter(code="EXP", name="Exporter")
        db.session.add_all((self.user, self.customer, self.exporter)); db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def pi(self, number, *, group=None, role=None):
        pi = PI(
            pi_no=number, pi_date=date(2026, 9, 1), order_type="SALES", status="NEW",
            customer_id=self.customer.id, exporter_id=self.exporter.id,
            customer_name_snapshot="Customer", exporter_name_snapshot="Exporter", currency="USD",
            planned_shipment_date=date(2026, 9, 30), advance_payment_amount=Decimal("100"),
            advance_received_amount=Decimal("0"), balance_payment_amount=Decimal("900"),
            trade_group=group, trade_role=role,
        )
        pi.items.append(PIItem(unit_price=Decimal("100"), quantity=Decimal("10"), quantity_unit="MT",
                               line_total=Decimal("1000"), product_model_snapshot="R-996"))
        db.session.add(pi); db.session.flush()
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 10, 9))
        db.session.commit()
        return pi

    def advance_task(self, pi):
        return db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == pi.id, OrderTask.task_code == "PAYMENT_ADVANCE_WAITING"))

    def audit_events(self, pi):
        return list(db.session.scalars(db.select(TaskActivity).join(OrderTask).where(
            OrderTask.pi_id == pi.id,
            TaskActivity.event_type == "ADVANCE_RECEIPT_RECORDED",
        ).order_by(TaskActivity.id)))

    def post_receipt(self, pi, amount, received_at):
        return self.client().post(f"/v2/orders/{pi.id}/advance-receipt", data={
            "advance_received_amount": amount, "advance_received_at": received_at,
        })

    def test_successful_confirmation_records_cumulative_amount_date_currency_and_actor(self):
        pi = self.pi("AUDIT-SUCCESS")

        response = self.post_receipt(pi, "100.00", "2026-09-15")

        self.assertEqual(response.status_code, 302)
        event, = self.audit_events(pi)
        self.assertEqual(event.task_id, self.advance_task(pi).id)
        self.assertEqual(event.actor_type, "USER")
        self.assertEqual(event.actor_id, self.user.id)
        self.assertIsNotNone(event.created_at)
        self.assertEqual(event.payload, {
            "currency": "USD", "previous_amount": "0.00", "confirmed_amount": "100.00",
            "previous_received_at": None, "received_at": "2026-09-15",
        })
        self.assertIn("cumulative advance-payment", event.note)
        gate = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == pi.id, OrderTask.task_code == "STAGE_GATE_PRE_SHIPMENT"))
        self.assertEqual(gate.status, "ACTION")
        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertIn("Advance receipt confirmation", page)
        self.assertIn("Confirmed cumulative advance: USD 100.00", page)

    def test_reconciliation_auto_resolved_activity_remains_alongside_user_audit(self):
        pi = self.pi("AUDIT-RESOLVED")

        self.post_receipt(pi, "100", "2026-09-15")

        task = self.advance_task(pi)
        events = list(db.session.scalars(db.select(TaskActivity.event_type).where(TaskActivity.task_id == task.id)))
        self.assertEqual((task.status, task.resolution_code), ("DONE", "AUTO_RESOLVED"))
        self.assertIn("AUTO_RESOLVED", events)
        self.assertIn("ADVANCE_RECEIPT_RECORDED", events)

    def test_repeated_confirmation_preserves_existing_overwrite_semantics_and_records_before_after_values(self):
        pi = self.pi("AUDIT-REPEAT")

        self.assertEqual(self.post_receipt(pi, "20", "2026-09-14").status_code, 302)
        self.assertEqual(self.post_receipt(pi, "100", "2026-09-15").status_code, 302)

        events = self.audit_events(pi)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].payload["previous_amount"], "20.00")
        self.assertEqual(events[1].payload["previous_received_at"], "2026-09-14")
        self.assertEqual(events[1].payload["confirmed_amount"], "100.00")
        self.assertEqual(PI.query.get(pi.id).advance_received_amount, Decimal("100"))
        self.assertEqual(self.advance_task(pi).status, "DONE")

    def test_invalid_form_writes_neither_activity_nor_aggregate_payment_change(self):
        pi = self.pi("AUDIT-INVALID")

        response = self.post_receipt(pi, "", "")

        self.assertEqual(response.status_code, 400)
        db.session.expire_all()
        self.assertEqual(PI.query.get(pi.id).advance_received_amount, Decimal("0"))
        self.assertEqual(self.audit_events(pi), [])

    def test_before_commit_failure_rolls_back_payment_task_and_activity_together(self):
        pi = self.pi("AUDIT-ROLLBACK")
        task = self.advance_task(pi)
        pi.advance_received_amount = Decimal("100")
        pi.advance_received_at = datetime(2026, 9, 15)

        def failed_audit():
            db.session.add(TaskActivity(
                task_id=task.id, event_type="ADVANCE_RECEIPT_RECORDED", actor_type="USER",
                actor_id=self.user.id, payload={"confirmed_amount": "100.00"},
            ))
            raise RuntimeError("simulated audit failure")

        with self.assertRaisesRegex(RuntimeError, "simulated audit failure"):
            save_order_with_reconcile(pi, before_commit=failed_audit)

        db.session.expire_all()
        reloaded = db.session.get(PI, pi.id)
        self.assertEqual(reloaded.advance_received_amount, Decimal("0"))
        self.assertIsNone(reloaded.advance_received_at)
        self.assertNotEqual(self.advance_task(reloaded).status, "DONE")
        self.assertEqual(self.audit_events(reloaded), [])

    def test_export_order_is_still_rejected_without_an_audit_event(self):
        group = TradeGroup(group_no="TRI-ADVANCE-AUDIT")
        db.session.add(group); db.session.flush()
        self.pi("AUDIT-CUSTOMER", group=group, role="CUSTOMER_ORDER")
        export = self.pi("AUDIT-EXPORT", group=group, role="EXPORT_ORDER")

        response = self.post_receipt(export, "100", "2026-09-15")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.audit_events(export), [])

    def test_reading_existing_history_never_backfills_audit_events(self):
        pi = self.pi("AUDIT-HISTORY")

        self.client().get(f"/v2/orders/{pi.id}")

        self.assertEqual(self.audit_events(pi), [])
