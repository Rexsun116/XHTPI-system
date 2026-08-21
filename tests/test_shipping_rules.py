from datetime import datetime, timedelta
import unittest

from tests import test_support  # noqa: F401

from sqlalchemy import text
from werkzeug.datastructures import MultiDict

from app import Customer, Exporter, PI, User, app, db, update_shipping_facts
from reminders.engine import ReconcileActionType, reconcile_order_tasks
from reminders.enums import ActivityEvent, TaskHealth, TaskStatus
from reminders.rules.documents import DOCUMENT_RULES
from reminders.rules.shipping import SHIPPING_RULES, build_shipping_rules
from reminders.task_service import InvalidTransitionError, mark_done
from task_models import OrderTask


NOW = datetime(2026, 8, 21, 10, 0, 0)


class ShippingRulesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        for table in [
            "task_activity",
            "order_task",
            "pi_item",
            "freight_quote",
            "pi",
            "product",
            "freight_forwarder",
            "factory",
            "customer",
            "exporter",
            "user",
        ]:
            db.session.execute(text(f"DELETE FROM {table}"))
        db.session.commit()
        user = User(username="shipping-tester", role="admin")
        user.set_password("test-password")
        customer = Customer(code="C-SHIP", name="Shipping Customer", address="Address", country="Country")
        exporter = Exporter(code="E-SHIP", name="Shipping Exporter", address="Address", country="Country")
        db.session.add_all([user, customer, exporter])
        db.session.flush()
        self.pi = PI(
            pi_no="SHIP-001",
            pi_date=NOW.date(),
            customer_id=customer.id,
            exporter_id=exporter.id,
            status="新建",
        )
        db.session.add(self.pi)
        db.session.commit()
        self.user_id = user.id

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.context.pop()

    def apply(self, now=NOW, rules=SHIPPING_RULES):
        actions = reconcile_order_tasks(
            self.pi,
            now=now,
            apply=True,
            rule_definitions=rules,
        )
        db.session.commit()
        return actions

    def task(self, code):
        return OrderTask.query.filter_by(pi_id=self.pi.id, task_code=code).one_or_none()

    def complete_driver(self):
        self.pi.driver_name = "Li"
        self.pi.driver_phone = "13800000000"
        self.pi.vehicle_number = "陕A12345"
        db.session.commit()

    def test_01_container_loading_at_takes_priority_over_legacy_date(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        self.pi.container_loading_at = NOW + timedelta(hours=2)
        db.session.commit()
        self.apply(rules=DOCUMENT_RULES)
        task = self.task("DOCUMENT_COO")
        self.assertEqual(task.status, TaskStatus.UPCOMING)
        self.assertEqual(task.activation_at, NOW + timedelta(hours=2))
        self.assertEqual(task.context_payload["loading_precision"], "DATETIME")

    def test_02_legacy_container_date_fallback(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply(rules=DOCUMENT_RULES)
        self.assertEqual(self.task("DOCUMENT_COO").status, TaskStatus.ACTION)
        self.assertEqual(self.task("DOCUMENT_COO").context_payload["loading_precision"], "DATE")

    def test_03_driver_before_window_is_upcoming_with_configurable_threshold(self):
        self.pi.container_loading_at = NOW + timedelta(hours=7)
        db.session.commit()
        self.apply(rules=build_shipping_rules(driver_lead_hours=6))
        task = self.task("SHIPPING_DRIVER_INFO")
        self.assertEqual(task.status, TaskStatus.UPCOMING)
        self.assertEqual(task.context_payload["driver_reminder_lead_hours"], 6)
        self.assertEqual(task.context_payload["business_parameter_status"], "TEST_OVERRIDE")

    def test_04_driver_window_open_is_action(self):
        self.pi.container_loading_at = NOW + timedelta(hours=12)
        db.session.commit()
        self.apply()
        task = self.task("SHIPPING_DRIVER_INFO")
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.health, TaskHealth.NORMAL)
        self.assertEqual(task.context_payload["business_parameter_status"], "CONFIRMED")

    def test_05_complete_driver_auto_resolves(self):
        self.pi.container_loading_at = NOW + timedelta(hours=12)
        db.session.commit()
        self.apply()
        self.complete_driver()
        actions = self.apply(now=NOW + timedelta(minutes=1))
        task = self.task("SHIPPING_DRIVER_INFO")
        self.assertEqual(actions[0].action, ReconcileActionType.AUTO_RESOLVE)
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.resolution_code, "DRIVER_INFO_COMPLETE")
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.AUTO_RESOLVED)

    def test_06_driver_rule_data_cannot_be_manually_done(self):
        self.pi.container_loading_at = NOW + timedelta(hours=12)
        db.session.commit()
        self.apply()
        with self.assertRaises(InvalidTransitionError):
            mark_done(self.task("SHIPPING_DRIVER_INFO"), actor_id=self.user_id, now=NOW)

    def test_07_driver_field_removed_reactivates_same_task(self):
        self.pi.container_loading_at = NOW + timedelta(hours=12)
        db.session.commit()
        self.apply()
        task_id = self.task("SHIPPING_DRIVER_INFO").id
        self.complete_driver()
        self.apply(now=NOW + timedelta(minutes=1))
        self.pi.vehicle_number = None
        db.session.commit()
        actions = self.apply(now=NOW + timedelta(minutes=2))
        task = self.task("SHIPPING_DRIVER_INFO")
        self.assertEqual(actions[0].action, ReconcileActionType.RULE_REACTIVATE)
        self.assertEqual(task.id, task_id)
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.REACTIVATED)

    def test_08_loading_reached_with_incomplete_driver_is_exception(self):
        self.pi.container_loading_at = NOW
        db.session.commit()
        self.apply()
        task = self.task("SHIPPING_DRIVER_INFO")
        self.assertEqual(task.health, TaskHealth.EXCEPTION)
        self.assertEqual(task.context_payload["health_reason_code"], "DRIVER_INFO_MISSING")

    def test_09_future_etd_is_upcoming_without_exception(self):
        self.pi.etd = NOW.date() + timedelta(days=1)
        db.session.commit()
        self.apply()
        task = self.task("SHIPPING_ACTUAL_DEPARTURE")
        self.assertEqual(task.status, TaskStatus.UPCOMING)
        self.assertEqual(task.health, TaskHealth.NORMAL)

    def test_10_etd_reached_is_action_exception(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        task = self.task("SHIPPING_ACTUAL_DEPARTURE")
        self.assertEqual((task.status, task.health), (TaskStatus.ACTION, TaskHealth.EXCEPTION))
        self.assertEqual(task.context_payload["health_reason_code"], "ACTUAL_DEPARTURE_MISSING")

    def test_11_overdue_etd_context_has_correct_days(self):
        self.pi.etd = NOW.date() - timedelta(days=2)
        db.session.commit()
        self.apply()
        task = self.task("SHIPPING_ACTUAL_DEPARTURE")
        self.assertEqual(task.context_payload["overdue_days"], 2)
        self.assertIn("2 天", task.context_payload["health_message"])

    def test_12_actual_departure_auto_resolves(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.actual_departure_date = NOW.date()
        db.session.commit()
        actions = self.apply(now=NOW + timedelta(hours=1))
        task = self.task("SHIPPING_ACTUAL_DEPARTURE")
        self.assertEqual(actions[0].action, ReconcileActionType.AUTO_RESOLVE)
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.AUTO_RESOLVED)

    def test_13_actual_departure_cleared_reactivates(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        task_id = self.task("SHIPPING_ACTUAL_DEPARTURE").id
        self.pi.actual_departure_date = NOW.date()
        db.session.commit()
        self.apply(now=NOW + timedelta(hours=1))
        self.pi.actual_departure_date = None
        db.session.commit()
        actions = self.apply(now=NOW + timedelta(hours=2))
        self.assertEqual(actions[0].action, ReconcileActionType.RULE_REACTIVATE)
        self.assertEqual(self.task("SHIPPING_ACTUAL_DEPARTURE").id, task_id)

    def test_14_etd_pushed_future_is_deferred(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.etd = NOW.date() + timedelta(days=3)
        db.session.commit()
        actions = self.apply(now=NOW + timedelta(hours=1))
        task = self.task("SHIPPING_ACTUAL_DEPARTURE")
        self.assertEqual(actions[0].action, ReconcileActionType.DEFER)
        self.assertEqual((task.status, task.health), (TaskStatus.UPCOMING, TaskHealth.NORMAL))
        self.assertEqual(task.activities[-1].reason_code, "RULE_DEFERRED")

    def test_15_deferred_etd_reaches_again_on_same_task(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        task_id = self.task("SHIPPING_ACTUAL_DEPARTURE").id
        self.pi.etd = NOW.date() + timedelta(days=2)
        db.session.commit()
        self.apply()
        self.apply(now=NOW + timedelta(days=2))
        task = self.task("SHIPPING_ACTUAL_DEPARTURE")
        self.assertEqual(task.id, task_id)
        self.assertEqual((task.status, task.health), (TaskStatus.ACTION, TaskHealth.EXCEPTION))

    def test_16_etd_reconcile_never_duplicates_task(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.apply()
        self.assertEqual(OrderTask.query.filter_by(task_code="SHIPPING_ACTUAL_DEPARTURE").count(), 1)

    def test_17_future_eta_is_upcoming(self):
        self.pi.eta = NOW.date() + timedelta(days=1)
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("SHIPPING_ACTUAL_ARRIVAL").status, TaskStatus.UPCOMING)

    def test_18_eta_reached_is_action_exception(self):
        self.pi.eta = NOW.date()
        db.session.commit()
        self.apply()
        task = self.task("SHIPPING_ACTUAL_ARRIVAL")
        self.assertEqual((task.status, task.health), (TaskStatus.ACTION, TaskHealth.EXCEPTION))
        self.assertEqual(task.context_payload["health_reason_code"], "ACTUAL_ARRIVAL_MISSING")

    def test_19_actual_arrival_auto_resolves(self):
        self.pi.eta = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.actual_arrival_date = NOW.date()
        db.session.commit()
        self.apply(now=NOW + timedelta(hours=1))
        self.assertEqual(self.task("SHIPPING_ACTUAL_ARRIVAL").status, TaskStatus.DONE)

    def test_20_actual_arrival_cleared_reactivates(self):
        self.pi.eta = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.actual_arrival_date = NOW.date()
        db.session.commit()
        self.apply(now=NOW + timedelta(hours=1))
        self.pi.actual_arrival_date = None
        db.session.commit()
        actions = self.apply(now=NOW + timedelta(hours=2))
        self.assertEqual(actions[0].action, ReconcileActionType.RULE_REACTIVATE)

    def test_21_eta_pushed_future_is_deferred(self):
        self.pi.eta = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.eta = NOW.date() + timedelta(days=2)
        db.session.commit()
        actions = self.apply()
        self.assertEqual(actions[0].action, ReconcileActionType.DEFER)
        self.assertEqual(self.task("SHIPPING_ACTUAL_ARRIVAL").status, TaskStatus.UPCOMING)

    def test_22_etd_ignores_incorrect_order_status(self):
        self.pi.status = "新建"
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("SHIPPING_ACTUAL_DEPARTURE").health, TaskHealth.EXCEPTION)

    def test_23_eta_ignores_incorrect_order_status(self):
        self.pi.status = "待发运"
        self.pi.eta = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("SHIPPING_ACTUAL_ARRIVAL").health, TaskHealth.EXCEPTION)

    def test_24_coo_waits_for_precise_loading_time(self):
        self.pi.coo_required = "需要"
        self.pi.container_loading_at = NOW + timedelta(minutes=30)
        db.session.commit()
        self.apply(rules=DOCUMENT_RULES)
        self.assertEqual(self.task("DOCUMENT_COO").status, TaskStatus.UPCOMING)
        self.apply(now=NOW + timedelta(minutes=30), rules=DOCUMENT_RULES)
        self.assertEqual(self.task("DOCUMENT_COO").status, TaskStatus.ACTION)

    def test_25_export_license_uses_loading_calendar_date_minus_five(self):
        self.pi.export_license_required = "需要"
        self.pi.container_loading_at = NOW + timedelta(days=5, hours=8)
        db.session.commit()
        self.apply(rules=DOCUMENT_RULES)
        self.assertEqual(self.task("DOCUMENT_EXPORT_LICENSE").status, TaskStatus.ACTION)

    def test_26_unchanged_reconcile_is_idempotent(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.apply(), [])

    def test_27_unchanged_reconcile_does_not_add_activity(self):
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        task = self.task("SHIPPING_ACTUAL_DEPARTURE")
        activity_count = len(task.activities)
        self.apply()
        self.assertEqual(len(task.activities), activity_count)

    def test_28_rule_data_manual_done_regression(self):
        self.pi.eta = NOW.date()
        db.session.commit()
        self.apply()
        with self.assertRaises(InvalidTransitionError):
            mark_done(self.task("SHIPPING_ACTUAL_ARRIVAL"), actor_id=self.user_id, now=NOW)

    def test_29_shipping_form_syncs_legacy_container_date(self):
        update_shipping_facts(
            self.pi,
            MultiDict(
                {
                    "container_loading_at": "2026-08-25T14:30",
                    "driver_name": "Li",
                    "driver_phone": "13800000000",
                    "vehicle_number": "陕A12345",
                }
            ),
        )
        self.assertEqual(self.pi.container_loading_at, datetime(2026, 8, 25, 14, 30))
        self.assertEqual(self.pi.container_date.isoformat(), "2026-08-25")
        self.assertEqual(self.pi.vehicle_number, "陕A12345")

    def test_30_shipping_form_omissions_preserve_existing_values(self):
        self.pi.driver_name = "Existing Driver"
        update_shipping_facts(self.pi, MultiDict({"driver_phone": " 13900000000 "}))
        self.assertEqual(self.pi.driver_name, "Existing Driver")
        self.assertEqual(self.pi.driver_phone, "13900000000")

    def test_31_shipping_context_exposes_action_targets(self):
        self.pi.container_loading_at = NOW
        self.pi.etd = NOW.date()
        self.pi.eta = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("SHIPPING_DRIVER_INFO").context_payload["action_target"], "UPDATE_LOADING_INFO")
        self.assertEqual(self.task("SHIPPING_ACTUAL_DEPARTURE").context_payload["action_target"], "UPDATE_SHIPPING_INFO")
        self.assertEqual(self.task("SHIPPING_ACTUAL_ARRIVAL").context_payload["action_target"], "UPDATE_ARRIVAL_INFO")

    def test_32_resolving_shipping_task_does_not_change_order_status(self):
        self.pi.status = "新建"
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.actual_departure_date = NOW.date()
        db.session.commit()
        self.apply(now=NOW + timedelta(hours=1))
        self.assertEqual(self.pi.status, "新建")


if __name__ == "__main__":
    unittest.main()
