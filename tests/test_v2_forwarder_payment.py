"""Freight-forwarder payment uses the first actual B/L receipt date."""
from datetime import date, datetime
from unittest import TestCase

from tests import test_v2_linked_shipment as fixture
from v2.models import FreightSettlement, OrderTask, db
from v2.services import reconcile_order_tasks_for_pi
from v2.task_service import mark_done


class ForwarderPaymentTest(TestCase):
    setUp = fixture.LinkedShipmentTest.setUp
    tearDown = fixture.LinkedShipmentTest.tearDown
    client = fixture.LinkedShipmentTest.client
    pi = fixture.LinkedShipmentTest.pi
    pair = fixture.LinkedShipmentTest.pair

    def order(self, number="PAY"):
        pi = self.pi(number)
        pi.status = "SHIPPED"
        pi.obd_electronic_required = pi.original_bl_required = True
        self.settlement = FreightSettlement(
            pi_id=pi.id, usd_bill_required=True, usd_invoice_issued=True,
        )
        db.session.add(self.settlement)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 1))
        db.session.commit()
        return pi

    def task(self, pi, code):
        return db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == pi.id, OrderTask.task_code == code,
        ))

    def record_bl(self, pi, code, received_on):
        task = self.task(pi, code)
        self.assertIsNotNone(task)
        response = self.client().post(
            f"/v2/tasks/{task.id}/done",
            data={"bill_of_lading_received_on": received_on},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(pi)
        return task

    def reconcile(self, pi, when):
        reconcile_order_tasks_for_pi(pi, now=when)
        db.session.commit()
        return self.task(pi, "FREIGHT_FORWARDER_PAYMENT")

    def dashboard_task_form(self, task):
        page = self.client().get("/v2/").get_data(as_text=True)
        marker = f'action="/v2/tasks/{task.id}/done"'
        start = page.index(marker)
        return page[start:page.index("</form>", start) + len("</form>")]

    def payment_task_due(self, pi):
        self.record_bl(pi, "DOCUMENT_OBD_BL", "2026-09-01")
        return self.reconcile(pi, datetime(2026, 9, 26))

    def test_obd_receipt_date_sets_due_date_plus_25_calendar_days(self):
        pi = self.order()
        self.record_bl(pi, "DOCUMENT_OBD_BL", "2026-09-01")
        self.assertEqual(pi.bill_of_lading_received_on, date(2026, 9, 1))
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 25)).status, "UPCOMING")
        payment = self.reconcile(pi, datetime(2026, 9, 26))
        self.assertEqual((payment.status, payment.context_payload["bl_done_date"],
                          payment.context_payload["payment_due_date"]),
                         ("ACTION", "2026-09-01", "2026-09-26"))

    def test_original_bl_receipt_date_sets_due_date_plus_25_calendar_days(self):
        pi = self.order()
        self.record_bl(pi, "DOCUMENT_ORIGINAL_BL", "2026-09-03")
        payment = self.reconcile(pi, datetime(2026, 9, 28))
        self.assertEqual((payment.status, payment.context_payload["payment_due_date"]),
                         ("ACTION", "2026-09-28"))

    def test_earliest_of_both_receipt_dates_wins_and_later_receipt_never_moves_due(self):
        pi = self.order()
        self.record_bl(pi, "DOCUMENT_ORIGINAL_BL", "2026-09-03")
        first = self.reconcile(pi, datetime(2026, 9, 10))
        first_id = first.id
        self.record_bl(pi, "DOCUMENT_OBD_BL", "2026-09-01")
        self.assertEqual(pi.bill_of_lading_received_on, date(2026, 9, 1))
        payment = self.reconcile(pi, datetime(2026, 9, 26))
        self.assertEqual((payment.id, payment.status, payment.context_payload["payment_due_date"]),
                         (first_id, "ACTION", "2026-09-26"))

    def test_later_second_bl_receipt_does_not_move_the_first_receipt_clock(self):
        pi = self.order()
        self.record_bl(pi, "DOCUMENT_OBD_BL", "2026-09-01")
        self.record_bl(pi, "DOCUMENT_ORIGINAL_BL", "2026-09-05")
        self.assertEqual(pi.bill_of_lading_received_on, date(2026, 9, 1))
        payment = self.reconcile(pi, datetime(2026, 9, 26))
        self.assertEqual(payment.context_payload["payment_due_date"], "2026-09-26")
        stable_id = payment.id
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 26)).id, stable_id)

    def test_historical_business_date_never_uses_current_completion_timestamp(self):
        pi = self.order()
        self.record_bl(pi, "DOCUMENT_OBD_BL", "2020-01-01")
        payment = self.reconcile(pi, datetime(2026, 9, 30, 12))
        self.assertEqual((payment.status, payment.context_payload["bl_done_date"],
                          payment.context_payload["payment_due_date"]),
                         ("ACTION", "2020-01-01", "2020-01-26"))

    def test_done_task_without_business_date_does_not_fabricate_a_reminder(self):
        pi = self.order()
        task = self.task(pi, "DOCUMENT_OBD_BL")
        mark_done(task, self.user.id)
        db.session.commit()
        self.assertIsNone(pi.bill_of_lading_received_on)
        self.assertIsNone(self.reconcile(pi, datetime(2026, 10, 1)))

    def test_requires_existing_issued_invoice_precondition(self):
        pi = self.order()
        self.settlement.usd_invoice_issued = False
        self.record_bl(pi, "DOCUMENT_OBD_BL", "2026-09-01")
        self.assertIsNone(self.reconcile(pi, datetime(2026, 9, 26)))
        self.settlement.usd_invoice_issued = True
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 26)).status, "ACTION")
        self.settlement.usd_invoice_issued = False
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 27)).status, "CANCELLED")

    def test_required_usd_payment_derives_done_and_is_stable(self):
        pi = self.order()
        payment = self.payment_task_due(pi)
        self.assertEqual(payment.status, "ACTION")
        self.settlement.usd_payment_status = "PAID"
        self.settlement.usd_paid_at = datetime(2026, 9, 26, 9)
        payment = self.reconcile(pi, datetime(2026, 9, 26))
        self.assertEqual((payment.status, payment.completion_mode), ("DONE", "RULE_DATA"))
        stable_id, activities = payment.id, len(payment.activities)
        payment = self.reconcile(pi, datetime(2026, 10, 1))
        self.assertEqual((payment.id, payment.status, len(payment.activities)),
                         (stable_id, "DONE", activities))

    def test_required_cny_payment_derives_done(self):
        pi = self.order()
        self.settlement.usd_bill_required = False
        self.settlement.usd_invoice_issued = None
        self.settlement.cny_bill_required = True
        self.settlement.cny_invoice_issued = True
        payment = self.payment_task_due(pi)
        self.assertEqual(payment.status, "ACTION")
        self.settlement.cny_payment_status = "PAID"
        self.settlement.cny_paid_at = datetime(2026, 9, 26, 9)
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 26)).status, "DONE")

    def test_both_required_branches_need_payment_and_non_required_branch_does_not_block(self):
        pi = self.order()
        self.settlement.cny_bill_required = True
        self.settlement.cny_invoice_issued = True
        payment = self.payment_task_due(pi)
        self.settlement.usd_payment_status = "PAID"
        self.settlement.usd_paid_at = datetime(2026, 9, 26, 9)
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 26)).status, "ACTION")
        self.settlement.cny_payment_status = "PAID"
        self.settlement.cny_paid_at = datetime(2026, 9, 26, 10)
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 26)).status, "DONE")

        other = self.order("NONREQUIRED")
        self.settlement.usd_payment_status = "PAID"
        self.settlement.usd_paid_at = datetime(2026, 9, 26, 9)
        self.settlement.cny_bill_required = False
        self.settlement.cny_payment_status = "UNPAID"
        self.assertEqual(self.payment_task_due(other).status, "DONE")
        self.assertEqual(payment.id, self.task(pi, "FREIGHT_FORWARDER_PAYMENT").id)

    def test_manual_done_endpoint_cannot_bypass_unpaid_freight(self):
        pi = self.order()
        payment = self.payment_task_due(pi)
        response = self.client().post(f"/v2/tasks/{payment.id}/done")
        self.assertEqual(response.status_code, 400)
        db.session.refresh(payment)
        self.assertEqual((payment.status, payment.completion_mode), ("ACTION", "RULE_DATA"))

    def test_freight_settlement_payment_post_reconciles_to_done(self):
        pi = self.order()
        payment = self.payment_task_due(pi)
        response = self.client().post(f"/v2/orders/{pi.id}/facts", data={
            "_form_scope": "FREIGHT_SETTLEMENT",
            "return_to": "freight-settlement",
            "usd_payment_status": "PAID",
            "usd_paid_at": "2026-09-26",
        })
        self.assertEqual(response.status_code, 302)
        db.session.refresh(payment)
        self.assertEqual(payment.status, "DONE")

    def test_completed_order_does_not_hide_unpaid_freight_reminder(self):
        pi = self.order()
        self.assertEqual(self.payment_task_due(pi).status, "ACTION")
        pi.status = "COMPLETED"
        self.assertEqual(self.reconcile(pi, datetime(2026, 9, 27)).status, "ACTION")

    def test_linked_export_never_gets_the_customer_payment_reminder(self):
        customer, export = self.pair()
        customer.status = "SHIPPED"
        customer.obd_electronic_required = True
        settlement = FreightSettlement(
            pi_id=customer.id, usd_bill_required=True, usd_invoice_issued=True,
        )
        db.session.add(settlement)
        reconcile_order_tasks_for_pi(customer, now=datetime(2026, 9, 1))
        db.session.commit()
        self.record_bl(customer, "DOCUMENT_OBD_BL", "2026-09-01")
        self.assertEqual(self.reconcile(customer, datetime(2026, 9, 26)).status, "ACTION")
        self.assertIsNone(self.reconcile(export, datetime(2026, 9, 26)))

    def test_bl_completion_form_requires_and_persists_a_labeled_native_date(self):
        pi = self.order()
        task = self.task(pi, "DOCUMENT_OBD_BL")
        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertIn('name="bill_of_lading_received_on"', page)
        self.assertIn('type="date"', page)
        self.assertIn("B/L Received Date", page)
        missing = self.client().post(f"/v2/tasks/{task.id}/done", data={})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(task.status, "ACTION")

    def test_dashboard_obd_completion_exposes_and_submits_bl_received_date(self):
        pi = self.order()
        task = self.task(pi, "DOCUMENT_OBD_BL")

        form = self.dashboard_task_form(task)
        self.assertIn("B/L Received Date", form)
        self.assertIn('name="bill_of_lading_received_on"', form)
        self.assertIn('type="date"', form)
        self.assertIn('id="bl-received-dashboard-', form)

        response = self.client().post(
            f"/v2/tasks/{task.id}/done",
            data={"bill_of_lading_received_on": "2026-08-31"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(pi)
        self.assertEqual(pi.bill_of_lading_received_on, date(2026, 8, 31))

    def test_dashboard_original_bl_uses_the_same_date_completion_contract(self):
        pi = self.order()
        obd = self.task(pi, "DOCUMENT_OBD_BL")
        original = self.task(pi, "DOCUMENT_ORIGINAL_BL")

        self.assertIn('name="bill_of_lading_received_on"', self.dashboard_task_form(original))
        missing = self.client().post(f"/v2/tasks/{original.id}/done", data={})
        self.assertEqual(missing.status_code, 400)
        db.session.refresh(original)
        self.assertEqual(original.status, "ACTION")

        self.client().post(
            f"/v2/tasks/{original.id}/done",
            data={"bill_of_lading_received_on": "2026-09-03"},
        )
        self.client().post(
            f"/v2/tasks/{obd.id}/done",
            data={"bill_of_lading_received_on": "2026-09-01"},
        )
        db.session.refresh(pi)
        self.assertEqual(pi.bill_of_lading_received_on, date(2026, 9, 1))
        self.assertEqual(
            self.reconcile(pi, datetime(2026, 9, 26)).context_payload["payment_due_date"],
            "2026-09-26",
        )

    def test_dashboard_unrelated_done_task_does_not_expose_bl_received_date(self):
        pi = self.order()
        task = OrderTask(
            pi_id=pi.id, task_code="MANUAL_DASHBOARD_TASK", title="Manual dashboard task",
            source="MANUAL", status="ACTION", health="NORMAL", completion_mode="MANUAL",
            priority=1, dedupe_key="manual-dashboard-task",
        )
        db.session.add(task)
        db.session.commit()

        form = self.dashboard_task_form(task)
        self.assertNotIn("B/L Received Date", form)
        self.assertNotIn('name="bill_of_lading_received_on"', form)

    def test_dashboard_and_order_detail_direct_unpaid_freight_to_settlement(self):
        pi = self.order()
        payment = self.payment_task_due(pi)

        dashboard = self.client().get("/v2/").get_data(as_text=True)
        self.assertIn(f'href="/v2/orders/{pi.id}#freight-settlement">确认付款</a>', dashboard)
        self.assertNotIn(f'action="/v2/tasks/{payment.id}/done"', dashboard)

        order = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        task_section = order.split(f'id="task-{payment.id}"', 1)[1].split("</details>", 1)[0]
        self.assertIn('href="#freight-settlement">确认付款</a>', task_section)
        self.assertNotIn(f'action="/v2/tasks/{payment.id}/done"', task_section)
        self.assertIn('id="freight-settlement"', order)
