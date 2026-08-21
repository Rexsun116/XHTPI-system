from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from tests import test_support  # noqa: F401

from sqlalchemy import text
from werkzeug.datastructures import MultiDict

from app import Customer, Exporter, Factory, PI, PIItem, Product, User, app, db, update_freight_facts
from reminders.engine import ReconcileActionType, reconcile_order_tasks
from reminders.enums import ActivityEvent, TaskHealth, TaskStatus
from reminders.freight import (
    confirm_cny_freight_amount,
    confirm_freight_invoice_issued,
    confirm_usd_freight_amount,
    freight_forwarder_name,
    update_freight_bill_amounts,
    update_freight_payment_status,
)
from reminders.rules.freight import FREIGHT_RULES
from reminders.task_service import InvalidTransitionError, mark_done
from task_models import OrderTask


NOW = datetime(2026, 8, 21, 10, 0)


class FreightRulesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        for table in (
            "task_activity", "order_task", "pi_item", "freight_quote", "pi", "product",
            "freight_forwarder", "factory", "customer", "exporter", "user",
        ):
            db.session.execute(text(f"DELETE FROM {table}"))
        db.session.commit()
        user = User(username="freight-tester", role="admin")
        user.set_password("password")
        customer = Customer(code="C-FRT", name="Freight Customer", address="A", country="US")
        exporter = Exporter(code="E-FRT", name="Exporter", address="A", country="CN")
        factory = Factory(code="F-FRT", name="Factory", country="CN")
        product = Product(code="P-FRT", model="R-996")
        db.session.add_all([user, customer, exporter, factory, product])
        db.session.flush()
        self.pi = PI(
            pi_no="FREIGHT-001", pi_date=NOW.date(), customer_id=customer.id,
            exporter_id=exporter.id, status="已发运",
            actual_departure_date=NOW.date() - timedelta(days=7),
        )
        db.session.add(self.pi)
        db.session.flush()
        db.session.add(PIItem(pi_id=self.pi.id, product_id=product.id, factory_id=factory.id, total_price=10000))
        db.session.commit()
        self.user_id = user.id

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.context.pop()

    def apply(self, now=NOW):
        actions = reconcile_order_tasks(
            self.pi, now=now, apply=True, rule_definitions=FREIGHT_RULES
        )
        db.session.commit()
        return actions

    def plan(self, now=NOW):
        return reconcile_order_tasks(
            self.pi, now=now, apply=False, rule_definitions=FREIGHT_RULES
        )

    def task(self, code):
        return OrderTask.query.filter_by(pi_id=self.pi.id, task_code=code).one_or_none()

    def configure(self, *, usd=None, cny=None):
        self.pi.freight_usd_bill_required = usd
        self.pi.freight_cny_bill_required = cny
        db.session.commit()

    def prepare_and_confirm(self, currency, amount):
        required_field = f"freight_{currency.lower()}_bill_required"
        amount_field = f"freight_{currency.lower()}_amount"
        setattr(self.pi, required_field, True)
        setattr(self.pi, amount_field, Decimal(str(amount)))
        db.session.commit()
        self.apply()
        code = f"FREIGHT_{currency}_AMOUNT_CONFIRM"
        if currency == "USD":
            confirm_usd_freight_amount(self.task(code), actor_id=self.user_id, now=NOW)
        else:
            confirm_cny_freight_amount(self.task(code), actor_id=self.user_id, now=NOW)
        db.session.commit()
        return self.task(code)

    def test_01_actual_departure_null_has_no_workflow(self):
        self.pi.actual_departure_date = None
        self.pi.freight_usd_bill_required = True
        db.session.commit()
        self.apply()
        self.assertEqual(OrderTask.query.count(), 0)

    def test_02_trigger_future_creates_upcoming(self):
        self.pi.actual_departure_date = NOW.date() - timedelta(days=5)
        self.pi.freight_usd_bill_required = True
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("FREIGHT_USD_AMOUNT_CAPTURE").status, TaskStatus.UPCOMING)

    def test_03_usd_required_missing_amount_is_capture_action(self):
        self.configure(usd=True)
        self.apply()
        self.assertEqual(self.task("FREIGHT_USD_AMOUNT_CAPTURE").status, TaskStatus.ACTION)

    def test_04_usd_capture_rule_data_cannot_done(self):
        self.configure(usd=True)
        self.apply()
        with self.assertRaises(InvalidTransitionError):
            mark_done(self.task("FREIGHT_USD_AMOUNT_CAPTURE"), actor_id=self.user_id, now=NOW)

    def test_05_usd_amount_resolves_capture_and_starts_confirm(self):
        self.configure(usd=True)
        self.apply()
        self.pi.freight_usd_amount = Decimal("18650.00")
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("FREIGHT_USD_AMOUNT_CAPTURE").status, TaskStatus.DONE)
        self.assertEqual(self.task("FREIGHT_USD_AMOUNT_CONFIRM").status, TaskStatus.ACTION)

    def test_06_usd_confirm_stores_snapshot_and_fact(self):
        task = self.prepare_and_confirm("USD", "18650.00")
        self.assertTrue(self.pi.freight_usd_confirmed)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.COMPLETED)
        self.assertEqual(task.activities[-1].payload, {"currency": "USD", "confirmed_amount": "18650.00"})

    def test_07_usd_changed_after_confirmation_reactivates_exception(self):
        task = self.prepare_and_confirm("USD", "18650")
        update_freight_bill_amounts(self.pi, usd_amount="19200", update_usd_amount=True)
        db.session.commit()
        actions = self.apply()
        self.assertFalse(self.pi.freight_usd_confirmed)
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.health, TaskHealth.EXCEPTION)
        self.assertTrue(any(a.action == ReconcileActionType.CONFIRMATION_REACTIVATE for a in actions))

    def test_08_cny_required_missing_amount_is_capture_action(self):
        self.configure(cny=True)
        self.apply()
        self.assertEqual(self.task("FREIGHT_CNY_AMOUNT_CAPTURE").status, TaskStatus.ACTION)

    def test_09_cny_amount_resolves_capture_and_starts_confirm(self):
        self.configure(cny=True)
        self.apply()
        self.pi.freight_cny_amount = Decimal("12800.00")
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("FREIGHT_CNY_AMOUNT_CAPTURE").status, TaskStatus.DONE)
        self.assertEqual(self.task("FREIGHT_CNY_AMOUNT_CONFIRM").status, TaskStatus.ACTION)

    def test_10_cny_confirm_snapshot(self):
        task = self.prepare_and_confirm("CNY", "12800.25")
        self.assertEqual(task.activities[-1].payload, {"currency": "CNY", "confirmed_amount": "12800.25"})

    def test_11_cny_changed_after_confirmation_reactivates(self):
        task = self.prepare_and_confirm("CNY", "12800")
        update_freight_bill_amounts(self.pi, cny_amount="12900", update_cny_amount=True)
        db.session.commit()
        self.apply()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.context_payload["health_reason_code"], "FREIGHT_CNY_AMOUNT_CHANGED_AFTER_CONFIRMATION")

    def test_12_only_usd_required_allows_invoice_after_usd_confirm(self):
        self.pi.freight_cny_bill_required = False
        db.session.commit()
        self.prepare_and_confirm("USD", "18650")
        self.apply()
        self.assertEqual(self.task("FREIGHT_INVOICE_ISSUED").status, TaskStatus.ACTION)

    def test_13_only_cny_required_allows_invoice_after_cny_confirm(self):
        self.pi.freight_usd_bill_required = False
        db.session.commit()
        self.prepare_and_confirm("CNY", "12800")
        self.apply()
        self.assertEqual(self.task("FREIGHT_INVOICE_ISSUED").status, TaskStatus.ACTION)

    def test_14_both_required_need_both_confirmations(self):
        self.pi.freight_cny_bill_required = True
        db.session.commit()
        self.prepare_and_confirm("USD", "18650")
        self.apply()
        self.assertEqual(self.task("FREIGHT_INVOICE_ISSUED").status, TaskStatus.UPCOMING)
        self.prepare_and_confirm("CNY", "12800")
        self.apply()
        self.assertEqual(self.task("FREIGHT_INVOICE_ISSUED").status, TaskStatus.ACTION)

    def test_15_neither_required_has_no_freight_workflow(self):
        self.configure(usd=False, cny=False)
        self.apply()
        self.assertEqual(OrderTask.query.count(), 0)

    def test_16_required_true_to_false_cancels_open_tasks(self):
        self.configure(usd=True)
        self.apply()
        task_id = self.task("FREIGHT_USD_AMOUNT_CAPTURE").id
        self.pi.freight_usd_bill_required = False
        db.session.commit()
        self.apply()
        task = db.session.get(OrderTask, task_id)
        self.assertEqual(task.status, TaskStatus.CANCELLED)

    def test_17_required_false_to_true_reactivates_same_task(self):
        self.configure(usd=True)
        self.apply()
        task = self.task("FREIGHT_USD_AMOUNT_CAPTURE")
        self.pi.freight_usd_bill_required = False
        db.session.commit(); self.apply()
        self.pi.freight_usd_bill_required = True
        db.session.commit(); self.apply()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(OrderTask.query.filter_by(task_code=task.task_code).count(), 1)

    def test_18_usd_and_cny_are_never_summed(self):
        self.prepare_and_confirm("USD", "18650")
        self.prepare_and_confirm("CNY", "12800")
        self.apply()
        context = self.task("FREIGHT_INVOICE_ISSUED").context_payload
        self.assertEqual(context["usd_freight"], "18650.00")
        self.assertEqual(context["cny_charges"], "12800.00")
        self.assertNotIn("total", context)

    def test_19_legacy_amount_is_not_mapped(self):
        self.pi.freight_invoice_amount = 9999.0
        self.configure(usd=True)
        self.apply()
        self.assertIsNone(self.pi.freight_usd_amount)
        context = self.task("FREIGHT_USD_AMOUNT_CAPTURE").context_payload
        self.assertEqual(context["legacy_amount_mapping"], "UNKNOWN")

    def test_20_legacy_bill_confirmation_is_one_conservative_task(self):
        self.pi.freight_invoice_confirmed = "已确认"
        db.session.commit(); self.apply()
        task = self.task("LEGACY_FREIGHT_BILL_CONFIRM")
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertIsNone(task.completed_at)
        self.assertIsNone(self.task("FREIGHT_USD_AMOUNT_CONFIRM"))
        self.assertIsNone(self.task("FREIGHT_CNY_AMOUNT_CONFIRM"))

    def test_21_invoice_only_after_all_required_confirmed(self):
        self.configure(usd=True, cny=True)
        self.pi.freight_usd_amount = Decimal("10")
        self.pi.freight_cny_amount = Decimal("20")
        db.session.commit(); self.apply()
        self.assertEqual(self.task("FREIGHT_INVOICE_ISSUED").status, TaskStatus.UPCOMING)

    def test_22_invoice_manual_done_updates_fact_and_activity(self):
        self.prepare_and_confirm("USD", "18650")
        self.apply()
        invoice = self.task("FREIGHT_INVOICE_ISSUED")
        confirm_freight_invoice_issued(invoice, actor_id=self.user_id, now=NOW)
        db.session.commit()
        self.assertEqual(self.pi.freight_invoice_issued, "已开具")
        self.assertEqual(invoice.status, TaskStatus.DONE)
        self.assertEqual(invoice.activities[-1].payload, {"freight_invoice_issued": "已开具"})

    def test_23_amount_change_after_invoice_adds_warning_without_changing_fact(self):
        self.prepare_and_confirm("USD", "18650")
        self.apply()
        confirm_freight_invoice_issued(self.task("FREIGHT_INVOICE_ISSUED"), actor_id=self.user_id, now=NOW)
        db.session.commit()
        update_freight_bill_amounts(self.pi, usd_amount="19200", update_usd_amount=True)
        db.session.commit(); self.apply()
        invoice = self.task("FREIGHT_INVOICE_ISSUED")
        self.assertEqual(self.pi.freight_invoice_issued, "已开具")
        self.assertIn("货代账单金额在发票流程后发生变化，请核实货代发票", invoice.context_payload["warnings"])

    def test_24_payment_task_only_after_invoice_issued(self):
        self.prepare_and_confirm("USD", "18650")
        self.apply()
        self.assertIsNone(self.task("FREIGHT_PAYMENT_CONFIRM"))
        confirm_freight_invoice_issued(self.task("FREIGHT_INVOICE_ISSUED"), actor_id=self.user_id, now=NOW)
        db.session.commit(); self.apply()
        self.assertEqual(self.task("FREIGHT_PAYMENT_CONFIRM").status, TaskStatus.ACTION)

    def test_25_payment_task_cannot_manual_done(self):
        self.pi.freight_invoice_issued = "已开具"
        db.session.commit(); self.apply()
        with self.assertRaises(InvalidTransitionError):
            mark_done(self.task("FREIGHT_PAYMENT_CONFIRM"), actor_id=self.user_id, now=NOW)

    def test_26_paid_auto_resolves_payment_task(self):
        self.pi.freight_invoice_issued = "已开具"
        db.session.commit(); self.apply()
        update_freight_payment_status(self.pi, status="已付款", paid_at=NOW)
        db.session.commit(); self.apply()
        task = self.task("FREIGHT_PAYMENT_CONFIRM")
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.resolution_code, "FREIGHT_PAYMENT_STATUS_PAID")

    def test_27_paid_to_unpaid_reactivates(self):
        self.pi.freight_invoice_issued = "已开具"
        db.session.commit(); self.apply()
        update_freight_payment_status(self.pi, status="已付款", paid_at=NOW)
        db.session.commit(); self.apply()
        task = self.task("FREIGHT_PAYMENT_CONFIRM")
        update_freight_payment_status(self.pi, status="未付款", paid_at=None)
        db.session.commit(); self.apply()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.REACTIVATED)

    def test_28_paid_at_is_optional_with_warning(self):
        self.pi.freight_invoice_issued = "已开具"
        db.session.commit(); self.apply()
        update_freight_payment_status(self.pi, status="已付款", paid_at=None)
        db.session.commit(); self.apply()
        self.assertIn("货代付款状态已标记为已付款，但付款日期未填写", self.task("FREIGHT_PAYMENT_CONFIRM").context_payload["warnings"])

    def test_29_legacy_invoice_issued_imports_unknown_time(self):
        self.pi.freight_invoice_issued = "已开具"
        db.session.commit(); self.apply()
        task = self.task("FREIGHT_INVOICE_ISSUED")
        self.assertEqual(task.resolution_code, "LEGACY_DONE")
        self.assertIsNone(task.completed_at)

    def test_30_legacy_payment_import_uses_paid_at_when_present(self):
        self.pi.freight_payment_status = "已付款"
        self.pi.freight_paid_at = NOW
        db.session.commit(); self.apply()
        task = self.task("FREIGHT_PAYMENT_CONFIRM")
        self.assertEqual(task.resolution_code, "LEGACY_DONE")
        self.assertEqual(task.completed_at, NOW)

    def test_31_missing_forwarder_does_not_crash(self):
        self.assertIsNone(freight_forwarder_name(self.pi))
        self.configure(usd=True)
        self.apply()
        self.assertIsNone(self.task("FREIGHT_USD_AMOUNT_CAPTURE").context_payload["freight_forwarder_name"])

    def test_32_decimal_accuracy_for_both_currencies(self):
        update_freight_bill_amounts(
            self.pi,
            usd_amount="18650.125", cny_amount="12800.235",
            update_usd_amount=True, update_cny_amount=True,
        )
        self.assertEqual(self.pi.freight_usd_amount, Decimal("18650.13"))
        self.assertEqual(self.pi.freight_cny_amount, Decimal("12800.24"))

    def test_33_repeated_reconcile_has_no_duplicate_or_activity(self):
        self.configure(usd=True)
        self.apply()
        task = self.task("FREIGHT_USD_AMOUNT_CAPTURE")
        activity_count = len(task.activities)
        self.assertEqual(self.apply(), [])
        self.assertEqual(OrderTask.query.filter_by(task_code=task.task_code).count(), 1)
        self.assertEqual(len(task.activities), activity_count)

    def test_34_dry_run_does_not_write(self):
        self.configure(usd=True, cny=True)
        actions = self.plan()
        self.assertTrue(actions)
        self.assertEqual(OrderTask.query.count(), 0)

    def test_35_form_updates_tri_state_amounts_and_paid_at(self):
        update_freight_facts(
            self.pi,
            MultiDict(
                {
                    "freight_usd_bill_required": "true",
                    "freight_usd_amount": "10.25",
                    "freight_cny_bill_required": "false",
                    "freight_cny_amount": "",
                    "freight_paid_at": "2026-08-21T09:30",
                }
            ),
        )
        self.assertTrue(self.pi.freight_usd_bill_required)
        self.assertEqual(self.pi.freight_usd_amount, Decimal("10.25"))
        self.assertFalse(self.pi.freight_cny_bill_required)
        self.assertEqual(self.pi.freight_paid_at, datetime(2026, 8, 21, 9, 30))

    def test_36_departure_pushed_future_defers_open_capture(self):
        self.configure(usd=True)
        self.apply()
        task = self.task("FREIGHT_USD_AMOUNT_CAPTURE")
        self.pi.actual_departure_date = NOW.date() + timedelta(days=1)
        db.session.commit()
        actions = self.apply()
        self.assertEqual(task.status, TaskStatus.UPCOMING)
        self.assertTrue(any(action.action == ReconcileActionType.DEFER for action in actions))

    def test_37_cleared_amount_reactivates_resolved_capture(self):
        self.configure(usd=True)
        self.apply()
        self.pi.freight_usd_amount = Decimal("10")
        db.session.commit(); self.apply()
        capture = self.task("FREIGHT_USD_AMOUNT_CAPTURE")
        self.assertEqual(capture.status, TaskStatus.DONE)
        self.pi.freight_usd_amount = None
        db.session.commit(); self.apply()
        self.assertEqual(capture.status, TaskStatus.ACTION)
        self.assertEqual(capture.activities[-1].event_type, ActivityEvent.REACTIVATED)

    def test_38_done_route_uses_freight_service_and_updates_fact(self):
        self.pi.freight_usd_bill_required = True
        self.pi.freight_usd_amount = Decimal("18650")
        db.session.commit(); self.apply()
        task = self.task("FREIGHT_USD_AMOUNT_CONFIRM")
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
            session["_task_csrf_token"] = "test-task-csrf"
        response = client.post(
            f"/tasks/{task.id}/done",
            json={"note": "Checked"},
            headers={"X-CSRF-Token": "test-task-csrf"},
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        refreshed_pi = db.session.get(PI, self.pi.id)
        refreshed_task = db.session.get(OrderTask, task.id)
        self.assertTrue(refreshed_pi.freight_usd_confirmed)
        completed = [
            activity
            for activity in refreshed_task.activities
            if activity.event_type == ActivityEvent.COMPLETED
            and (activity.payload or {}).get("currency") == "USD"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].payload["confirmed_amount"], "18650.00")


if __name__ == "__main__":
    unittest.main()
