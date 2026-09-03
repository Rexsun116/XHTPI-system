"""Mutation-path coverage for per-PI Reminder reconciliation."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest import TestCase, mock

from sqlalchemy import text

from tests import test_support  # noqa: F401

from app import (
    Customer,
    Exporter,
    Factory,
    FreightForwarder,
    PI,
    PIItem,
    Product,
    TargetedReconcileError,
    User,
    app,
    commit_pi_with_targeted_reconcile,
    db,
)
from reminders.enums import TaskHealth, TaskStatus
from task_models import OrderTask, TaskActivity


NOW = datetime(2026, 8, 21, 10, 0)


class TargetedReconcileIntegrationTest(TestCase):
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
        app._admin_created = True
        self.user = User(username="targeted-user")
        self.user.set_password("password")
        self.customer = Customer(code="C-TARGET", name="Rahul Minerals", address="A", country="IN")
        self.exporter = Exporter(code="E-TARGET", name="AEA", address="A", country="HK")
        self.factory = Factory(code="F-TARGET", name="Factory", address="A", country="CN")
        self.product = Product(code="P-TARGET", model="TiO2")
        self.forwarder = FreightForwarder(code="FF-TARGET", name="SeaLink", address="A", country="CN")
        db.session.add_all(
            [self.user, self.customer, self.exporter, self.factory, self.product, self.forwarder]
        )
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.context.pop()

    def client(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True
        return client

    def create_pi(self, suffix="1", **values):
        status = values.pop("status", "新建")
        pi = PI(
            pi_no=f"TARGET-{suffix}",
            pi_date=NOW.date(),
            customer_id=self.customer.id,
            exporter_id=self.exporter.id,
            customer_name_snapshot=self.customer.name,
            status=status,
            **values,
        )
        db.session.add(pi)
        db.session.flush()
        db.session.add(
            PIItem(
                pi_id=pi.id,
                product_id=self.product.id,
                factory_id=self.factory.id,
                unit_price=1000,
                quantity=10,
                total_price=10000,
            )
        )
        return pi

    def task(self, pi, code):
        return OrderTask.query.filter_by(pi_id=pi.id, task_code=code).one_or_none()

    def sales_form(self, pi_no="TARGET-ROUTE"):
        return {
            "pi_no": pi_no,
            "pi_date": NOW.date().isoformat(),
            "customer": str(self.customer.id),
            "exporter": str(self.exporter.id),
            "payment_terms": "20% advance, balance after BL copy",
            "currency": "USD",
            "advance_payment_percent": "20",
            "loading_port": "Shanghai",
            "destination_port": "Nhava Sheva",
            "product_0": str(self.product.id),
            "factory_0": str(self.factory.id),
            "trade_term_0": "CIF",
            "unit_price_0": "1000",
            "quantity_0": "10",
        }

    def test_01_new_sales_pi_runs_targeted_reconcile_and_derives_20_percent(self):
        response = self.client().post("/create-pi", data=self.sales_form())
        self.assertEqual(response.status_code, 302)
        pi = PI.query.filter_by(pi_no="TARGET-ROUTE").one()
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING")
        self.assertIsNotNone(task)
        self.assertEqual(task.status, TaskStatus.WAITING)
        self.assertEqual(task.context_payload["expected_amount"], "2000.00")
        self.assertEqual(task.context_payload["currency"], "USD")

    def test_02_new_commission_pi_runs_targeted_reconcile(self):
        form = self.sales_form("TARGET-COMMISSION")
        form.pop("exporter")
        form.update(
            commission_factory_id=str(self.factory.id),
            commission_exporter_id=str(self.exporter.id),
            commission_amount="100",
            commission_rate="1",
            commission_status="未结算",
            factory_sale_amount="9000",
        )
        response = self.client().post("/create-pi-commission", data=form)
        self.assertEqual(response.status_code, 302)
        pi = PI.query.filter_by(pi_no="TARGET-COMMISSION").one()
        self.assertIsNone(pi.exporter_id)
        self.assertIsNotNone(self.task(pi, "PAYMENT_ADVANCE_WAITING"))

    def test_03_missing_contract_total_is_explicit_exception(self):
        pi = PI(
            pi_no="TARGET-NO-TOTAL", pi_date=NOW.date(), customer_id=self.customer.id,
            exporter_id=self.exporter.id, advance_payment_percent=Decimal("20"), status="新建",
        )
        db.session.add(pi)
        commit_pi_with_targeted_reconcile(pi)
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING")
        self.assertEqual((task.status, task.health), (TaskStatus.UPCOMING, TaskHealth.EXCEPTION))
        self.assertEqual(task.context_payload["health_reason_code"], "PAYMENT_CONTRACT_TOTAL_MISSING")

    def test_04_required_null_true_false_true_reuses_task(self):
        pi = self.create_pi("DOC", status="待发运")
        commit_pi_with_targeted_reconcile(pi)
        self.assertIsNone(self.task(pi, "DOCUMENT_COC"))
        pi.coc_required = True
        commit_pi_with_targeted_reconcile(pi)
        task = self.task(pi, "DOCUMENT_COC")
        task_id = task.id
        pi.coc_required = False
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual(self.task(pi, "DOCUMENT_COC").status, TaskStatus.CANCELLED)
        pi.coc_required = True
        commit_pi_with_targeted_reconcile(pi)
        task = self.task(pi, "DOCUMENT_COC")
        self.assertEqual(task.id, task_id)
        self.assertEqual(task.status, TaskStatus.ACTION)

    def test_05_etd_change_and_actual_departure_fill_remove(self):
        today = datetime.now().date()
        pi = self.create_pi("ETD", etd=today)
        commit_pi_with_targeted_reconcile(pi)
        task = self.task(pi, "SHIPPING_ACTUAL_DEPARTURE")
        task_id = task.id
        self.assertEqual(task.health, TaskHealth.EXCEPTION)
        pi.etd = today + timedelta(days=2)
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual(task.status, TaskStatus.UPCOMING)
        pi.etd = today
        pi.actual_departure_date = today
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual(task.status, TaskStatus.DONE)
        pi.actual_departure_date = None
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual((task.id, task.status), (task_id, TaskStatus.ACTION))

    def test_06_driver_fill_and_remove_reactivates_same_task(self):
        pi = self.create_pi("DRIVER", container_loading_at=NOW)
        commit_pi_with_targeted_reconcile(pi)
        task = self.task(pi, "SHIPPING_DRIVER_INFO")
        task_id = task.id
        pi.driver_name, pi.driver_phone, pi.vehicle_number = "Li", "13800000000", "川A12345"
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual(task.status, TaskStatus.DONE)
        pi.vehicle_number = None
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual((task.id, task.status), (task_id, TaskStatus.ACTION))

    def test_07_usd_and_cny_freight_activate_independently(self):
        pi = self.create_pi("FREIGHT", actual_departure_date=NOW.date() - timedelta(days=7))
        pi.freight_usd_bill_required = True
        commit_pi_with_targeted_reconcile(pi)
        self.assertIsNotNone(self.task(pi, "FREIGHT_USD_AMOUNT_CAPTURE"))
        self.assertIsNone(self.task(pi, "FREIGHT_CNY_AMOUNT_CAPTURE"))
        pi.freight_cny_bill_required = True
        commit_pi_with_targeted_reconcile(pi)
        self.assertIsNotNone(self.task(pi, "FREIGHT_CNY_AMOUNT_CAPTURE"))

    def test_08_payment_received_change_auto_resolves(self):
        pi = self.create_pi(
            "PAY", currency="USD", advance_payment_amount=Decimal("2000"),
            advance_received_amount=Decimal("0"), balance_payment_amount=Decimal("8000"),
            balance_received_amount=Decimal("0"),
        )
        commit_pi_with_targeted_reconcile(pi)
        task = self.task(pi, "PAYMENT_ADVANCE_WAITING")
        pi.advance_received_amount = Decimal("2000")
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_09_repeated_targeted_reconcile_is_idempotent(self):
        pi = self.create_pi("IDEMP", etd=NOW.date())
        commit_pi_with_targeted_reconcile(pi)
        counts = (OrderTask.query.count(), TaskActivity.query.count())
        commit_pi_with_targeted_reconcile(pi)
        self.assertEqual((OrderTask.query.count(), TaskActivity.query.count()), counts)

    def test_10_dashboard_get_remains_read_only(self):
        pi = self.create_pi("READONLY", etd=NOW.date())
        db.session.commit()
        before = (OrderTask.query.count(), TaskActivity.query.count())
        response = self.client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((OrderTask.query.count(), TaskActivity.query.count()), before)
        self.assertIsNone(self.task(pi, "SHIPPING_ACTUAL_DEPARTURE"))

    def test_11_failed_reconcile_rolls_back_pi_mutation(self):
        pi = self.create_pi("ROLLBACK")
        db.session.commit()
        pi_id = pi.id
        with mock.patch("app.reconcile_order_tasks_for_pi", side_effect=RuntimeError("boom")):
            pi.note = "must rollback"
            with self.assertRaises(TargetedReconcileError):
                commit_pi_with_targeted_reconcile(pi)
        db.session.expire_all()
        self.assertNotEqual(db.session.get(PI, pi_id).note, "must rollback")
        self.assertEqual(OrderTask.query.count(), 0)
