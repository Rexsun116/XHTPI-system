from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from tests import test_support  # noqa: F401

from sqlalchemy import text

from app import Customer, Exporter, Factory, PI, PIItem, Product, User, app, db, update_payment_facts
from reminders.adapters import legacy_settlement_fact, legacy_telex_done
from reminders.engine import reconcile_order_tasks
from reminders.enums import CompletionMode, TaskHealth, TaskStatus, WaitingOn
from reminders.payment import (
    assess_payment,
    contract_total,
    is_payment_fully_received,
    suggested_advance_amount,
)
from reminders.rules.documents import DOCUMENT_RULES
from reminders.rules.payments import PAYMENT_RULES
from reminders.task_service import (
    InvalidTransitionError,
    add_follow_up,
    create_auto_task,
    mark_done,
    move_to_waiting,
    reconcile_task_timing,
)
from task_models import OrderTask
from werkzeug.datastructures import MultiDict


NOW = datetime(2026, 8, 21, 10, 0)


class PaymentRulesTestCase(unittest.TestCase):
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
        user = User(username="payment-tester", role="admin")
        user.set_password("password")
        customer = Customer(code="C-PAY", name="Rahul Minerals", address="A", country="IN")
        exporter = Exporter(code="E-PAY", name="Exporter", address="A", country="CN")
        factory = Factory(code="F-PAY", name="Factory", country="CN")
        product = Product(code="P-PAY", model="R-996")
        db.session.add_all([user, customer, exporter, factory, product])
        db.session.flush()
        self.pi = PI(
            pi_no="PAY-001", pi_date=NOW.date(), customer_id=customer.id,
            exporter_id=exporter.id, status="已发运", customer_name_snapshot="Rahul Minerals",
        )
        db.session.add(self.pi)
        db.session.flush()
        db.session.add(PIItem(pi_id=self.pi.id, product_id=product.id, factory_id=factory.id, total_price=61700.0))
        db.session.commit()
        self.user_id = user.id

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.context.pop()

    def apply(self, now=NOW, rules=PAYMENT_RULES):
        actions = reconcile_order_tasks(self.pi, now=now, apply=True, rule_definitions=rules)
        db.session.commit()
        return actions

    def task(self, code):
        return OrderTask.query.filter_by(pi_id=self.pi.id, task_code=code).one_or_none()

    def set_plan(self, *, percent=20, advance=12340, balance=49360, advance_received=0, balance_received=0):
        self.pi.currency = "USD"
        self.pi.advance_payment_percent = Decimal(str(percent))
        self.pi.advance_payment_amount = Decimal(str(advance))
        self.pi.advance_received_amount = Decimal(str(advance_received))
        self.pi.balance_payment_amount = Decimal(str(balance))
        self.pi.balance_received_amount = Decimal(str(balance_received))
        db.session.commit()

    def completed_source_task(self, code, completed_at):
        task = create_auto_task(
            pi_id=self.pi.id, task_code=code, title=code, rule_key=f"test.{code}",
            rule_version=1, instance_key="default", dedupe_key=f"TEST:{self.pi.id}:{code}",
            status=TaskStatus.ACTION, health=TaskHealth.NORMAL,
            completion_mode=CompletionMode.MANUAL, now=completed_at - timedelta(minutes=1),
        )
        mark_done(task, actor_id=self.user_id, now=completed_at)
        db.session.commit()
        return task

    def test_01_currency_nullable_and_numeric_roundtrip(self):
        self.assertIsNone(self.pi.currency)
        self.set_plan(percent=Decimal("20.50"), advance=Decimal("12648.50"))
        db.session.expire_all()
        self.assertEqual(self.pi.advance_payment_percent, Decimal("20.50"))
        self.assertEqual(self.pi.advance_payment_amount, Decimal("12648.50"))

    def test_02_decimal_calculation_and_contract_total(self):
        self.assertEqual(contract_total(self.pi), Decimal("61700.00"))
        self.assertEqual(suggested_advance_amount(Decimal("61700"), Decimal("20")), Decimal("12340.00"))

    def test_03_hundred_percent_advance_has_zero_balance(self):
        self.pi.advance_payment_percent = Decimal("100")
        assessment = assess_payment(self.pi)
        self.assertEqual(assessment.advance_expected, Decimal("61700.00"))
        self.assertEqual(assessment.balance_expected, Decimal("0.00"))

    def test_04_structured_full_payment(self):
        self.set_plan(advance_received=12340, balance_received=49360)
        self.assertTrue(is_payment_fully_received(self.pi))

    def test_05_legacy_paid_in_full_fallback(self):
        self.pi.payment_received = "已收齐"
        db.session.commit()
        assessment = assess_payment(self.pi)
        self.assertTrue(assessment.fully_received)
        self.assertEqual(assessment.full_payment_source, "LEGACY_PAID_IN_FULL")

    def test_06_structured_legacy_conflict_is_reported(self):
        self.set_plan(advance_received=12340, balance_received=0)
        self.pi.payment_received = "已收齐"
        db.session.commit()
        assessment = assess_payment(self.pi)
        self.assertFalse(assessment.fully_received)
        self.assertIn("LEGACY_STRUCTURED_PAYMENT_CONFLICT", assessment.warnings)

    def test_07_email_only_sets_follow_up_base_and_three_days(self):
        self.set_plan(advance_received=12340)
        self.completed_source_task("PAYMENT_EMAIL", NOW - timedelta(days=3))
        self.apply()
        task = self.task("PAYMENT_BALANCE_FOLLOWUP")
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.context_payload["base_date"], (NOW - timedelta(days=3)).date().isoformat())

    def test_08_mail_only_sets_follow_up_base(self):
        self.set_plan(advance_received=12340)
        self.completed_source_task("DOCUMENT_ORIGINALS_MAIL", NOW - timedelta(days=3))
        self.apply()
        self.assertEqual(self.task("PAYMENT_BALANCE_FOLLOWUP").status, TaskStatus.ACTION)

    def test_09_both_sources_use_earliest(self):
        self.set_plan(advance_received=12340)
        early = NOW - timedelta(days=5)
        self.completed_source_task("PAYMENT_EMAIL", early)
        self.completed_source_task("DOCUMENT_ORIGINALS_MAIL", NOW - timedelta(days=3))
        self.apply()
        self.assertEqual(self.task("PAYMENT_BALANCE_FOLLOWUP").context_payload["base_date"], early.date().isoformat())

    def test_10_no_completed_source_no_follow_up(self):
        self.set_plan(advance_received=12340)
        self.apply()
        self.assertIsNone(self.task("PAYMENT_BALANCE_FOLLOWUP"))

    def test_11_before_three_calendar_days_is_upcoming(self):
        self.set_plan(advance_received=12340)
        self.completed_source_task("PAYMENT_EMAIL", NOW - timedelta(days=2))
        self.apply()
        self.assertEqual(self.task("PAYMENT_BALANCE_FOLLOWUP").status, TaskStatus.UPCOMING)

    def test_12_full_payment_auto_resolves_open_follow_up(self):
        self.set_plan(advance_received=12340)
        self.completed_source_task("PAYMENT_EMAIL", NOW - timedelta(days=3))
        self.apply()
        self.pi.balance_received_amount = Decimal("49360")
        db.session.commit()
        self.apply(now=NOW + timedelta(minutes=1))
        task = self.task("PAYMENT_BALANCE_FOLLOWUP")
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.resolution_code, "PAYMENT_FULLY_RECEIVED")

    def test_13_balance_follow_up_cannot_manual_done(self):
        self.set_plan(advance_received=12340)
        self.completed_source_task("PAYMENT_EMAIL", NOW - timedelta(days=3))
        self.apply()
        with self.assertRaises(InvalidTransitionError):
            mark_done(self.task("PAYMENT_BALANCE_FOLLOWUP"), actor_id=self.user_id, now=NOW)

    def test_14_follow_up_waiting_due_and_history_preserved(self):
        self.set_plan(advance_received=12340)
        self.completed_source_task("PAYMENT_EMAIL", NOW - timedelta(days=3))
        self.apply()
        task = self.task("PAYMENT_BALANCE_FOLLOWUP")
        move_to_waiting(
            task, actor_id=self.user_id, waiting_on=WaitingOn.CUSTOMER,
            next_follow_up_at=NOW + timedelta(days=1), note="First reminder", now=NOW,
        )
        add_follow_up(
            task, actor_id=self.user_id, note="Second reminder",
            next_follow_up_at=NOW + timedelta(days=2), now=NOW + timedelta(hours=1),
        )
        db.session.commit()
        self.apply(now=NOW + timedelta(days=1))
        self.assertEqual(task.status, TaskStatus.WAITING)
        reconcile_task_timing(tasks=[task], now=NOW + timedelta(days=3))
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.health, TaskHealth.OVERDUE)
        self.assertEqual([a.note for a in task.activities if a.note and "reminder" in a.note], ["First reminder", "Second reminder"])

    def test_15_advance_settlement_trigger(self):
        self.set_plan(advance_received=12340)
        self.pi.advance_received_at = NOW
        self.pi.settlement_documents_required = "需要"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("SETTLEMENT_DOCUMENT_ADVANCE").status, TaskStatus.ACTION)
        self.assertEqual(self.task("SETTLEMENT_DOCUMENT_BALANCE").status, TaskStatus.UPCOMING)

    def test_16_balance_settlement_trigger_and_independent_dedupe(self):
        self.set_plan(advance_received=12340, balance_received=49360)
        self.pi.advance_received_at = NOW - timedelta(days=1)
        self.pi.balance_received_at = NOW
        self.pi.settlement_documents_required = "需要"
        db.session.commit()
        self.apply()
        advance = self.task("SETTLEMENT_DOCUMENT_ADVANCE")
        balance = self.task("SETTLEMENT_DOCUMENT_BALANCE")
        self.assertEqual(balance.status, TaskStatus.ACTION)
        self.assertNotEqual(advance.dedupe_key, balance.dedupe_key)

    def test_17_hundred_percent_advance_has_no_balance_settlement(self):
        self.set_plan(percent=100, advance=61700, balance=0, advance_received=61700)
        self.pi.advance_received_at = NOW
        self.pi.settlement_documents_required = "需要"
        db.session.commit()
        self.apply()
        self.assertIsNone(self.task("SETTLEMENT_DOCUMENT_BALANCE"))

    def test_18_legacy_settlement_done_is_one_conservative_task(self):
        self.pi.settlement_documents_required = "已完成"
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("LEGACY_SETTLEMENT_DOCUMENT").status, TaskStatus.DONE)
        self.assertIsNone(self.task("SETTLEMENT_DOCUMENT_ADVANCE"))
        self.assertIsNone(self.task("SETTLEMENT_DOCUMENT_BALANCE"))
        self.assertTrue(legacy_settlement_fact("已完成").legacy_done)

    def test_19_telex_required_unpaid_is_upcoming(self):
        self.set_plan(advance_received=12340)
        self.pi.telex_release_required = True
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_TELEX_RELEASE").status, TaskStatus.UPCOMING)

    def test_20_telex_required_paid_is_action_then_manual_done_protected(self):
        self.set_plan(advance_received=12340, balance_received=49360)
        self.pi.telex_release_required = True
        db.session.commit()
        self.apply()
        task = self.task("DOCUMENT_TELEX_RELEASE")
        self.assertEqual(task.status, TaskStatus.ACTION)
        mark_done(task, actor_id=self.user_id, now=NOW)
        db.session.commit()
        self.apply(now=NOW + timedelta(days=1))
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_21_telex_false_does_not_create(self):
        self.pi.telex_release_required = False
        db.session.commit()
        self.apply()
        self.assertIsNone(self.task("DOCUMENT_TELEX_RELEASE"))

    def test_22_legacy_telex_done_requires_formal_required_fact(self):
        self.pi.telex_release = "已电放"
        db.session.commit()
        self.apply()
        self.assertIsNone(self.task("DOCUMENT_TELEX_RELEASE"))
        self.pi.telex_release_required = True
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("DOCUMENT_TELEX_RELEASE").resolution_code, "LEGACY_DONE")
        self.assertTrue(legacy_telex_done("已电放"))

    def test_23_reconcile_is_idempotent(self):
        self.set_plan(advance_received=12340)
        self.completed_source_task("PAYMENT_EMAIL", NOW - timedelta(days=3))
        self.apply()
        count = OrderTask.query.count()
        self.assertEqual(self.apply(), [])
        self.assertEqual(OrderTask.query.count(), count)

    def test_24_payment_context_has_amounts_customer_and_currency(self):
        self.set_plan(advance_received=12340, balance_received=20000)
        self.completed_source_task("PAYMENT_EMAIL", NOW - timedelta(days=3))
        self.apply()
        context = self.task("PAYMENT_BALANCE_FOLLOWUP").context_payload
        self.assertEqual(context["customer_name"], "Rahul Minerals")
        self.assertEqual(context["currency"], "USD")
        self.assertEqual(context["expected_amount"], "49360.00")
        self.assertEqual(context["received_amount"], "20000.00")
        self.assertEqual(context["outstanding_amount"], "29360.00")

    def test_25_document_payment_email_completion_is_candidate(self):
        self.set_plan(advance_received=12340)
        self.apply(rules=DOCUMENT_RULES)
        email = self.task("PAYMENT_EMAIL")
        mark_done(email, actor_id=self.user_id, now=NOW - timedelta(days=3))
        db.session.commit()
        self.apply()
        self.assertEqual(self.task("PAYMENT_BALANCE_FOLLOWUP").status, TaskStatus.ACTION)

    def test_26_payment_form_updates_decimal_datetime_and_currency(self):
        update_payment_facts(
            self.pi,
            MultiDict(
                {
                    "currency": " usd ",
                    "advance_payment_percent": "20.50",
                    "advance_payment_amount": "12648.50",
                    "advance_received_amount": "10000.00",
                    "advance_received_at": "2026-08-21T09:30",
                    "balance_payment_amount": "49051.50",
                    "balance_received_amount": "",
                    "balance_received_at": "",
                }
            ),
        )
        self.assertEqual(self.pi.currency, "USD")
        self.assertEqual(self.pi.advance_payment_percent, Decimal("20.50"))
        self.assertEqual(self.pi.advance_received_at, datetime(2026, 8, 21, 9, 30))
        self.assertIsNone(self.pi.balance_received_amount)

    def test_27_payment_form_omission_preserves_existing_fact(self):
        self.pi.currency = "EUR"
        self.pi.advance_payment_amount = Decimal("100.00")
        update_payment_facts(self.pi, MultiDict({"balance_payment_amount": "200.00"}))
        self.assertEqual(self.pi.currency, "EUR")
        self.assertEqual(self.pi.advance_payment_amount, Decimal("100.00"))

    def test_28_payment_form_rejects_negative_or_percent_over_100(self):
        with self.assertRaises(ValueError):
            update_payment_facts(self.pi, MultiDict({"advance_received_amount": "-1"}))
        with self.assertRaises(ValueError):
            update_payment_facts(self.pi, MultiDict({"advance_payment_percent": "100.01"}))

    def test_29_advance_outstanding_is_waiting_with_amount(self):
        self.set_plan(advance_received=0)
        self.apply()
        task = self.task("PAYMENT_ADVANCE_WAITING")
        self.assertEqual(task.status, TaskStatus.WAITING)
        self.assertEqual(task.waiting_on, WaitingOn.CUSTOMER)
        self.assertEqual(task.context_payload["expected_amount"], "12340.00")
        self.assertEqual(task.context_payload["outstanding_amount"], "12340.00")

    def test_30_advance_waiting_partial_receipt_updates_and_full_receipt_resolves(self):
        self.set_plan(advance_received=1000)
        self.apply()
        task = self.task("PAYMENT_ADVANCE_WAITING")
        self.pi.advance_received_amount = Decimal("12340")
        db.session.commit()
        self.apply(now=NOW + timedelta(minutes=1))
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.resolution_code, "ADVANCE_PAYMENT_RECEIVED")

    def test_31_advance_waiting_cannot_manual_done(self):
        self.set_plan(advance_received=0)
        self.apply()
        with self.assertRaises(InvalidTransitionError):
            mark_done(self.task("PAYMENT_ADVANCE_WAITING"), actor_id=self.user_id, now=NOW)


if __name__ == "__main__":
    unittest.main()
