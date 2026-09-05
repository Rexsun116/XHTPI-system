"""ARRIVED -> COMPLETED stage-gate coverage using isolated V2 databases."""

from datetime import date, datetime
from decimal import Decimal
import re
import tempfile
from pathlib import Path
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import (Customer, Exporter, FreightSettlement, OrderFreightAgreement,
                       OrderTask, PI, PIItem, ProductBatch, TaskActivity, User, db)
from v2.services import completion_check, open_correction_session, reconcile_order_tasks_for_pi, save_order_with_reconcile
from v2.task_service import mark_done, reopen


class CompletionGateTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="completion", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="C", name="Customer")
        self.exporter = Exporter(code="E", name="Exporter")
        db.session.add_all([self.user, self.customer, self.exporter]); db.session.commit()

    def tearDown(self):
        db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def pi(self, *, order_type="SALES", **facts):
        facts.setdefault("advance_received_amount", Decimal("0"))
        facts.setdefault("balance_received_amount", Decimal("1000"))
        pi = PI(pi_no=f"COMP-{PI.query.count()+1}", pi_date=date(2026, 9, 1), order_type=order_type,
                status="ARRIVED", customer_id=self.customer.id, exporter_id=self.exporter.id,
                customer_name_snapshot="Customer", exporter_name_snapshot="Exporter", currency="USD",
                **facts)
        pi.items.append(PIItem(unit_price=Decimal("100"), quantity=Decimal("10"), quantity_unit="MT", line_total=Decimal("1000")))
        db.session.add(pi); db.session.flush(); return pi

    def task(self, pi, code, *, status="DONE", mode="MANUAL"):
        task = OrderTask(pi_id=pi.id, task_code=code, title=code, source="AUTO", status=status,
                         health="NORMAL", completion_mode=mode, dedupe_key=f"v2:order:{pi.id}:{code.lower()}")
        db.session.add(task); db.session.flush(); return task

    def completed_activity(self, task, tracking=None, *, created_at=None):
        payload = {"tracking_number": tracking} if tracking is not None else None
        row = TaskActivity(task_id=task.id, event_type="COMPLETED", from_status="ACTION", to_status="DONE",
                           actor_type="USER", actor_id=self.user.id, payload=payload, created_at=created_at)
        db.session.add(row); db.session.flush(); return row

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def complete_url(self, pi):
        return f"/v2/orders/{pi.id}/enter-completed"

    def test_payment_predicate_supports_exact_overpaid_balance_and_commission(self):
        unpaid = self.pi(balance_received_amount=Decimal("999.99"))
        self.assertFalse(completion_check(unpaid)["payment"]["complete"])
        exact = self.pi(balance_received_amount=Decimal("1000"))
        self.assertTrue(completion_check(exact)["payment"]["complete"])
        overpaid = self.pi(balance_received_amount=Decimal("1001"))
        self.assertTrue(completion_check(overpaid)["payment"]["complete"])
        split = self.pi(advance_received_amount=Decimal("200"), balance_received_amount=Decimal("800"))
        self.assertTrue(completion_check(split)["payment"]["complete"])
        commission = self.pi(order_type="COMMISSION", balance_received_amount=Decimal("1000"), commission_status="UNPAID")
        self.assertTrue(completion_check(commission)["payment"]["complete"])

    def test_freight_currency_requirements_are_independent(self):
        pi = self.pi()
        self.assertTrue(completion_check(pi)["freight"]["complete"])
        cases = [
            ({"usd_bill_required": True, "usd_payment_status": None}, False),
            ({"usd_bill_required": True, "usd_payment_status": "UNPAID"}, False),
            ({"usd_bill_required": True, "usd_payment_status": "PAID"}, True),
            ({"cny_bill_required": True, "cny_payment_status": "PAID"}, True),
            ({"usd_bill_required": True, "usd_payment_status": "PAID", "cny_bill_required": True, "cny_payment_status": "UNPAID"}, False),
            ({"usd_bill_required": True, "usd_payment_status": "PAID", "cny_bill_required": True, "cny_payment_status": "PAID"}, True),
            ({"usd_bill_required": False, "usd_payment_status": "UNPAID", "cny_bill_required": False, "cny_payment_status": "UNPAID"}, True),
        ]
        for values, expected in cases:
            settlement = FreightSettlement(pi_id=pi.id, **values)
            db.session.add(settlement); db.session.flush()
            self.assertEqual(completion_check(pi)["freight"]["complete"], expected)
            db.session.delete(settlement); db.session.flush()

    def test_document_delivery_requires_current_telex_and_mail_tracking(self):
        pi = self.pi(telex_release_required=True, original_documents_mail_required=True)
        self.assertFalse(completion_check(pi)["documents"]["complete"])
        self.task(pi, "DOCUMENT_TELEX_RELEASE")
        mail = self.task(pi, "ORIGINAL_DOCUMENTS_MAIL", mode="MANUAL_REQUIRED_INPUT")
        self.assertFalse(completion_check(pi)["documents"]["complete"])
        self.completed_activity(mail, " ")
        self.assertFalse(completion_check(pi)["documents"]["complete"])
        self.completed_activity(mail, "DHL-A")
        self.assertTrue(completion_check(pi)["documents"]["complete"])
        self.assertEqual(completion_check(pi)["documents"]["mail"]["tracking_number"], "DHL-A")

    def test_original_mail_reopen_and_latest_completion_evidence(self):
        pi = self.pi(original_documents_mail_required=True)
        mail = self.task(pi, "ORIGINAL_DOCUMENTS_MAIL", mode="MANUAL_REQUIRED_INPUT")
        self.completed_activity(mail, "TRACK-A")
        self.assertTrue(completion_check(pi)["documents"]["mail"]["complete"])
        reopen(mail, self.user.id, reason="Correction")
        self.assertFalse(completion_check(pi)["documents"]["mail"]["complete"])
        mark_done(mail, self.user.id, payload={"tracking_number": "TRACK-B"})
        self.assertTrue(completion_check(pi)["documents"]["mail"]["complete"])
        self.assertEqual(completion_check(pi)["documents"]["mail"]["tracking_number"], "TRACK-B")

    def test_original_mail_same_timestamp_uses_later_activity_id(self):
        pi = self.pi(original_documents_mail_required=True)
        mail = self.task(pi, "ORIGINAL_DOCUMENTS_MAIL", mode="MANUAL_REQUIRED_INPUT")
        same_second = datetime(2026, 9, 5, 9, 0)
        first = self.completed_activity(mail, "TRACK-A", created_at=same_second)
        second = self.completed_activity(mail, "TRACK-B", created_at=same_second)
        self.assertGreater(second.id, first.id)
        self.assertEqual(completion_check(pi)["documents"]["mail"]["tracking_number"], "TRACK-B")

    def test_document_requirements_not_required_pass_automatically(self):
        pi = self.pi(telex_release_required=False, original_documents_mail_required=False)
        documents = completion_check(pi)["documents"]
        self.assertTrue(documents["complete"])
        self.assertFalse(documents["telex"]["required"])
        self.assertFalse(documents["mail"]["required"])

    def test_stage_gate_allows_only_arrived_order_with_all_requirements(self):
        pi = self.pi()
        pickup = self.task(pi, "ARRIVAL_CUSTOMER_PICKUP", status="ACTION")
        unrelated = self.task(pi, "PAYMENT_EMAIL", status="ACTION")
        response = self.client().post(self.complete_url(pi))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pi.status, "COMPLETED")
        self.assertEqual((pickup.status, unrelated.status), ("ACTION", "ACTION"))

    def test_stage_gate_rejects_each_failed_group_and_generic_bypass(self):
        payment = self.pi(balance_received_amount=Decimal("1"))
        freight = self.pi(); db.session.add(FreightSettlement(pi_id=freight.id, usd_bill_required=True, usd_payment_status="UNPAID"))
        docs = self.pi(telex_release_required=True)
        client = self.client()
        for pi in (payment, freight, docs):
            self.assertEqual(client.post(self.complete_url(pi)).status_code, 409)
            self.assertEqual(pi.status, "ARRIVED")
            self.assertEqual(client.post(f"/v2/orders/{pi.id}/status", data={"status": "COMPLETED"}).status_code, 409)

    def test_stage_gate_csrf_lifecycle_and_repeat_protection(self):
        pi = self.pi(); client = self.client(); self.app.config["WTF_CSRF_ENABLED"] = True
        page = client.get(self.complete_url(pi)).get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        self.assertEqual(client.post(self.complete_url(pi)).status_code, 400)
        self.assertEqual(client.post(self.complete_url(pi), data={"csrf_token": token}).status_code, 302)
        self.assertEqual(client.post(self.complete_url(pi), data={"csrf_token": token}).status_code, 409)
        self.app.config["WTF_CSRF_ENABLED"] = False
        for status in ("NEW", "PRE_SHIPMENT", "SHIPPED"):
            other = self.pi(); other.status = status; db.session.commit()
            self.assertEqual(client.get(self.complete_url(other)).status_code, 409)
            self.assertEqual(client.post(self.complete_url(other)).status_code, 409)

    def test_completion_check_panel_and_failed_post_are_server_authoritative(self):
        pi = self.pi(balance_received_amount=Decimal("10"))
        html = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertIn("Completion Check", html)
        self.assertIn("Customer Payment", html)
        self.assertNotIn("确认订单完成</a>", html)
        self.assertEqual(self.client().post(self.complete_url(pi)).status_code, 409)
        self.assertEqual(pi.status, "ARRIVED")

    def test_completed_order_blocks_task_mutation_but_arrived_does_not(self):
        arrived = self.pi()
        active = self.task(arrived, "ARRIVAL_CUSTOMER_PICKUP", status="ACTION")
        self.assertEqual(self.client().post(f"/v2/tasks/{active.id}/waiting", data={"waiting_on": "CUSTOMER"}).status_code, 302)
        completed = self.pi(); completed.status = "COMPLETED"
        action = self.task(completed, "ARRIVAL_CUSTOMER_PICKUP", status="ACTION")
        done = self.task(completed, "DOCUMENT_COO")
        client = self.client()
        for task, action_name, data in ((action, "done", {}), (action, "waiting", {"waiting_on": "CUSTOMER"}),
                                        (action, "followup", {"waiting_on": "CUSTOMER", "note": "No"}),
                                        (done, "reopen", {"reason": "No"})):
            self.assertEqual(client.post(f"/v2/tasks/{task.id}/{action_name}", data=data).status_code, 409)
        self.assertEqual((action.status, done.status), ("ACTION", "DONE"))
        html = client.get(f"/v2/orders/{completed.id}").get_data(as_text=True)
        self.assertNotIn(f"/v2/tasks/{action.id}/done", html)
        self.assertNotIn(f"/v2/tasks/{done.id}/reopen", html)

    def test_advance_receipt_is_blocked_for_completed_and_dashboard_is_read_only(self):
        completed = self.pi(advance_received_amount=Decimal("0")); completed.status = "COMPLETED"
        task = self.task(completed, "PAYMENT_ADVANCE_WAITING", status="ACTION")
        db.session.commit()
        before = (completed.advance_received_amount, OrderTask.query.count(), TaskActivity.query.count())
        client = self.client()
        response = client.post(f"/v2/orders/{completed.id}/advance-receipt", data={
            "advance_received_amount": "100", "advance_received_at": "2026-09-05",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual((completed.advance_received_amount, OrderTask.query.count(), TaskActivity.query.count()), before)
        dashboard = client.get("/v2/").get_data(as_text=True)
        self.assertIn(completed.pi_no, dashboard)
        self.assertIn(task.title, dashboard)
        self.assertNotIn(f"/v2/orders/{completed.id}/advance-receipt", dashboard)

        arrived = self.pi(advance_received_amount=Decimal("0"))
        response = client.post(f"/v2/orders/{arrived.id}/advance-receipt", data={
            "advance_received_amount": "100", "advance_received_at": "2026-09-05",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(arrived.advance_received_amount, Decimal("100"))

    def test_arrived_payment_receipts_are_date_only_and_do_not_touch_shipping_facts(self):
        pi = self.pi(balance_received_amount=Decimal("0"))
        pi.actual_departure_date = date(2026, 8, 1)
        pi.actual_arrival_date = date(2026, 8, 20)
        task = self.task(pi, "PAYMENT_EMAIL", status="DONE")
        activity_time = datetime(2026, 9, 4, 13, 45)
        activity = self.completed_activity(task, created_at=activity_time)
        db.session.commit()
        response = self.client().post(f"/v2/orders/{pi.id}/facts", data={
            "balance_received_amount": "1000",
            "balance_received_at": "2026-09-05",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pi.balance_received_amount, Decimal("1000"))
        self.assertEqual(pi.balance_received_at, datetime(2026, 9, 5))
        self.assertEqual(pi.actual_departure_date, date(2026, 8, 1))
        self.assertEqual(pi.actual_arrival_date, date(2026, 8, 20))
        self.assertEqual(pi.status, "ARRIVED")
        self.assertEqual(activity.created_at, activity_time)
        self.assertTrue(completion_check(pi)["overall_complete"])
        html = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertIn('name="balance_received_at" type="date" value="2026-09-05"', html)
        self.assertNotIn('name="balance_received_at" type="datetime-local"', html)

    def test_advance_receipt_accepts_a_date_and_dashboard_uses_date_control(self):
        pi = self.pi(advance_received_amount=Decimal("0"))
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING", status="ACTION")
        dashboard = self.client().get("/v2/").get_data(as_text=True)
        self.assertIn(task.title, dashboard)
        self.assertIn('name="advance_received_at" type="date"', dashboard)
        response = self.client().post(f"/v2/orders/{pi.id}/advance-receipt", data={
            "advance_received_amount": "100", "advance_received_at": "2026-09-05",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pi.advance_received_at, datetime(2026, 9, 5))

    def test_arrived_freight_settlement_is_scoped_and_currency_branches_stay_independent(self):
        pi = self.pi(actual_departure_date=date(2026, 8, 1), actual_arrival_date=date(2026, 8, 20),
                     etd=date(2026, 8, 1), eta=date(2026, 8, 20),
                     advance_received_amount=Decimal("100"), balance_received_amount=Decimal("900"))
        settlement = FreightSettlement(pi_id=pi.id, usd_bill_required=True, cny_bill_required=True)
        db.session.add(settlement); db.session.commit()
        client = self.client()
        html = client.get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        freight_form = html.split('<form id="freight-settlement"', 1)[1].split('</form>', 1)[0]
        for name in ("actual_departure_date", "actual_arrival_date", "advance_received_amount",
                     "advance_received_at", "balance_received_amount", "balance_received_at"):
            self.assertNotIn(f'name="{name}"', freight_form)

        preserved = (pi.actual_departure_date, pi.actual_arrival_date, pi.etd, pi.eta,
                     pi.advance_received_amount, pi.balance_received_amount)
        for currency, amount in (("usd", "100"), ("cny", "700")):
            response = client.post(f"/v2/orders/{pi.id}/facts", data={
                "_form_scope": "FREIGHT_SETTLEMENT", f"{currency}_bill_amount": amount,
            })
            self.assertEqual(response.status_code, 302)
        self.assertEqual((settlement.usd_bill_amount, settlement.cny_bill_amount), (Decimal("100"), Decimal("700")))
        self.assertEqual((pi.actual_departure_date, pi.actual_arrival_date, pi.etd, pi.eta,
                          pi.advance_received_amount, pi.balance_received_amount), preserved)
        self.assertEqual(pi.status, "ARRIVED")

        for currency in ("USD", "CNY"):
            confirm = db.session.scalar(db.select(OrderTask).where(
                OrderTask.pi_id == pi.id, OrderTask.task_code == f"FREIGHT_{currency}_AMOUNT_CONFIRM"))
            self.assertEqual(client.post(f"/v2/tasks/{confirm.id}/done").status_code, 302)
            invoice = db.session.scalar(db.select(OrderTask).where(
                OrderTask.pi_id == pi.id, OrderTask.task_code == f"FREIGHT_{currency}_INVOICE_ISSUED"))
            self.assertEqual(client.post(f"/v2/tasks/{invoice.id}/done").status_code, 302)
            response = client.post(f"/v2/orders/{pi.id}/facts", data={
                "_form_scope": "FREIGHT_SETTLEMENT",
                f"{currency.lower()}_payment_status": "PAID",
                f"{currency.lower()}_paid_at": "2026-09-05",
            })
            self.assertEqual(response.status_code, 302)
            if currency == "USD":
                self.assertFalse(completion_check(pi)["freight"]["complete"])
                self.assertEqual(settlement.cny_payment_status, None)
                self.assertEqual(settlement.usd_paid_at, datetime(2026, 9, 5))
        self.assertTrue(completion_check(pi)["freight"]["complete"])
        self.assertEqual(settlement.cny_paid_at, datetime(2026, 9, 5))

    def test_completed_snapshot_renders_final_persisted_business_record(self):
        pi = self.pi(advance_received_amount=Decimal("200"), balance_received_amount=Decimal("800"),
                     advance_received_at=datetime(2026, 8, 2), balance_received_at=datetime(2026, 9, 5),
                     planned_shipment_date=date(2026, 8, 1), container_loading_date=date(2026, 8, 2),
                     container_loading_period="AM", container_location="Factory A", etd=date(2026, 8, 3),
                     eta=date(2026, 8, 20), actual_departure_date=date(2026, 8, 4),
                     actual_arrival_date=date(2026, 8, 21), shipping_company="Carrier A",
                     bill_of_lading_number="BL-006", booking_number="BOOK-006", shipping_mark="MARK-006",
                     container_type="40HQ", container_count=1, container_number="CONT-006", seal_number="SEAL-006",
                     gross_weight_kg=Decimal("1234.500"), volume_cbm=Decimal("12.300"), package_count=960,
                     package_unit="BAGS", driver_name="Li", driver_phone="13800000000", vehicle_number="沪A12345",
                     notify_party_name_snapshot="Notify Co", coo_required=True, telex_release_required=False,
                     original_documents_mail_required=False)
        pi.status = "COMPLETED"
        item = pi.items[0]
        item.product_model_snapshot = "Product-006"; item.product_hs_code_snapshot = "HS-006"
        item.factory_name_snapshot = "Factory A"; item.batches.append(ProductBatch(batch_number="BATCH-006"))
        agreement = OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="Forwarder A",
            amount=Decimal("450"), currency="USD", agreed_at=datetime(2026, 8, 1, 10), note="Accepted quote")
        settlement = FreightSettlement(pi_id=pi.id, usd_bill_required=True, usd_bill_amount=Decimal("500"),
            usd_bill_confirmed=True, usd_invoice_issued=True, usd_payment_status="PAID",
            usd_paid_at=datetime(2026, 9, 5, 16), cny_bill_required=True, cny_bill_amount=Decimal("700"),
            cny_bill_confirmed=True, cny_invoice_issued=True, cny_payment_status="PAID",
            cny_paid_at=datetime(2026, 9, 6, 9))
        task = self.task(pi, "ARRIVAL_CUSTOMER_PICKUP", status="ACTION")
        db.session.add_all([agreement, settlement]); db.session.commit()
        before = (pi.status, task.status, TaskActivity.query.count())
        html = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertEqual((pi.status, task.status, TaskActivity.query.count()), before)
        for text in (pi.pi_no, "Order Summary", "Items", "Document Requirements", "Product-006", "BATCH-006",
                     "2026-08-03 / 2026-08-20", "2026-08-04", "2026-08-21", "Carrier A",
                     "BL-006", "BOOK-006", "40HQ", "Forwarder A", "500", "700", "PAID", "2026-09-05",
                     "2026-09-06", "Document Requirements"):
            self.assertIn(text, html)
        self.assertNotIn("Completed Order Snapshot", html)
        self.assertEqual(html.count("Order Summary"), 1)
        self.assertEqual(html.count("Document Requirements"), 1)
        self.assertNotIn('name="usd_bill_amount"', html)
        self.assertNotIn('name="advance_received_amount"', html)
        self.assertIn(f"/v2/tasks/{task.id}/history", html)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data={
            "_form_scope": "FREIGHT_SETTLEMENT", "usd_bill_amount": "501",
        }).status_code, 403)

    def test_completed_correction_does_not_auto_reverse_lifecycle(self):
        pi = self.pi(); pi.status = "COMPLETED"; db.session.flush()
        correction = open_correction_session(pi, "PAYMENT", "Fix receipt", self.user.id)
        pi.balance_received_amount = Decimal("0")
        save_order_with_reconcile(pi)
        self.assertEqual(pi.status, "COMPLETED")
        self.assertIsNotNone(correction.id)

    def test_reconcile_never_auto_completes_arrived_order(self):
        pi = self.pi()
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5, 12))
        self.assertEqual(pi.status, "ARRIVED")
