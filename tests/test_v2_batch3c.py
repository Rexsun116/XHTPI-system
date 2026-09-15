"""Batch 3C regressions on disposable V2 databases only."""
from datetime import date
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import event, update
from tests import test_v2_linked_shipment as fixture
from v2.models import PI, OrderTask, TaskActivity, OrderFreightAgreement, FreightSettlement, db
from v2.linked_shipment import record_linked_actual_departure, _claim_pair, LinkedShipmentError
from v2.services import reconcile_order_tasks_for_pi
from v2.presenter import present_task


class Batch3CTest(TestCase):
    setUp = fixture.LinkedShipmentTest.setUp
    tearDown = fixture.LinkedShipmentTest.tearDown
    client = fixture.LinkedShipmentTest.client
    pi = fixture.LinkedShipmentTest.pi
    pair = fixture.LinkedShipmentTest.pair
    snapshot = fixture.LinkedShipmentTest.snapshot
    departure = fixture.LinkedShipmentTest.departure

    def task(self, pi, code="SHIPPING_ETA_MISSING"):
        return db.session.scalar(db.select(OrderTask).where(
            OrderTask.dedupe_key == f"v2:order:{pi.id}:{code.lower()}"))

    def ordinary(self):
        pi = self.pi("ORDINARY")
        pi.advance_received_amount = Decimal("20")
        reconcile_order_tasks_for_pi(pi)
        pi.container_loading_date = date(2026, 9, 17)
        pi.container_location = "Factory"
        pi.container_type = "20GP"
        db.session.add(OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="Forwarder",
                                            currency="USD", amount=100))
        reconcile_order_tasks_for_pi(pi)
        db.session.commit()
        return pi

    def submit(self, pi, eta=""):
        return self.departure(pi, eta=eta, shipping_company="Carrier", bill_of_lading_number="BL123")

    def test_ordinary_eta_capture_and_form(self):
        pi = self.ordinary()
        page = self.client().get(f"/v2/orders/{pi.id}/enter-shipped").get_data(as_text=True)
        self.assertIn("ETA (optional)", page)
        self.assertIn('name="eta"', page)
        self.assertEqual(self.submit(pi, "2026-09-25").status_code, 302)
        self.assertEqual((pi.status, pi.eta), ("SHIPPED", date(2026, 9, 25)))
        self.assertEqual((pi.shipping_company, pi.bill_of_lading_number), ("Carrier", "BL123"))
        self.assertIsNone(self.task(pi))

    def test_ordinary_missing_eta_action_and_existing_editor(self):
        pi = self.ordinary()
        self.assertEqual(self.submit(pi).status_code, 302)
        task = self.task(pi)
        self.assertEqual((task.status, task.health, task.completion_mode), ("ACTION", "NORMAL", "RULE_DATA"))
        self.assertIsNone(self.task(pi, "SHIPPING_ACTUAL_ARRIVAL"))
        self.assertIn({"kind": "edit_schedule", "label": "Update ETA"}, present_task(task)["actions"])
        before = self.snapshot()
        page = self.client().get("/v2/").get_data(as_text=True)
        self.assertIn("ETA missing", page)
        self.assertIn(f'/v2/orders/{pi.id}#shipping-schedule', page)
        self.assertEqual(before, self.snapshot())
        response = self.client().post(f"/v2/orders/{pi.id}/eta", data={"eta": "2026-09-25"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(task.status, "DONE")
        self.assertEqual(task.resolution_code, "AUTO_RESOLVED")
        self.assertIsNotNone(self.task(pi, "SHIPPING_ACTUAL_ARRIVAL"))

    def test_blank_preserves_existing_eta_ordinary_and_linked(self):
        ordinary = self.ordinary()
        customer, export = self.pair()
        for pi in (ordinary, customer):
            pi.eta = date(2026, 9, 25)
            db.session.commit()
            self.assertEqual(self.submit(pi, " ").status_code, 302)
            self.assertEqual(pi.eta, date(2026, 9, 25))
            self.assertIsNone(self.task(pi))
        self.assertEqual(export.status, "COMPLETED")

    def test_invalid_eta_rolls_back_ordinary_and_linked(self):
        ordinary = self.ordinary()
        customer, export = self.pair()
        for pi in (ordinary, customer):
            for eta in ("invalid", "2026-09-19"):
                before = self.snapshot()
                self.assertIn(self.submit(pi, eta).status_code, (400, 409))
                self.assertEqual(before, self.snapshot())

    def test_linked_eta_only_on_customer_one_commit(self):
        customer, export = self.pair()
        commits = []
        session = db.session()
        def committed(session):
            commits.append(True)
        event.listen(session, "after_commit", committed)
        try:
            self.assertEqual(self.submit(customer, "2026-09-25").status_code, 302)
        finally:
            event.remove(session, "after_commit", committed)
        self.assertEqual(commits, [True])
        self.assertEqual((customer.status, export.status), ("SHIPPED", "COMPLETED"))
        self.assertEqual(customer.eta, date(2026, 9, 25))
        self.assertIsNone(export.eta)
        self.assertIsNone(self.task(customer))
        self.assertIsNone(self.task(export))

    def test_linked_missing_then_update_never_reopens_export(self):
        customer, export = self.pair()
        self.assertEqual(self.submit(customer).status_code, 302)
        self.assertEqual(self.task(customer).status, "ACTION")
        self.assertIsNone(self.task(export))
        before = {c.name: getattr(export, c.name) for c in PI.__table__.columns}
        self.assertEqual(self.client().post(f"/v2/orders/{customer.id}/eta", data={"eta": "2026-09-25"}).status_code, 302)
        self.assertEqual(self.task(customer).status, "DONE")
        self.assertEqual(before, {c.name: getattr(export, c.name) for c in PI.__table__.columns})
        self.assertIsNone(self.task(export))

    def test_linked_reconcile_failure_rolls_back_everything(self):
        customer, export = self.pair()
        before = self.snapshot()
        def fail(pi):
            reconcile_order_tasks_for_pi(pi)
            db.session.flush()
            raise RuntimeError("injected after task writes")
        with patch("v2.linked_shipment.reconcile_order_tasks_for_pi", side_effect=fail):
            with self.assertRaises(RuntimeError):
                record_linked_actual_departure(customer, date(2026, 9, 18), eta=date(2026, 9, 25))
        self.assertEqual(before, self.snapshot())

    def test_post_claim_schedule_refresh_and_no_preclaim_eta_write(self):
        customer, export = self.pair()
        before = self.snapshot()
        def claim(c, e):
            self.assertIsNone(c.eta)
            self.assertFalse(db.inspect(c).attrs.eta.history.has_changes())
            _claim_pair(c, e)
            # Deterministic database-side change leaves the loaded ETD stale.
            db.session.execute(update(PI).where(PI.id == c.id).values(etd=date(2026, 9, 30))
                               .execution_options(synchronize_session=False))
        with patch("v2.linked_shipment._claim_pair", side_effect=claim):
            self.assertEqual(self.submit(customer, "2026-09-25").status_code, 409)
        self.assertEqual(before, self.snapshot())

    def test_missing_eta_eligibility_and_idempotency(self):
        pi = self.pi("ELIGIBLE")
        for status in ("NEW", "PRE_SHIPMENT", "ARRIVED", "COMPLETED"):
            pi.status = status
            reconcile_order_tasks_for_pi(pi)
            self.assertIsNone(self.task(pi))
        pi.status = "SHIPPED"
        pi.order_type = "COMMISSION"
        reconcile_order_tasks_for_pi(pi)
        self.assertIsNone(self.task(pi))
        pi.order_type = "SALES"
        reconcile_order_tasks_for_pi(pi)
        db.session.commit()
        task = self.task(pi)
        count = len(task.activities)
        for _ in range(3):
            reconcile_order_tasks_for_pi(pi)
        db.session.commit()
        self.assertEqual(len(task.activities), count)
        self.assertEqual(task.status, "ACTION")

    def test_freight_variance_terminal_cancellation_preserves_history(self):
        pi = self.ordinary()
        pi.status = "ARRIVED"
        pi.balance_received_amount = Decimal("100")
        pi.telex_release_required = pi.original_documents_mail_required = False
        settlement = FreightSettlement(pi_id=pi.id, usd_bill_required=True,
                                       usd_bill_amount=120, usd_payment_status="PAID")
        db.session.add(settlement)
        reconcile_order_tasks_for_pi(pi)
        db.session.commit()
        task = self.task(pi, "FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE")
        self.assertEqual((task.status, task.health), ("ACTION", "EXCEPTION"))
        history = list(db.session.execute(TaskActivity.__table__.select()).tuples())
        context = dict(task.context_payload)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/enter-completed").status_code, 302)
        self.assertEqual((pi.status, task.status), ("COMPLETED", "CANCELLED"))
        self.assertEqual(task.resolution_code, "ORDER_COMPLETED_FREIGHT_VARIANCE_RETIRED")
        self.assertEqual(task.context_payload, context)
        self.assertEqual(settlement.usd_bill_amount, Decimal("120"))
        self.assertEqual(db.session.scalar(db.select(OrderFreightAgreement.amount).where(OrderFreightAgreement.pi_id == pi.id)), Decimal("100"))
        after = list(db.session.execute(TaskActivity.__table__.select()).tuples())
        self.assertTrue(all(row in after for row in history))
        cancellation = db.session.scalar(db.select(TaskActivity).where(TaskActivity.task_id == task.id,
                                                                       TaskActivity.event_type == "CANCELLED"))
        self.assertEqual(cancellation.note, task.resolution_code)
        for _ in range(3):
            reconcile_order_tasks_for_pi(pi)
        db.session.commit()
        self.assertEqual(after, list(db.session.execute(TaskActivity.__table__.select()).tuples()))
        self.assertEqual(task.status, "CANCELLED")

    def test_ordinary_failure_after_reconciliation_rolls_back_eta_and_departure(self):
        pi = self.ordinary()
        before = self.snapshot()
        def fail(row, **kwargs):
            reconcile_order_tasks_for_pi(row, **kwargs)
            db.session.flush()
            raise ValueError("injected reconciliation failure")
        with patch("v2.services.reconcile_order_tasks_for_pi", side_effect=fail):
            self.assertEqual(self.submit(pi, "2026-09-25").status_code, 400)
        self.assertEqual(before, self.snapshot())

    def test_completed_export_retires_existing_variance_without_creating_new_one(self):
        customer, export = self.pair()
        task = OrderTask(pi_id=export.id, task_code="FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE",
                         title="Historical variance", source="AUTO", status="ACTION", health="EXCEPTION",
                         completion_mode="RULE_DATA",
                         dedupe_key=f"v2:order:{export.id}:freight_bill_differs_from_agreed_quote",
                         context_payload={"difference": "20.00"})
        db.session.add(task)
        db.session.commit()
        self.assertEqual(self.submit(customer).status_code, 302)
        self.assertEqual(task.status, "CANCELLED")
        self.assertEqual(task.resolution_code, "ORDER_COMPLETED_FREIGHT_VARIANCE_RETIRED")
        self.assertEqual(task.context_payload, {"difference": "20.00"})
        self.assertIsNone(self.task(customer, "FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE"))
        self.assertIsNone(self.task(export))
