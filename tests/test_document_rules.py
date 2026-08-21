from datetime import datetime, timedelta
import unittest

from tests import test_support  # noqa: F401  # Configure isolated DB before importing app.

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import Customer, Exporter, PI, User, app, db, update_document_required_facts
from reminders.adapters import (
    document_facts,
    legacy_mail_fact,
    legacy_required_fact,
)
from reminders.engine import ReconcileActionType, reconcile_order_tasks, task_dedupe_key
from reminders.enums import ActivityEvent, CompletionMode, TaskHealth, TaskSource, TaskStatus
from reminders.rules.documents import DOCUMENT_RULES
from reminders.task_service import CompletionValidationError, mark_done
from task_models import OrderTask
from werkzeug.datastructures import MultiDict


NOW = datetime(2026, 8, 21, 10, 0, 0)


class DocumentRulesTestCase(unittest.TestCase):
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

        user = User(username="rule-tester", role="admin")
        user.set_password("test-password")
        customer = Customer(code="C-RULE", name="Rule Customer", address="Address", country="Country")
        exporter = Exporter(code="E-RULE", name="Rule Exporter", address="Address", country="Country")
        db.session.add_all([user, customer, exporter])
        db.session.flush()
        self.pi = PI(
            pi_no="RULE-001",
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

    def apply(self, now=NOW):
        actions = reconcile_order_tasks(self.pi, now=now, apply=True)
        db.session.commit()
        return actions

    def task(self, task_code):
        return OrderTask.query.filter_by(pi_id=self.pi.id, task_code=task_code).one_or_none()

    def complete(self, task_code, payload=None):
        task = self.task(task_code)
        mark_done(task, actor_id=self.user_id, payload=payload, now=NOW)
        db.session.commit()
        return task

    def test_01_coo_false_does_not_create(self):
        self.pi.coo_required = "不需要"
        db.session.commit()
        self.apply()
        self.assertIsNone(self.task("DOCUMENT_COO"))

    def test_02_coo_true_future_is_upcoming(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date() + timedelta(days=1)
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_COO").status, TaskStatus.UPCOMING)

    def test_03_coo_trigger_reached_is_action(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_COO").status, TaskStatus.ACTION)

    def test_04_coo_legacy_done_has_unknown_completion_time(self):
        self.pi.coo_required = "已完成"
        db.session.commit()
        self.apply()
        task = self.task("DOCUMENT_COO")
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.resolution_code, "LEGACY_DONE")
        self.assertIsNone(task.completed_at)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.COMPLETED)

    def test_05_repeated_reconcile_does_not_duplicate(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        second = self.apply()
        self.assertEqual(OrderTask.query.filter_by(task_code="DOCUMENT_COO").count(), 1)
        self.assertEqual(second, [])

    def test_06_required_true_to_false_cancels(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.coo_required = "不需要"
        db.session.commit()
        actions = self.apply()
        self.assertEqual(self.task("DOCUMENT_COO").status, TaskStatus.CANCELLED)
        self.assertEqual(actions[0].reason_code, "REQUIREMENT_REMOVED")

    def test_07_required_false_to_true_reactivates_without_duplicate(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        self.pi.coo_required = "不需要"
        db.session.commit()
        self.apply()
        original_id = self.task("DOCUMENT_COO").id
        self.pi.coo_required = "需要"
        db.session.commit()
        actions = self.apply()
        self.assertEqual(actions[0].action, ReconcileActionType.REACTIVATE)
        self.assertEqual(self.task("DOCUMENT_COO").id, original_id)
        self.assertEqual(self.task("DOCUMENT_COO").status, TaskStatus.ACTION)

    def test_08_manual_done_auto_task_stays_done(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        task = self.complete("DOCUMENT_COO")
        activity_count = len(task.activities)
        actions = self.apply(now=NOW + timedelta(days=1))
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.resolution_code, "MANUAL_DONE")
        self.assertEqual(len(task.activities), activity_count)
        self.assertEqual(actions, [])

    def test_09_apta_includes_special_warning(self):
        self.pi.apta_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        task = self.task("DOCUMENT_APTA")
        self.assertIn("APTA 日期需在提单开船日期三日内", task.description)
        self.assertEqual(task.context_payload["special_notice"], "APTA 日期需在提单开船日期三日内")

    def test_10_export_license_uses_five_calendar_days(self):
        self.pi.export_license_required = "需要"
        self.pi.container_date = NOW.date() + timedelta(days=6)
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_EXPORT_LICENSE").status, TaskStatus.UPCOMING)
        self.apply(now=NOW + timedelta(days=1))
        self.assertEqual(self.task("DOCUMENT_EXPORT_LICENSE").status, TaskStatus.ACTION)

    def test_11_customs_uses_five_calendar_days(self):
        self.pi.customs_docs_required = "需要"
        self.pi.container_date = NOW.date() + timedelta(days=5)
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_CUSTOMS").status, TaskStatus.ACTION)

    def test_12_coc_activates_at_waiting_for_shipment(self):
        self.pi.coc_required = True
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_COC").status, TaskStatus.UPCOMING)
        self.pi.status = "待发运"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_COC").status, TaskStatus.ACTION)

    def test_13_original_bl_activates_when_shipped(self):
        self.pi.original_bl_required = True
        self.pi.status = "已发运"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_ORIGINAL_BL").status, TaskStatus.ACTION)

    def test_14_original_bl_activates_when_etd_reached(self):
        self.pi.original_bl_required = True
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_ORIGINAL_BL").status, TaskStatus.ACTION)

    def test_15_obd_uses_same_shipped_or_etd_trigger(self):
        self.pi.obd_electronic_required = True
        self.pi.etd = NOW.date() + timedelta(days=1)
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_OBD_BL").status, TaskStatus.UPCOMING)
        self.pi.status = "已发运"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_OBD_BL").status, TaskStatus.ACTION)

    def test_16_coa_uses_container_trigger(self):
        self.pi.coa_required = True
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_COA").status, TaskStatus.ACTION)

    def test_17_insurance_original_trigger(self):
        self.pi.insurance_original_required = True
        self.pi.status = "已发运"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_INSURANCE_ORIGINAL").status, TaskStatus.ACTION)

    def test_18_insurance_electronic_trigger(self):
        self.pi.insurance_electronic_required = True
        self.pi.etd = NOW.date()
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_INSURANCE_ELECTRONIC").status, TaskStatus.ACTION)

    def test_19_missing_container_date_is_upcoming_exception(self):
        self.pi.coo_required = "需要"
        db.session.commit()
        self.apply()
        task = self.task("DOCUMENT_COO")
        self.assertEqual(task.status, TaskStatus.UPCOMING)
        self.assertEqual(task.health, TaskHealth.EXCEPTION)
        self.assertEqual(task.context_payload["health_reason_code"], "MISSING_TRIGGER_DATE")

    def test_20_mail_bl_only_requires_bl_done(self):
        self.pi.status = "已发运"
        self.pi.original_bl_required = True
        self.pi.insurance_original_required = False
        self.pi.original_documents_mail_required = True
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_ORIGINALS_MAIL").status, TaskStatus.UPCOMING)
        self.complete("DOCUMENT_ORIGINAL_BL")
        self.apply()
        self.assertEqual(self.task("DOCUMENT_ORIGINALS_MAIL").status, TaskStatus.ACTION)

    def test_21_mail_insurance_only_requires_insurance_done(self):
        self.pi.status = "已发运"
        self.pi.original_bl_required = False
        self.pi.insurance_original_required = True
        self.pi.original_documents_mail_required = True
        db.session.commit()
        self.apply()
        self.complete("DOCUMENT_INSURANCE_ORIGINAL")
        self.apply()
        self.assertEqual(self.task("DOCUMENT_ORIGINALS_MAIL").status, TaskStatus.ACTION)

    def test_22_mail_with_both_requires_both_done(self):
        self.pi.status = "已发运"
        self.pi.original_bl_required = True
        self.pi.insurance_original_required = True
        self.pi.original_documents_mail_required = True
        db.session.commit()
        self.apply()
        self.complete("DOCUMENT_ORIGINAL_BL")
        self.apply()
        self.assertEqual(self.task("DOCUMENT_ORIGINALS_MAIL").status, TaskStatus.UPCOMING)
        self.complete("DOCUMENT_INSURANCE_ORIGINAL")
        self.apply()
        self.assertEqual(self.task("DOCUMENT_ORIGINALS_MAIL").status, TaskStatus.ACTION)

    def test_23_mail_not_generated_when_no_original_is_required(self):
        self.pi.original_bl_required = False
        self.pi.insurance_original_required = False
        self.pi.original_documents_mail_required = True
        db.session.commit()
        self.apply()
        self.assertIsNone(self.task("DOCUMENT_ORIGINALS_MAIL"))

    def test_24_mail_done_rejects_missing_tracking_number(self):
        self.pi.status = "已发运"
        self.pi.original_bl_required = True
        self.pi.original_documents_mail_required = True
        db.session.commit()
        self.apply()
        self.complete("DOCUMENT_ORIGINAL_BL")
        self.apply()
        with self.assertRaises(CompletionValidationError):
            mark_done(self.task("DOCUMENT_ORIGINALS_MAIL"), actor_id=self.user_id, payload={})

    def test_25_mail_done_retains_tracking_payload_without_changing_pi(self):
        self.pi.status = "已发运"
        self.pi.original_bl_required = True
        self.pi.original_documents_mail_required = True
        db.session.commit()
        self.apply()
        self.complete("DOCUMENT_ORIGINAL_BL")
        self.apply()
        payload = {"tracking_number": "TRACK-2A", "carrier": "DHL"}
        mail = self.complete("DOCUMENT_ORIGINALS_MAIL", payload=payload)
        self.assertEqual(mail.activities[-1].payload, payload)
        self.assertIsNone(self.pi.tracking_number)

    def test_26_payment_email_is_action_when_shipped(self):
        self.pi.status = "已发运"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("PAYMENT_EMAIL").status, TaskStatus.ACTION)

    def test_27_payment_email_repeated_reconcile_has_no_duplicate(self):
        self.pi.status = "已发运"
        db.session.commit()
        self.apply()
        self.assertEqual(self.apply(), [])
        self.assertEqual(OrderTask.query.filter_by(task_code="PAYMENT_EMAIL").count(), 1)

    def test_28_payment_email_rolls_back_and_reactivates(self):
        self.pi.status = "已发运"
        db.session.commit()
        self.apply()
        task_id = self.task("PAYMENT_EMAIL").id
        self.pi.status = "待发运"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("PAYMENT_EMAIL").status, TaskStatus.CANCELLED)
        self.pi.status = "已发运"
        db.session.commit()
        actions = self.apply()
        self.assertEqual(actions[0].action, ReconcileActionType.REACTIVATE)
        self.assertEqual(self.task("PAYMENT_EMAIL").id, task_id)

    def test_29_legacy_adapter_exact_mappings(self):
        self.assertEqual((legacy_required_fact("需要").required, legacy_required_fact("需要").legacy_done), (True, False))
        self.assertEqual((legacy_required_fact("不需要").required, legacy_required_fact("不需要").legacy_done), (False, False))
        self.assertEqual((legacy_required_fact("已完成").required, legacy_required_fact("已完成").legacy_done), (True, True))
        self.assertIsNone(legacy_required_fact("未知值").required)
        self.assertEqual((legacy_mail_fact("已邮寄").required, legacy_mail_fact("已邮寄").legacy_done), (True, True))

    def test_30_auto_dedupe_key_unique_constraint(self):
        key = task_dedupe_key(self.pi.id, "document.coo")
        values = dict(
            pi_id=self.pi.id,
            scope="ORDER",
            task_code="DOCUMENT_COO",
            title="COO",
            source=TaskSource.AUTO,
            status=TaskStatus.ACTION,
            health=TaskHealth.NORMAL,
            completion_mode=CompletionMode.MANUAL,
            priority=100,
            dedupe_key=key,
            created_at=NOW,
            updated_at=NOW,
        )
        db.session.add(OrderTask(**values))
        db.session.commit()
        db.session.add(OrderTask(**values))
        with self.assertRaises(IntegrityError):
            db.session.commit()

    def test_31_dry_run_does_not_write(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        actions = reconcile_order_tasks(self.pi, now=NOW, apply=False)
        self.assertTrue(any(action.task_code == "DOCUMENT_COO" for action in actions))
        self.assertEqual(OrderTask.query.count(), 0)

    def test_32_apply_writes_expected_rows(self):
        self.pi.coo_required = "需要"
        self.pi.container_date = NOW.date()
        db.session.commit()
        actions = self.apply()
        self.assertTrue(any(action.task_code == "DOCUMENT_COO" for action in actions))
        document_task = self.task("DOCUMENT_COO")
        self.assertIsNotNone(document_task)
        self.assertEqual(document_task.activities[0].event_type, ActivityEvent.CREATED)

    def test_33_new_boolean_overrides_legacy_coa(self):
        self.pi.coa_status = "需要"
        self.pi.coa_required = False
        self.pi.container_date = NOW.date()
        db.session.commit()
        self.apply()
        self.assertIsNone(self.task("DOCUMENT_COA"))

    def test_34_ambiguous_legacy_insurance_does_not_create_two_tasks(self):
        self.pi.insurance_status = "需要"
        self.pi.status = "已发运"
        db.session.commit()
        self.apply()
        self.assertIsNone(self.task("DOCUMENT_INSURANCE_ORIGINAL"))
        self.assertIsNone(self.task("DOCUMENT_INSURANCE_ELECTRONIC"))

    def test_35_legacy_mail_done_imports_existing_tracking_as_history_payload(self):
        self.pi.document_shipping_status = "已邮寄"
        self.pi.tracking_number = "LEGACY-TRACK"
        db.session.commit()
        self.apply()
        task = self.task("DOCUMENT_ORIGINALS_MAIL")
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertIsNone(task.completed_at)
        self.assertEqual(task.activities[-1].payload, {"tracking_number": "LEGACY-TRACK"})

    def test_36_telex_required_fact_has_no_phase_2a_task(self):
        self.pi.telex_release_required = True
        db.session.commit()
        reconcile_order_tasks(self.pi, now=NOW, apply=True, rule_definitions=DOCUMENT_RULES)
        db.session.commit()
        self.assertFalse(any(task.task_code.startswith("DOCUMENT_TELEX") for task in OrderTask.query.all()))

    def test_37_tri_state_form_updates_explicit_values(self):
        update_document_required_facts(
            self.pi,
            MultiDict(
                {
                    "coc_required": "true",
                    "coa_required": "false",
                    "original_bl_required": "",
                }
            ),
        )
        self.assertIs(self.pi.coc_required, True)
        self.assertIs(self.pi.coa_required, False)
        self.assertIsNone(self.pi.original_bl_required)

    def test_38_omitted_tri_state_form_value_is_preserved(self):
        self.pi.coc_required = True
        update_document_required_facts(self.pi, MultiDict({"coa_required": "false"}))
        self.assertIs(self.pi.coc_required, True)

    def test_39_invalid_tri_state_form_value_is_rejected(self):
        with self.assertRaises(ValueError):
            update_document_required_facts(
                self.pi, MultiDict({"coc_required": "已完成"})
            )


if __name__ == "__main__":
    unittest.main()
