"""Focused UAT coverage for NEW-sales reminders and dashboard task actions."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import re
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import (Customer, Exporter, FreightSettlement, OrderFreightAgreement,
                       OrderTask, PI, PIItem, ProductBatch, TaskActivity, User, db)
from v2.presenter import format_task_datetime, present_task
from v2.services import reconcile_order_tasks_for_pi
from v2.selector import projected
from v2.task_service import follow_up


class NewSalesReminderTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="new", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="C", name="ABC Customer")
        self.exporter = Exporter(code="E", name="Exporter")
        db.session.add_all([self.user, self.customer, self.exporter]); db.session.commit()

    def tearDown(self):
        db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def make_pi(self, *, planned, advance=Decimal("200"), received=Decimal("0")):
        pi = PI(pi_no=f"NEW-{PI.query.count()+1}", pi_date=date(2026, 8, 1), order_type="SALES", status="NEW",
                customer_id=self.customer.id, exporter_id=self.exporter.id, customer_name_snapshot="ABC Customer",
                exporter_name_snapshot="Exporter", currency="USD", payment_terms="20% advance",
                planned_shipment_date=planned, advance_payment_amount=advance, advance_received_amount=received,
                balance_payment_amount=Decimal("800"))
        pi.items.append(PIItem(unit_price=Decimal("100"), quantity=Decimal("10"), quantity_unit="MT",
                               line_total=Decimal("1000"), product_model_snapshot="R504"))
        db.session.add(pi); db.session.flush()
        return pi

    def task(self, pi, code):
        return db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == pi.id, OrderTask.task_code == code))

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def test_advance_waiting_then_etd_ten_and_followup_same_task(self):
        planned = date(2026, 9, 20); pi = self.make_pi(planned=planned)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 9, 9))
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING")
        self.assertEqual((task.status, task.waiting_on), ("WAITING", "CUSTOMER"))
        self.assertEqual(task.context_payload["outstanding_amount"], "200.00")
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 10, 0))
        self.assertEqual(task.status, "ACTION")
        follow_up(task, self.user.id, waiting_on="CUSTOMER", next_follow_up_at=datetime(2026, 9, 12), note="Customer will arrange")
        same_id = task.id; reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 11))
        self.assertEqual((task.id, task.status), (same_id, "WAITING"))
        self.assertEqual(len([a for a in task.activities if a.event_type == "FOLLOW_UP"]), 1)
        self.assertEqual(projected(task, datetime(2026, 9, 12))[0], "ACTION")

    def test_advance_resolves_and_unlocks_prep_at_minus_fifteen(self):
        planned = date(2026, 9, 20); pi = self.make_pi(planned=planned)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5))
        self.assertIsNone(self.task(pi, "SHIPPING_CONTAINER_LOADING"))
        pi.advance_received_amount = Decimal("200"); pi.advance_received_at = datetime(2026, 9, 5, 9)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5, 10))
        self.assertEqual(self.task(pi, "PAYMENT_ADVANCE_WAITING").status, "DONE")
        self.assertEqual(self.task(pi, "STAGE_GATE_PRE_SHIPMENT").status, "ACTION")
        self.assertIsNone(self.task(pi, "SHIPPING_CONTAINER_LOADING"))
        pi.status = "PRE_SHIPMENT"; reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5, 11))
        self.assertEqual(self.task(pi, "SHIPPING_CONTAINER_LOADING").status, "ACTION")
        self.assertEqual(self.task(pi, "SHIPPING_FREIGHT_AGREEMENT").status, "ACTION")

    def test_advance_overdue_is_single_exception_and_no_prep(self):
        pi = self.make_pi(planned=date(2026, 9, 20))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 21))
        advance = self.task(pi, "PAYMENT_ADVANCE_WAITING")
        self.assertEqual((advance.status, advance.health), ("ACTION", "EXCEPTION"))
        self.assertIsNone(self.task(pi, "SHIPPING_PLANNED_DATE_OVERDUE"))
        self.assertIsNone(self.task(pi, "SHIPPING_CONTAINER_LOADING"))
        labels = [x["label"] for x in present_task(advance)["actions"]]
        self.assertIn("Follow-up", labels); self.assertIn("修改计划发运日期", labels)

    def test_no_advance_prep_and_rule_data_resolution(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 4, 23, 59))
        self.assertIsNone(self.task(pi, "SHIPPING_CONTAINER_LOADING"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5))
        self.assertEqual(self.task(pi, "STAGE_GATE_PRE_SHIPMENT").status, "ACTION")
        pi.status = "PRE_SHIPMENT"; reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5))
        loading = self.task(pi, "SHIPPING_CONTAINER_LOADING"); agreement = self.task(pi, "SHIPPING_FREIGHT_AGREEMENT")
        self.assertEqual((loading.status, loading.completion_mode, agreement.status), ("ACTION", "RULE_DATA", "ACTION"))
        pi.container_loading_date = date(2026, 9, 20); db.session.add(OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="FF", amount=Decimal("100"), currency="USD"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5, 10))
        self.assertEqual((loading.status, agreement.status), ("DONE", "DONE"))
        pi.container_loading_date = None; reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5, 11))
        self.assertEqual(loading.status, "ACTION")

    def test_pre_shipment_shipped_gate_boundaries_and_incomplete_exception(self):
        planned = date(2026, 9, 20)
        pi = self.make_pi(planned=planned, advance=Decimal("0")); pi.status = "PRE_SHIPMENT"
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5))
        self.assertIsNone(self.task(pi, "STAGE_GATE_SHIPPED"))
        pi.container_loading_date = planned
        db.session.add(OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="FF", amount=Decimal("100"), currency="USD"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5, 1))
        self.assertEqual(self.task(pi, "SHIPPING_CONTAINER_LOADING").status, "DONE")
        self.assertEqual(self.task(pi, "SHIPPING_FREIGHT_AGREEMENT").status, "DONE")
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 19))
        gate = self.task(pi, "STAGE_GATE_SHIPPED"); self.assertEqual(gate.status, "UPCOMING")
        gate_id = gate.id
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 20))
        self.assertEqual((gate.id, gate.status, gate.health), (gate_id, "ACTION", "NORMAL"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 23))
        self.assertEqual((gate.status, gate.health), ("ACTION", "EXCEPTION"))
        pi.container_loading_date = None
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 23, 1))
        self.assertEqual((gate.status, gate.health), ("ACTION", "EXCEPTION"))
        self.assertIn("工厂装柜日期尚未确认", gate.context_payload["missing_preparation"])

    def test_enter_shipped_requires_gate_departure_carrier_and_bl(self):
        planned = date(2026, 9, 20)
        pi = self.make_pi(planned=planned, advance=Decimal("0")); pi.status = "PRE_SHIPMENT"
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5))
        pi.container_loading_date = planned
        db.session.add(OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="FF", amount=Decimal("100"), currency="USD"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 20))
        pi.shipping_company, pi.bill_of_lading_number = "Saved Carrier", "Saved BL"
        detail = self.client().get(f"/v2/orders/{pi.id}/enter-shipped").get_data(as_text=True)
        self.assertIn('value="Saved Carrier"', detail)
        self.assertIn('value="Saved BL"', detail)
        self.assertIn('name="csrf_token"', detail)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/enter-shipped", data={}).status_code, 400)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/status", data={"status": "SHIPPED"}).status_code, 409)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/enter-shipped", data={"actual_departure_date": "2026-09-20"}).status_code, 400)
        response = self.client().post(f"/v2/orders/{pi.id}/enter-shipped", data={
            "actual_departure_date": "2026-09-20", "shipping_company": "Carrier", "bill_of_lading_number": "BL-1",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual((pi.status, pi.actual_departure_date), ("SHIPPED", planned))
        self.assertEqual((pi.shipping_company, pi.bill_of_lading_number), ("Carrier", "BL-1"))
        self.assertEqual(self.task(pi, "STAGE_GATE_SHIPPED").status, "DONE")
        self.assertEqual(self.task(pi, "PAYMENT_EMAIL").status, "ACTION")

    def test_enter_arrived_renders_and_enforces_existing_csrf_convention(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        client = self.client()
        self.app.config["WTF_CSRF_ENABLED"] = True
        response = client.get(f"/v2/orders/{pi.id}/enter-arrived")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertIsNotNone(token)
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/enter-arrived", data={"actual_arrival_date": "2026-09-22"}).status_code, 400)
        valid = client.post(f"/v2/orders/{pi.id}/enter-arrived", data={
            "actual_arrival_date": "2026-09-22", "csrf_token": token.group(1),
        })
        self.assertEqual(valid.status_code, 302)
        self.assertEqual((pi.status, pi.actual_arrival_date), ("ARRIVED", date(2026, 9, 22)))

    def test_pre_shipment_has_one_freight_agreement_entry_and_independent_save_copy(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "PRE_SHIPMENT"
        html = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertEqual(html.count(f"/v2/orders/{pi.id}/freight-agreement"), 1)
        self.assertLess(html.index("Shipment Preparation · Freight"), html.index("Booking Cargo Information"))
        script = self.client().get("/v2/static/v2_batch1.js").get_data(as_text=True)
        self.assertIn("This section saves independently", script)

    def test_enter_arrived_requires_only_actual_arrival_and_creates_pickup_reminder(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        pi.actual_departure_date = date(2026, 9, 20)
        db.session.add(FreightSettlement(
            pi_id=pi.id, usd_bill_required=True, cny_bill_required=True,
            usd_invoice_issued=True, cny_invoice_issued=True,
            usd_payment_status="UNPAID", cny_payment_status="UNPAID",
        ))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 21))
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/status", data={"status": "ARRIVED"}).status_code, 409)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/enter-arrived", data={}).status_code, 400)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/enter-arrived", data={"actual_arrival_date": "2026-09-22"}).status_code, 302)
        self.assertEqual((pi.status, pi.actual_arrival_date), ("ARRIVED", date(2026, 9, 22)))
        pickup = self.task(pi, "ARRIVAL_CUSTOMER_PICKUP")
        self.assertEqual((pickup.status, pickup.source, pickup.completion_mode), ("ACTION", "AUTO", "MANUAL"))
        pickup_id = pickup.id; reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 22))
        self.assertEqual(self.task(pi, "ARRIVAL_CUSTOMER_PICKUP").id, pickup_id)

    def test_enter_arrived_link_is_visible_for_every_shipped_order_only(self):
        pi = self.make_pi(planned=date(2026, 9, 20)); pi.status = "SHIPPED"
        pi.coo_required = True
        db.session.add(FreightSettlement(pi_id=pi.id, usd_bill_required=True, cny_bill_required=True,
                                         usd_payment_status="UNPAID", cny_payment_status="UNPAID"))
        client = self.client()
        shipped = client.get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        enter_arrived = f"/v2/orders/{pi.id}/enter-arrived"
        self.assertIn(enter_arrived, shipped)
        self.assertIn("确认货物已到港", shipped)
        for status in ("PRE_SHIPMENT", "ARRIVED"):
            pi.status = status; db.session.commit()
            self.assertNotIn(enter_arrived, client.get(f"/v2/orders/{pi.id}").get_data(as_text=True))

    def _outbound_done(self, pi, *, email_at=None, mail_at=None):
        """Complete only the selected outbound task(s) at deterministic times."""
        if email_at:
            reconcile_order_tasks_for_pi(pi, now=email_at)
            task = self.task(pi, "PAYMENT_EMAIL")
            from v2.task_service import mark_done
            mark_done(task, self.user.id)
            task.completed_at = email_at
        if mail_at:
            pi.original_bl_required = True
            pi.original_documents_mail_required = True
            reconcile_order_tasks_for_pi(pi, now=mail_at)
            from v2.task_service import mark_done
            mark_done(self.task(pi, "DOCUMENT_ORIGINAL_BL"), self.user.id)
            reconcile_order_tasks_for_pi(pi, now=mail_at)
            task = self.task(pi, "ORIGINAL_DOCUMENTS_MAIL")
            mark_done(task, self.user.id, payload={"tracking_number": "DHL-1"})
            task.completed_at = mail_at

    def test_balance_followup_uses_payment_email_calendar_day_boundary(self):
        baseline = datetime(2026, 9, 1, 12)
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        self._outbound_done(pi, email_at=baseline)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 3, 12))
        task = self.task(pi, "PAYMENT_BALANCE_FOLLOWUP")
        self.assertEqual(task.status, "UPCOMING")
        self.assertEqual(task.context_payload["trigger_date"], "2026-09-04")
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 3, 16))
        self.assertEqual(task.status, "ACTION")

    def test_balance_followup_uses_original_mail_when_it_is_only_prerequisite(self):
        baseline = datetime(2026, 9, 1, 8)
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        self._outbound_done(pi, mail_at=baseline)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 4, 9))
        task = self.task(pi, "PAYMENT_BALANCE_FOLLOWUP")
        self.assertEqual((task.status, task.context_payload["base_completed_at"]), ("ACTION", baseline.isoformat()))

    def test_balance_followup_uses_earliest_outbound_completion_and_waiting_schedule(self):
        email_at, mail_at = datetime(2026, 9, 2, 18), datetime(2026, 9, 1, 8)
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        self._outbound_done(pi, email_at=email_at, mail_at=mail_at)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 4, 12))
        task = self.task(pi, "PAYMENT_BALANCE_FOLLOWUP")
        self.assertEqual((task.status, task.context_payload["base_completed_at"]), ("ACTION", mail_at.isoformat()))
        follow_up(task, self.user.id, waiting_on="CUSTOMER", next_follow_up_at=datetime(2026, 9, 6), note="Awaiting transfer")
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 5, 12))
        self.assertEqual(task.status, "WAITING")
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 6))
        self.assertEqual(task.status, "ACTION")
        pi.balance_received_amount = Decimal("1000")
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 6, 1))
        self.assertEqual(task.status, "DONE")

    def test_balance_followup_is_absent_without_done_outbound_task_or_when_paid_early(self):
        no_outbound = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); no_outbound.status = "SHIPPED"
        reconcile_order_tasks_for_pi(no_outbound, now=datetime(2026, 9, 20))
        self.assertIsNone(self.task(no_outbound, "PAYMENT_BALANCE_FOLLOWUP"))
        paid = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); paid.status = "SHIPPED"
        paid.balance_received_amount = Decimal("1000")
        self._outbound_done(paid, email_at=datetime(2026, 9, 1))
        reconcile_order_tasks_for_pi(paid, now=datetime(2026, 9, 4))
        self.assertIsNone(self.task(paid, "PAYMENT_BALANCE_FOLLOWUP"))

    def test_product_batch_crud_duplicate_and_order_lifecycle_protection(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        other = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); other.status = "SHIPPED"
        client = self.client()
        self.assertIn(f'id="batch-item-{pi.items[0].id}"', client.get(f"/v2/orders/{pi.id}").get_data(as_text=True))
        add = lambda number: client.post(f"/v2/orders/{pi.id}/batches", data={"pi_item_id": pi.items[0].id, "batch_number": number})
        self.assertEqual(add("B-1").status_code, 302)
        batch = pi.items[0].batches[0]
        edited = client.post(f"/v2/orders/{pi.id}/batches/{batch.id}/edit", data={"batch_number": "B-2"})
        self.assertEqual(edited.status_code, 302)
        self.assertTrue(edited.headers["Location"].endswith(f"#batch-item-{pi.items[0].id}"))
        self.assertEqual(batch.batch_number, "B-2")
        self.assertEqual(add("B-2").status_code, 302)
        self.assertEqual(ProductBatch.query.filter_by(pi_item_id=pi.items[0].id).count(), 1)
        self.assertEqual(client.post(f"/v2/orders/{other.id}/batches/{batch.id}/edit", data={"batch_number": "wrong"}).status_code, 404)
        pi.status = "PRE_SHIPMENT"; db.session.commit()
        self.assertEqual(add("blocked").status_code, 403)
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/batches/{batch.id}/delete").status_code, 403)
        pi.status = "ARRIVED"; db.session.commit()
        deleted = client.post(f"/v2/orders/{pi.id}/batches/{batch.id}/delete")
        self.assertEqual(deleted.status_code, 302)
        self.assertTrue(deleted.headers["Location"].endswith(f"#batch-item-{pi.items[0].id}"))
        self.assertEqual(ProductBatch.query.filter_by(pi_item_id=pi.items[0].id).count(), 0)

    def test_booking_editing_is_pre_shipment_only_in_order_view(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0"))
        pi.status = "PRE_SHIPMENT"
        pre_shipment = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertIn('id="booking-cargo-information"', pre_shipment)
        self.assertIn('id="shipment-preparation"', pre_shipment)
        self.assertNotIn('id="post-loading-booking-facts"', pre_shipment)
        for status in ("SHIPPED", "ARRIVED"):
            pi.status = status; db.session.commit()
            html = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
            self.assertNotIn('id="post-loading-booking-facts"', html)
            self.assertNotIn('id="booking-cargo-information"', html)
            self.assertNotIn('id="shipment-preparation"', html)

    def test_arrived_preserves_existing_work_and_cancels_driver_history(self):
        now = datetime(2026, 9, 20, 9)
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        pi.actual_departure_date = date(2026, 9, 10); pi.coo_required = True
        settlement = FreightSettlement(pi_id=pi.id, usd_bill_required=True, cny_bill_required=True)
        db.session.add(settlement)
        self._outbound_done(pi, email_at=datetime(2026, 9, 15, 8))
        reconcile_order_tasks_for_pi(pi, now=now)
        tracked = {code: self.task(pi, code).id for code in ("FREIGHT_USD_AMOUNT_CAPTURE", "FREIGHT_CNY_AMOUNT_CAPTURE", "DOCUMENT_COO", "PAYMENT_BALANCE_FOLLOWUP")}
        driver = OrderTask(pi_id=pi.id, task_code="SHIPPING_DRIVER_INFO", title="Driver", source="AUTO", status="ACTION", health="NORMAL", completion_mode="RULE_DATA", dedupe_key=f"v2:order:{pi.id}:shipping_driver_info")
        db.session.add(driver); db.session.flush()
        pi.status = "ARRIVED"; pi.actual_arrival_date = date(2026, 9, 20)
        reconcile_order_tasks_for_pi(pi, now=now)
        self.assertEqual(driver.status, "CANCELLED")
        self.assertTrue(any(row.event_type == "CANCELLED" for row in driver.activities))
        for code, task_id in tracked.items():
            self.assertEqual(self.task(pi, code).id, task_id)
        pickup = self.task(pi, "ARRIVAL_CUSTOMER_PICKUP")
        self.assertEqual((pickup.status, pickup.source, pickup.completion_mode), ("ACTION", "AUTO", "MANUAL"))
        reconcile_order_tasks_for_pi(pi, now=now)
        self.assertEqual(self.task(pi, "ARRIVAL_CUSTOMER_PICKUP").id, pickup.id)

    def test_task_action_redirects_to_task_and_activity_datetime_is_minutes(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0")); pi.status = "SHIPPED"
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 1))
        task = self.task(pi, "PAYMENT_EMAIL"); db.session.commit()
        done = self.client().post(f"/v2/tasks/{task.id}/done")
        self.assertTrue(done.headers["Location"].endswith(f"#task-{task.id}"))
        reopened = self.client().post(f"/v2/tasks/{task.id}/reopen", data={"reason": "Correction"})
        self.assertTrue(reopened.headers["Location"].endswith(f"#task-{task.id}"))
        waiting = self.client().post(f"/v2/tasks/{task.id}/waiting", data={"waiting_on": "CUSTOMER"})
        self.assertTrue(waiting.headers["Location"].endswith(f"#task-{task.id}"))
        self.assertEqual(format_task_datetime(datetime(2026, 9, 1, 7, 5)), "2026-09-01 07:05")

    def test_expired_no_advance_has_one_general_exception(self):
        pi = self.make_pi(planned=date(2026, 9, 20), advance=Decimal("0"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 21))
        overdue = self.task(pi, "SHIPPING_PLANNED_DATE_OVERDUE")
        self.assertEqual((overdue.status, overdue.health), ("ACTION", "EXCEPTION"))
        count = len(overdue.activities); reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 21))
        self.assertEqual(len(overdue.activities), count)

    def test_dashboard_actions_and_get_zero_write(self):
        pi = self.make_pi(planned=date.today() + timedelta(days=5))
        reconcile_order_tasks_for_pi(pi, now=datetime.now())
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING"); db.session.commit()
        before = (OrderTask.query.count(), TaskActivity.query.count())
        response = self.client().get("/v2/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("task-action-area", html); self.assertIn("登记预付款到账", html); self.assertIn("Follow-up", html)
        self.assertNotIn(f"/v2/tasks/{task.id}/done", html)
        self.assertEqual((OrderTask.query.count(), TaskActivity.query.count()), before)

    def test_stage_gate_post_is_the_only_new_to_pre_shipment_transition(self):
        clock = datetime(2026, 9, 5, 12)
        pi = self.make_pi(planned=clock.date() + timedelta(days=15), advance=Decimal("0"))
        db.session.commit()
        client = self.client()
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/status", data={"status": "PRE_SHIPMENT"}).status_code, 409)
        with patch("v2.services.utcnow", return_value=clock):
            reconcile_order_tasks_for_pi(pi)
            db.session.commit()
            response = client.post(f"/v2/orders/{pi.id}/enter-pre-shipment")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pi.status, "PRE_SHIPMENT")
        self.assertEqual(self.task(pi, "SHIPPING_CONTAINER_LOADING").status, "ACTION")

    def test_task_history_is_append_only_and_dashboard_opens_that_task(self):
        pi = self.make_pi(planned=date(2026, 9, 20))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 10))
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING")
        follow_up(task, self.user.id, waiting_on="CUSTOMER", next_follow_up_at=datetime(2026, 9, 12), note="First call")
        follow_up(task, self.user.id, waiting_on="CUSTOMER", next_follow_up_at=datetime(2026, 9, 14), note="Bank pending")
        db.session.commit()
        self.assertEqual(len([a for a in task.activities if a.event_type == "FOLLOW_UP"]), 2)
        dashboard = self.client().get("/v2/").get_data(as_text=True)
        self.assertIn(f"/v2/tasks/{task.id}/history", dashboard)
        history = self.client().get(f"/v2/tasks/{task.id}/history").get_data(as_text=True)
        self.assertIn("First call", history)
        self.assertIn("Bank pending", history)
        detail = self.client().get(f"/v2/orders/{pi.id}?open_task={task.id}").get_data(as_text=True)
        self.assertIn(f'id="history-{task.id}"', detail)
        self.assertIn("First call", detail)
        self.assertIn("Bank pending", detail)
        self.assertIn("collapse mt-2 show", detail)

    def test_new_reconcile_cancels_premature_prep_tasks_without_duplicates(self):
        pi = self.make_pi(planned=date.today(), advance=Decimal("0"))
        from v2.services import _upsert_task
        early_loading = _upsert_task(pi, "SHIPPING_CONTAINER_LOADING", "确认工厂装柜日期", status="ACTION")
        early_quote = _upsert_task(pi, "SHIPPING_FREIGHT_AGREEMENT", "向货代询价并确认船期", status="ACTION")
        reconcile_order_tasks_for_pi(pi, now=datetime.now())
        self.assertEqual(early_loading.status, "CANCELLED")
        self.assertEqual(early_quote.status, "CANCELLED")
        self.assertEqual(self.task(pi, "STAGE_GATE_PRE_SHIPMENT").status, "ACTION")
        self.assertEqual(len([a for a in early_loading.activities if a.event_type == "CANCELLED"]), 1)

    def test_pre_shipment_payload_matches_lifecycle_policy(self):
        pi = self.make_pi(planned=date.today(), advance=Decimal("0")); pi.status = "PRE_SHIPMENT"; pi.etd = date(2026, 9, 30); db.session.commit()
        payload = {
            "container_type": "20GP", "container_count": "1", "container_loading_date": "2026-09-20",
            "container_loading_period": "PM", "container_location": "Factory", "driver_name": "Li",
            "driver_phone": "13800000000", "vehicle_number": "A123", "vessel_info": "Vessel 1",
            "booking_number": "BK-1", "shipping_mark": "XHT MARK", "freight_term": "FOB",
            "freight_clause": "PREPAID", "waybill_option": "ORIGINAL", "freight_forwarder_id": "",
            "usd_bill_required": "true", "cny_bill_required": "false", "notify_party_same_as_consignee": "true",
            "package_count": "800", "package_unit": "BAGS", "gross_weight": "20", "gross_weight_display_unit": "MT", "volume": "25.5",
        }
        response = self.client().post(f"/v2/orders/{pi.id}/facts", data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual((pi.shipping_mark, pi.container_loading_period, pi.driver_name, pi.etd), ("XHT MARK", "PM", "Li", date(2026, 9, 30)))
        self.assertEqual((pi.package_count, pi.package_unit, pi.gross_weight_kg, pi.gross_weight_display_unit, pi.volume_cbm), (800, "BAGS", Decimal("20000"), "MT", Decimal("25.5")))
        payload["gross_weight_display_unit"] = "LB"
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data=payload).status_code, 400)
        payload["gross_weight_display_unit"] = "MT"
        self.assertIsNotNone(self.task(pi, "SHIPPING_FREIGHT_AGREEMENT"))
        payload["actual_departure_date"] = "2026-09-20"
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data=payload).status_code, 400)
        payload.pop("actual_departure_date")
        payload["container_number"] = "POST-ONLY"
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data=payload).status_code, 400)
        payload.pop("container_number")
        payload["coo_required"] = "true"
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data=payload).status_code, 400)
        pi.status = "NEW"; db.session.commit()
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data={"shipping_mark": "blocked"}).status_code, 400)
        pi.status = "COMPLETED"; db.session.commit()
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data={"shipping_mark": "blocked"}).status_code, 403)

    def test_booking_cargo_patch_preserves_other_pre_shipment_modules(self):
        pi = self.make_pi(planned=date.today(), advance=Decimal("0")); pi.status = "PRE_SHIPMENT"
        pi.container_type = "20GP"; pi.container_count = 1
        pi.container_loading_date = date(2026, 8, 25); pi.container_loading_period = "PM"
        pi.container_location = "Factory"; pi.vessel_info = "Vessel 1"
        pi.shipping_mark = "MARK"; pi.waybill_option = "ORIGINAL"
        db.session.commit()
        response = self.client().post(f"/v2/orders/{pi.id}/facts", data={
            "package_count": "960", "package_unit": "BAGS", "gross_weight": "24.4",
            "gross_weight_display_unit": "MT", "volume": "25",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            (pi.container_type, pi.container_count, pi.container_loading_date,
             pi.container_loading_period, pi.container_location),
            ("20GP", 1, date(2026, 8, 25), "PM", "Factory"),
        )
        self.assertEqual((pi.vessel_info, pi.shipping_mark, pi.waybill_option),
                         ("Vessel 1", "MARK", "ORIGINAL"))
        self.assertEqual((pi.package_count, pi.package_unit, pi.gross_weight_kg, pi.volume_cbm),
                         (960, "BAGS", Decimal("24400"), Decimal("25")))

    def test_booking_cargo_defaults_package_unit_without_overwriting_existing_value(self):
        pi = self.make_pi(planned=date.today(), advance=Decimal("0")); pi.status = "PRE_SHIPMENT"
        db.session.commit()
        html = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertIn('name="package_unit" value="BAGS"', html)
        self.assertEqual(self.client().post(f"/v2/orders/{pi.id}/facts", data={"package_count": "960"}).status_code, 302)
        self.assertEqual(pi.package_unit, "BAGS")
        pi.package_unit = "DRUMS"; db.session.commit()
        html = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertIn('name="package_unit" value="DRUMS"', html)

    def test_advance_receipt_action_auto_resolves_same_task(self):
        pi = self.make_pi(planned=date.today() + timedelta(days=4))
        reconcile_order_tasks_for_pi(pi, now=datetime.now()); db.session.commit()
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING")
        response = self.client().post(f"/v2/orders/{pi.id}/advance-receipt", data={
            "advance_received_amount": "200.00", "advance_received_at": "2026-09-10",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual((task.id, task.status, task.resolution_code), (task.id, "DONE", "AUTO_RESOLVED"))

    def test_sales_planned_shipment_required_api(self):
        response = self.client().post("/orders", json={"pi_no": "NO-PLAN", "pi_date": "2026-08-01", "order_type": "SALES"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Planned Shipment Date", response.get_json()["error"])
