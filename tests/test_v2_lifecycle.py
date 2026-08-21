"""V2 lifecycle policy, progressive disclosure, and pure-domain coverage."""

from datetime import date
from decimal import Decimal
from unittest import TestCase

from sqlalchemy import text

from tests import test_support  # noqa: F401

from app import Customer, Exporter, Factory, PI, PIItem, Product, User, app, db
from order_lifecycle import (
    LifecyclePolicyError,
    ModuleState,
    OrderModule,
    lifecycle_context,
    validate_lifecycle_submission,
)
from v2_domain import (
    calculate_commission_amount,
    compare_freight_quote_to_bill,
    format_batch_numbers,
    format_container_requirement,
)


class V2LifecycleTest(TestCase):
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
        self.user = User(username="v2-lifecycle-user")
        self.user.set_password("password")
        self.customer = Customer(code="C-V2", name="V2 Customer", address="A", country="IN")
        self.exporter = Exporter(code="E-V2", name="V2 Exporter", address="A", country="HK")
        self.factory = Factory(code="F-V2", name="V2 Factory", address="A", country="CN")
        self.product = Product(code="P-V2", model="R504")
        db.session.add_all([self.user, self.customer, self.exporter, self.factory, self.product])
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

    def make_pi(self, status, suffix):
        pi = PI(
            pi_no=f"V2-{suffix}", pi_date=date(2026, 8, 21), customer_id=self.customer.id,
            exporter_id=self.exporter.id, customer_name_snapshot=self.customer.name,
            status=status, currency="USD", advance_payment_percent=Decimal("20.00"),
            freight_usd_bill_required=True, freight_cny_bill_required=False,
            freight_usd_amount=Decimal("100.00"),
        )
        db.session.add(pi)
        db.session.flush()
        db.session.add(PIItem(
            pi_id=pi.id, product_id=self.product.id, factory_id=self.factory.id,
            unit_price=Decimal("1000.00"), quantity=Decimal("10"),
            total_price=Decimal("10000.00"),
        ))
        db.session.commit()
        return pi

    def html(self, pi, route="view_pi"):
        response = self.client().get(
            f"/pi/{pi.id}" if route == "view_pi" else f"/pi/{pi.id}/edit"
        )
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_01_new_view_hides_operational_freight(self):
        html = self.html(self.make_pi("新建", "NEW"))
        self.assertIn("付款计划", html)
        self.assertIn("文件要求", html)
        self.assertNotIn("USD 海运费账单：</strong> USD", html)
        self.assertNotIn("司机电话", html)
        self.assertNotIn("实际发运日期", html)
        self.assertNotIn("实际到港日期", html)

    def test_02_pre_shipment_shows_requirements_not_amounts(self):
        html = self.html(self.make_pi("待发运", "PRE"))
        self.assertIn("USD 海运费账单：</strong> 需要", html)
        self.assertIn("司机电话", html)
        self.assertNotIn("USD 海运费账单：</strong> USD 100", html)

    def test_03_shipped_shows_freight_amount(self):
        html = self.html(self.make_pi("已发运", "SHIP"))
        self.assertIn("USD 海运费账单：</strong> USD 100", html)
        self.assertIn("实际发运日期", html)
        self.assertIn("实际到港日期", html)

    def test_04_completed_is_read_only_in_ui_and_server(self):
        pi = self.make_pi("已完成", "DONE")
        html = self.html(pi, route="edit")
        self.assertIn("订单已完成，无法编辑", html)
        self.assertNotIn("保存更改", html)
        response = self.client().post(f"/pi/{pi.id}/edit", data={"freight_usd_amount": "999"})
        self.assertEqual(response.status_code, 302)
        db.session.refresh(pi)
        self.assertEqual(pi.freight_usd_amount, Decimal("100.00"))

    def test_05_backend_rejects_new_stage_freight_mutation(self):
        pi = self.make_pi("新建", "GUARD")
        with self.assertRaises(LifecyclePolicyError):
            validate_lifecycle_submission(pi, {"freight_usd_amount": "999"})

    def test_06_sales_and_commission_share_policy(self):
        sales = lifecycle_context("待发运")
        commission = lifecycle_context("待发运")
        self.assertEqual(sales.state(OrderModule.FREIGHT_REQUIREMENTS), ModuleState.EDITABLE)
        self.assertEqual(sales, commission)

    def test_07_payment_receipts_begin_at_shipped(self):
        self.assertFalse(lifecycle_context("新建").can_view(OrderModule.PAYMENT_RECEIPTS))
        self.assertTrue(lifecycle_context("已发运").can_edit(OrderModule.PAYMENT_RECEIPTS))

    def test_08_container_and_batch_presenters(self):
        self.assertEqual(format_container_requirement("20gp", 1), "1 × 20GP")
        self.assertEqual(format_batch_numbers(["A", " B ", ""]), "A / B")

    def test_09_freight_quote_bill_same_currency_difference(self):
        result = compare_freight_quote_to_bill(
            quote_amount="100", quote_currency="USD", bill_amount="120", bill_currency="USD"
        )
        self.assertTrue(result["comparable"])
        self.assertEqual(result["reason"], "AMOUNT_DIFFERENCE")
        self.assertEqual(result["difference"], Decimal("20.00"))

    def test_10_freight_quote_bill_never_cross_currency_compares(self):
        result = compare_freight_quote_to_bill(
            quote_amount="100", quote_currency="USD", bill_amount="700", bill_currency="CNY"
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "CURRENCY_MISMATCH")
        self.assertIsNone(result["difference"])

    def test_11_commission_decimal_rounding(self):
        self.assertEqual(calculate_commission_amount("123.45", "2.5"), Decimal("3.09"))

    def test_12_completed_view_has_no_edit_link(self):
        html = self.html(self.make_pi("已完成", "VIEW-DONE"))
        self.assertIn("订单只读", html)
        self.assertNotIn(f'/pi/{PI.query.filter_by(pi_no="V2-VIEW-DONE").one().id}/edit', html)
