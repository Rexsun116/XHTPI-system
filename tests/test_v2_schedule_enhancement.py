"""Deterministic V2 ETD/ETA schedule workflow coverage."""

from datetime import date, datetime, timezone
from decimal import Decimal
import re
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.business_time import business_today
from v2.models import Customer, Exporter, FreightSettlement, OrderTask, PI, PIItem, TaskActivity, User, db
from v2.presenter import present_task
from v2.services import reconcile_order_tasks_for_pi
from v2.selector import projected, projected_details
from v2.task_service import follow_up


class ScheduleEnhancementTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="schedule", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="C", name="Customer")
        self.exporter = Exporter(code="E", name="Exporter")
        db.session.add_all([self.user, self.customer, self.exporter]); db.session.commit()

    def tearDown(self):
        db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def pi(self, status="PRE_SHIPMENT", **facts):
        pi = PI(pi_no=f"S-{PI.query.count()+1}", pi_date=date(2026, 9, 1), order_type="SALES", status=status,
                customer_id=self.customer.id, exporter_id=self.exporter.id,
                customer_name_snapshot="Customer", exporter_name_snapshot="Exporter", currency="USD",
                advance_payment_amount=Decimal("0"), balance_payment_amount=Decimal("1000"), **facts)
        pi.items.append(PIItem(unit_price=Decimal("100"), quantity=Decimal("10"), quantity_unit="MT", line_total=Decimal("1000")))
        db.session.add(pi); db.session.flush(); return pi

    def task(self, pi, code):
        return db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == pi.id, OrderTask.task_code == code))

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def test_shanghai_business_date_can_differ_from_utc_date(self):
        utc_late = datetime(2026, 9, 17, 16, tzinfo=timezone.utc)
        self.assertEqual(utc_late.date(), date(2026, 9, 17))
        self.assertEqual(business_today(utc_late), date(2026, 9, 18))
        pi = self.pi(etd=date(2026, 9, 18))
        reconcile_order_tasks_for_pi(pi, now=utc_late.replace(tzinfo=None))
        self.assertEqual((self.task(pi, "SHIPPING_ACTUAL_DEPARTURE").status, self.task(pi, "SHIPPING_ACTUAL_DEPARTURE").health), ("ACTION", "EXCEPTION"))

    def test_pre_shipment_schedule_save_and_validation(self):
        pi = self.pi(); client = self.client()
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/facts", data={"etd": "2026-09-20"}).status_code, 302)
        self.assertEqual(pi.etd, date(2026, 9, 20))
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/facts", data={"eta": "2026-09-21"}).status_code, 302)
        self.assertEqual(pi.eta, date(2026, 9, 21))
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/facts", data={"etd": "2026-09-22", "eta": "2026-09-21"}).status_code, 400)
        self.assertEqual((pi.etd, pi.eta), (date(2026, 9, 20), date(2026, 9, 21)))
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/facts", data={"etd": "2026-09-22", "eta": "2026-09-23"}).status_code, 302)
        self.assertEqual((pi.etd, pi.eta), (date(2026, 9, 22), date(2026, 9, 23)))
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/facts", data={"etd": "2026-09-23", "eta": "2026-09-23"}).status_code, 302)
        self.assertEqual((pi.etd, pi.eta), (date(2026, 9, 23), date(2026, 9, 23)))

    def test_etd_and_eta_task_boundaries_and_stable_dedupe(self):
        etd, eta = date(2026, 9, 20), date(2026, 9, 30)
        pi = self.pi(etd=etd)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 19, 12))
        departure = self.task(pi, "SHIPPING_ACTUAL_DEPARTURE"); self.assertEqual(departure.status, "UPCOMING")
        departure_id = departure.id; reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 20, 12))
        self.assertEqual((departure.id, departure.status, departure.health), (departure_id, "ACTION", "EXCEPTION"))
        pi.actual_departure_date = etd; pi.status = "SHIPPED"; pi.eta = eta
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 27, 12))
        self.assertEqual(departure.status, "DONE")
        arrival = self.task(pi, "SHIPPING_ACTUAL_ARRIVAL"); self.assertEqual(arrival.status, "UPCOMING")
        arrival_id = arrival.id
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 28, 12)); self.assertEqual((arrival.status, arrival.health), ("ACTION", "NORMAL"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 29, 12)); self.assertEqual((arrival.status, arrival.health), ("ACTION", "NORMAL"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 30, 12)); self.assertEqual((arrival.id, arrival.health), (arrival_id, "EXCEPTION"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 10, 1, 12)); self.assertEqual((arrival.status, arrival.health), ("ACTION", "EXCEPTION"))
        self.assertIn("enter_arrived", [x["kind"] for x in present_task(arrival)["actions"]])
        pi.actual_arrival_date = eta; reconcile_order_tasks_for_pi(pi, now=datetime(2026, 10, 1, 12))
        self.assertEqual(arrival.status, "DONE")

    def test_eta_change_reuses_task_and_defers_action_to_new_schedule(self):
        pi = self.pi(status="SHIPPED", etd=date(2026, 9, 20), actual_departure_date=date(2026, 9, 20), eta=date(2026, 9, 20))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 18, 12))
        task = self.task(pi, "SHIPPING_ACTUAL_ARRIVAL"); task_id = task.id
        history_count = TaskActivity.query.filter_by(task_id=task.id).count()
        pi.eta = date(2026, 9, 25); reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 18, 12))
        self.assertEqual((task.id, task.dedupe_key, task.status, task.health),
                         (task_id, f"v2:order:{pi.id}:shipping_actual_arrival", "UPCOMING", "NORMAL"))
        self.assertEqual(task.context_payload["eta"], "2026-09-25")
        self.assertEqual(OrderTask.query.filter_by(pi_id=pi.id, task_code="SHIPPING_ACTUAL_ARRIVAL").count(), 1)
        self.assertGreater(TaskActivity.query.filter_by(task_id=task.id).count(), history_count)

    def test_eta_followup_precedence_and_change_while_waiting(self):
        pi = self.pi(status="SHIPPED", etd=date(2026, 9, 20), actual_departure_date=date(2026, 9, 20), eta=date(2026, 9, 20))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 18, 12))
        task = self.task(pi, "SHIPPING_ACTUAL_ARRIVAL"); task_id = task.id
        follow_up(task, self.user.id, waiting_on="FREIGHT_FORWARDER", next_follow_up_at=datetime(2026, 9, 22, 9), note="Carrier checking")
        history_count = TaskActivity.query.filter_by(task_id=task.id).count()
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 19, 12)); self.assertEqual((task.status, task.health), ("WAITING", "NORMAL"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 20, 12)); self.assertEqual((task.status, task.health), ("WAITING", "EXCEPTION"))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 21, 12)); self.assertEqual((task.status, task.health), ("WAITING", "EXCEPTION"))
        pi.eta = date(2026, 9, 25); reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 21, 12))
        self.assertEqual((task.id, task.status, task.health, task.next_follow_up_at),
                         (task_id, "WAITING", "NORMAL", datetime(2026, 9, 22, 9)))
        self.assertEqual(task.context_payload["eta"], "2026-09-25")
        self.assertEqual(TaskActivity.query.filter_by(task_id=task.id).count(), history_count)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 22, 12)); self.assertEqual(task.status, "ACTION")
        self.assertEqual(task.health, "NORMAL")
        pi.status = "ARRIVED"; pi.actual_arrival_date = date(2026, 9, 22)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 22, 12)); self.assertEqual(task.status, "DONE")

    def test_eta_followup_stays_exception_until_due_and_actual_arrival_resolves(self):
        pi = self.pi(status="SHIPPED", etd=date(2026, 9, 20), actual_departure_date=date(2026, 9, 20), eta=date(2026, 9, 20))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 18, 12))
        task = self.task(pi, "SHIPPING_ACTUAL_ARRIVAL")
        follow_up(task, self.user.id, waiting_on="FREIGHT_FORWARDER", next_follow_up_at=datetime(2026, 9, 22, 9), note="Carrier checking")
        for day in (20, 21):
            reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, day, 12))
            self.assertEqual((task.status, task.health), ("WAITING", "EXCEPTION"))
        pi.actual_arrival_date = date(2026, 9, 21)
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 21, 13))
        self.assertEqual(task.status, "DONE")

    def test_eta_dashboard_projection_refreshes_at_shanghai_midnight(self):
        pi = self.pi(status="SHIPPED", etd=date(2026, 9, 18), actual_departure_date=date(2026, 9, 18), eta=date(2026, 9, 20))
        before_midnight_utc = datetime(2026, 9, 19, 15, 50)
        reconcile_order_tasks_for_pi(pi, now=before_midnight_utc)
        task = self.task(pi, "SHIPPING_ACTUAL_ARRIVAL")
        self.assertEqual((task.status, task.health, task.context_payload["message"]), ("ACTION", "NORMAL", None))
        status, health, context = projected_details(task, datetime(2026, 9, 19, 16, 10))
        self.assertEqual((status, health), ("ACTION", "EXCEPTION"))
        self.assertIn("ETA 已过 0 天", context["message"])
        self.assertEqual(projected(task, datetime(2026, 9, 19, 16, 10)), ("ACTION", "EXCEPTION"))

    def test_arrival_task_dashboard_action_uses_dedicated_arrival_route_not_done(self):
        pi = self.pi(status="SHIPPED", etd=date(2026, 9, 18), actual_departure_date=date(2026, 9, 18), eta=date(2026, 9, 20))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 18, 12))
        task = self.task(pi, "SHIPPING_ACTUAL_ARRIVAL")
        self.assertEqual(task.completion_mode, "RULE_DATA")
        with patch("v2.selector.utcnow", return_value=datetime(2026, 9, 18, 12)):
            html = self.client().get("/v2/").get_data(as_text=True)
        self.assertIn(f'/v2/orders/{pi.id}/enter-arrived', html)
        self.assertNotIn(f'/v2/tasks/{task.id}/done', html)

    def test_driver_activation_uses_shanghai_calendar_day(self):
        pi = self.pi(container_loading_date=date(2026, 9, 20))
        now = datetime(2026, 9, 18, 16, 30)  # 2026-09-19 00:30 Asia/Shanghai
        reconcile_order_tasks_for_pi(pi, now=now)
        task = self.task(pi, "SHIPPING_DRIVER_INFO")
        self.assertEqual((task.status, task.health), ("ACTION", "NORMAL"))
        self.assertEqual(projected(task, now), ("ACTION", "NORMAL"))

    def test_freight_trigger_uses_departure_plus_seven_business_days(self):
        pi = self.pi(status="SHIPPED", actual_departure_date=date(2026, 9, 20))
        db.session.add(FreightSettlement(pi_id=pi.id, usd_bill_required=True))
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 25, 16, 30))  # Shanghai 9/26, +6
        self.assertEqual(self.task(pi, "FREIGHT_USD_AMOUNT_CAPTURE").status, "UPCOMING")
        reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 26, 16, 30))  # Shanghai 9/27, +7
        self.assertEqual(self.task(pi, "FREIGHT_USD_AMOUNT_CAPTURE").status, "ACTION")

    def test_shipped_eta_update_is_csrf_and_lifecycle_protected(self):
        pi = self.pi(status="SHIPPED", etd=date(2026, 9, 20), actual_departure_date=date(2026, 9, 20))
        client = self.client(); self.app.config["WTF_CSRF_ENABLED"] = True
        page = client.get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/eta", data={"eta": "2026-09-21"}).status_code, 400)
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/eta", data={"eta": "2026-09-19", "csrf_token": token}).status_code, 400)
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/eta", data={"eta": "2026-09-21", "csrf_token": token}).status_code, 302)
        self.assertEqual(pi.eta, date(2026, 9, 21))
        for status in ("PRE_SHIPMENT", "ARRIVED", "COMPLETED"):
            pi.status = status; db.session.commit()
            self.assertEqual(client.post(f"/v2/orders/{pi.id}/eta", data={"eta": "2026-09-22", "csrf_token": token}).status_code, 403)
