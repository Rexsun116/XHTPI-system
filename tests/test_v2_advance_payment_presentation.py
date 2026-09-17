"""Read-only presentation coverage for V2 aggregate advance-payment facts."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, OrderTask, PI, PIItem, TradeGroup, User, db
from v2.presenter import present_task
from v2.selector import projected_details
from v2.services import reconcile_order_tasks_for_pi


class AdvancePaymentPresentationTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="advance-presentation", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="CUS", name="Customer")
        self.exporter = Exporter(code="EXP", name="Exporter")
        db.session.add_all((self.user, self.customer, self.exporter)); db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def pi(self, number, *, status="NEW", expected="100", received="0", received_at=None,
           group=None, role=None):
        pi = PI(
            pi_no=number, pi_date=date(2026, 9, 1), order_type="SALES", status=status,
            customer_id=self.customer.id, exporter_id=self.exporter.id,
            customer_name_snapshot="Customer", exporter_name_snapshot="Exporter", currency="USD",
            planned_shipment_date=date(2026, 9, 30), advance_payment_amount=Decimal(expected),
            advance_received_amount=Decimal(received), advance_received_at=received_at,
            balance_payment_amount=Decimal("900"), trade_group=group, trade_role=role,
        )
        pi.items.append(PIItem(unit_price=Decimal("100"), quantity=Decimal("10"), quantity_unit="MT",
                               line_total=Decimal("1000"), product_model_snapshot="R-996"))
        db.session.add(pi); db.session.flush()
        return pi

    def legacy_advance_task(self, pi, *, status="DONE"):
        task = OrderTask(
            pi_id=pi.id, task_code="PAYMENT_ADVANCE_WAITING", title="等待客户支付预付款",
            source="AUTO", status=status, health="NORMAL", completion_mode="RULE_DATA", priority=10,
            context_payload={
                "currency": "USD", "expected_amount": "100.00", "received_amount": "0.00",
                "outstanding_amount": "100.00", "days_remaining": 14,
                "message": "发运准备暂未启动：等待预付款到账",
            }, dedupe_key=f"advance-{pi.id}",
        )
        db.session.add(task); db.session.flush()
        return task

    def projected(self, task):
        status, health, context = projected_details(task, datetime(2026, 9, 16, 9))
        task._dashboard_status, task._dashboard_health, task._dashboard_context = status, health, context
        return present_task(task)

    def test_done_legacy_context_uses_current_settled_pi_facts_without_rewriting_json(self):
        pi = self.pi("SETTLED", received="100", received_at=datetime(2026, 9, 15, 10))
        task = self.legacy_advance_task(pi)
        original_context = dict(task.context_payload)

        presented = self.projected(task)

        self.assertEqual(presented["title"], "预付款已确认到账")
        self.assertIn("Expected: USD 100.00", presented["lines"])
        self.assertIn("Received: USD 100.00", presented["lines"])
        self.assertIn("Outstanding: USD 0.00", presented["lines"])
        self.assertIn("Advance received: 2026-09-15", presented["lines"])
        self.assertFalse(any("等待预付款到账" in line or "Days remaining" in line for line in presented["lines"]))
        self.assertEqual(task.context_payload, original_context)

    def test_partial_and_unpaid_tasks_keep_active_payment_logic(self):
        partial = self.pi("PARTIAL", received="40", received_at=datetime(2026, 9, 14, 10))
        unpaid = self.pi("UNPAID", received="0")
        for pi in (partial, unpaid):
            reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 16, 9))

        partial_task = db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == partial.id,
                                                                      OrderTask.task_code == "PAYMENT_ADVANCE_WAITING"))
        unpaid_task = db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == unpaid.id,
                                                                     OrderTask.task_code == "PAYMENT_ADVANCE_WAITING"))
        self.assertNotEqual(partial_task.status, "DONE")
        self.assertNotEqual(unpaid_task.status, "DONE")
        self.assertEqual(partial_task.context_payload["received_amount"], "40.00")
        self.assertEqual(partial_task.context_payload["outstanding_amount"], "60.00")
        self.assertEqual(unpaid_task.context_payload["received_amount"], "0.00")

    def test_other_done_task_retains_its_historical_context(self):
        pi = self.pi("OTHER-DONE", received="100")
        task = OrderTask(pi_id=pi.id, task_code="PAYMENT_EMAIL", title="历史邮件任务", source="AUTO",
                         status="DONE", health="NORMAL", completion_mode="MANUAL", priority=50,
                         context_payload={"message": "历史说明", "days_remaining": 3}, dedupe_key=f"mail-{pi.id}")
        db.session.add(task); db.session.flush()

        presented = self.projected(task)

        self.assertEqual(presented["title"], "历史邮件任务")
        self.assertIn("历史说明", presented["lines"])
        self.assertIn("Days remaining: 3", presented["lines"])

    def test_read_only_summary_is_visible_for_new_and_pre_shipment_and_preserves_post_shipment_form(self):
        new = self.pi("SUMMARY-NEW", received="40", received_at=datetime(2026, 9, 14, 10))
        pre = self.pi("SUMMARY-PRE", status="PRE_SHIPMENT", received="100", received_at=datetime(2026, 9, 15, 10))
        shipped = self.pi("SUMMARY-SHIPPED", status="SHIPPED", received="100", received_at=datetime(2026, 9, 15, 10))
        db.session.commit()
        for pi, status in ((new, "部分到账"), (pre, "已足额到账"), (shipped, "已足额到账")):
            page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
            self.assertIn('id="advance-payment-summary"', page)
            self.assertIn("应收预付款", page)
            self.assertIn("USD 100.00", page)
            self.assertIn(status, page)
            self.assertIn("@media (max-width:575.98px)", page)
        shipped_page = self.client().get(f"/v2/orders/{shipped.id}").get_data(as_text=True)
        self.assertIn('id="payment-receipts"', shipped_page)

    def test_linked_export_order_has_no_customer_payment_summary(self):
        group = TradeGroup(group_no="TRI-ADVANCE-SUMMARY")
        db.session.add(group); db.session.flush()
        self.pi("SUMMARY-CUSTOMER", group=group, role="CUSTOMER_ORDER")
        export = self.pi("SUMMARY-EXPORT", group=group, role="EXPORT_ORDER")
        db.session.commit()

        page = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)

        self.assertNotIn('id="advance-payment-summary"', page)
        self.assertIn('id="linked-financial-owner"', page)

    def test_missing_aggregate_dates_render_safely(self):
        pi = self.pi("SUMMARY-MISSING", expected="0", received="0", received_at=None)
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertIn("无需预付款", page)
        self.assertIn("到账日期</small><div>—</div>", page)

    def test_order_detail_shell_uses_real_identity_overview_and_unique_anchor_targets(self):
        pi = self.pi("A-VERY-LONG-PI-NUMBER-FOR-RESPONSIVE-CHECK", status="PRE_SHIPMENT")
        pi.customer_name_snapshot = "A very long customer name used to exercise the shrinkable page header"
        pi.loading_port, pi.destination_port = "Shanghai", "Mombasa"
        pi.items.append(PIItem(unit_price=Decimal("10"), quantity=Decimal("3"), quantity_unit="MT",
                               line_total=Decimal("30"), product_model_snapshot="R-2195"))
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        for text in ("Order details", pi.pi_no, "Order type", "Sales order", "Receiving company",
                     "Order date", "Current stage: PRE_SHIPMENT", "Contract amount",
                     "Products & quantity", "R-996", "R-2195", "Total · 13.000 MT",
                     "Shanghai → Mombasa"):
            self.assertIn(text, page)
        self.assertEqual(page.count('id="order-overview"'), 1)
        self.assertEqual(page.count('id="document-requirements"'), 1)
        self.assertIn('href="#shipping-schedule"', page)
        self.assertNotIn('action="/v2/orders/%s/status"' % pi.id, page)

    def test_mixed_units_do_not_create_a_misleading_combined_total(self):
        pi = self.pi("SUMMARY-MIXED-UNITS")
        pi.items.append(PIItem(unit_price=Decimal("10"), quantity=Decimal("4"), quantity_unit="KGS",
                               line_total=Decimal("40"), product_model_snapshot="R-2195"))
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertIn("Multiple units — shown per item", page)
        self.assertIn("R-996 · 10.000 MT", page)
        self.assertIn("R-2195 · 4.000 KGS", page)
