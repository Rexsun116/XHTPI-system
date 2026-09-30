"""Read-only presentation coverage for V2 aggregate advance-payment facts."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import (Customer, Exporter, FreightForwarder, OrderFreightAgreement,
                       OrderTask, PI, PIItem, TradeGroup, User, db)
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
        detail_css = (Path(__file__).parents[1] / "v2/static/v2_order_detail.css").read_text()
        self.assertIn("@media(max-width:575.98px)", detail_css)
        for pi, status in ((new, "部分到账"), (pre, "已足额到账"), (shipped, "已足额到账")):
            page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
            self.assertIn('id="advance-payment-summary"', page)
            self.assertIn("应收预付款", page)
            self.assertIn("USD 100.00", page)
            self.assertIn(status, page)
        shipped_page = self.client().get(f"/v2/orders/{shipped.id}").get_data(as_text=True)
        self.assertIn('id="payment-receipts"', shipped_page)
        receipts = shipped_page.split('id="payment-receipts"', 1)[1].split('</form>', 1)[0]
        self.assertIn('class="order-payment-receipts-grid"', receipts)
        self.assertIn(f'action="/v2/orders/{shipped.id}/facts"', receipts)
        self.assertIn('method="post"', receipts)
        self.assertIn('name="csrf_token"', receipts)
        for field in ('advance_received_amount', 'advance_received_at',
                      'balance_received_amount', 'balance_received_at'):
            self.assertIn(f'name="{field}"', receipts)

    def test_order_overview_shows_only_a_populated_pi_note(self):
        noted = self.pi("ORDER-NOTE")
        noted.note = "Commercial instruction\nKeep this exact wording."
        noted.other_document_notes = "Document-only note"
        empty = self.pi("ORDER-NOTE-EMPTY")
        empty.other_document_notes = "Document-only note"
        db.session.commit()

        page = self.client().get(f"/v2/orders/{noted.id}").get_data(as_text=True)
        self.assertIn('id="order-note"', page)
        self.assertIn("Commercial instruction\nKeep this exact wording.", page)
        self.assertNotIn("Document-only note", page.split('id="order-overview"', 1)[1].split('</section>', 1)[0])

        empty_page = self.client().get(f"/v2/orders/{empty.id}").get_data(as_text=True)
        self.assertNotIn('id="order-note"', empty_page)

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
        self.assertIn("到账日期</span><strong>—</strong>", page)

    def test_order_detail_shell_uses_real_identity_overview_and_unique_anchor_targets(self):
        pi = self.pi("A-VERY-LONG-PI-NUMBER-FOR-RESPONSIVE-CHECK", status="PRE_SHIPMENT")
        pi.customer_name_snapshot = "A very long customer name used to exercise the shrinkable page header"
        pi.loading_port, pi.destination_port = "Shanghai", "Mombasa"
        pi.items.append(PIItem(unit_price=Decimal("10"), quantity=Decimal("3"), quantity_unit="MT",
                               line_total=Decimal("30"), product_model_snapshot="R-2195"))
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        for text in ("Order workspace", pi.pi_no, "Order type", "Sales order", "Receiving company",
                     "Order date", "Current stage: PRE_SHIPMENT", "合同金额",
                     "产品与数量", "R-996", "R-2195", "Total · 13.000 MT",
                     "Shanghai → Mombasa"):
            self.assertIn(text, page)
        self.assertEqual(page.count('id="order-overview"'), 1)
        self.assertEqual(page.count('id="documents"'), 1)
        self.assertIn('href="#shipping"', page)
        self.assertNotIn('action="/v2/orders/%s/status"' % pi.id, page)

    def test_shipping_route_flags_use_only_explicit_route_country_codes(self):
        flagged = self.pi("ROUTE-FLAGS", status="PRE_SHIPMENT")
        flagged.loading_port, flagged.loading_country_code = "Shanghai", "cn"
        flagged.destination_port, flagged.destination_country_code = "Nhava Sheva", "IN"
        missing = self.pi("ROUTE-NO-FLAGS", status="PRE_SHIPMENT")
        missing.loading_port, missing.destination_port = "Shanghai", "Mombasa"
        invalid = self.pi("ROUTE-INVALID-FLAG", status="PRE_SHIPMENT")
        invalid.loading_port, invalid.loading_country_code = "Jebel Ali", "XX"
        invalid.destination_port, invalid.destination_country_code = "Mombasa", "KEN"
        db.session.commit()

        flagged_page = self.client().get(f"/v2/orders/{flagged.id}").get_data(as_text=True)
        missing_page = self.client().get(f"/v2/orders/{missing.id}").get_data(as_text=True)
        invalid_page = self.client().get(f"/v2/orders/{invalid.id}").get_data(as_text=True)

        self.assertIn('class="order-route-flag" aria-hidden="true">🇨🇳</span>Shanghai', flagged_page)
        self.assertIn('class="order-route-flag" aria-hidden="true">🇮🇳</span>Nhava Sheva', flagged_page)
        self.assertIn("Shanghai", missing_page)
        self.assertIn("Mombasa", missing_page)
        self.assertNotIn('class="order-route-flag"', missing_page)
        self.assertIn("Jebel Ali", invalid_page)
        self.assertIn("Mombasa", invalid_page)
        self.assertNotIn('class="order-route-flag"', invalid_page)

    def test_linked_export_route_uses_owner_for_overview_and_shipping_when_copy_is_stale(self):
        group = TradeGroup(group_no="TRI-ROUTE-OWNER")
        db.session.add(group); db.session.flush()
        customer = self.pi("ROUTE-CUSTOMER", status="PRE_SHIPMENT", group=group, role="CUSTOMER_ORDER")
        export = self.pi("ROUTE-EXPORT", status="PRE_SHIPMENT", group=group, role="EXPORT_ORDER")
        customer.loading_port, customer.destination_port = "Shanghai", "Mombasa"
        export.loading_port, export.destination_port = "Ningbo", "Jebel Ali"
        db.session.commit()

        page = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)

        overview = page.split('id="order-overview"', 1)[1].split('</section>', 1)[0]
        shipping = page.split('id="shipping"', 1)[1].split('</section>', 1)[0]
        self.assertIn("Shanghai → Mombasa", overview)
        self.assertNotIn("Ningbo → Jebel Ali", overview)
        self.assertIn("Shanghai", shipping)
        self.assertIn("Mombasa", shipping)
        self.assertNotIn("Ningbo", shipping)
        self.assertNotIn("Jebel Ali", shipping)

    def test_mixed_units_do_not_create_a_misleading_combined_total(self):
        pi = self.pi("SUMMARY-MIXED-UNITS")
        pi.items.append(PIItem(unit_price=Decimal("10"), quantity=Decimal("4"), quantity_unit="KGS",
                               line_total=Decimal("40"), product_model_snapshot="R-2195"))
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertIn("Multiple units — shown per item", page)
        self.assertIn("R-996 · 10.000 MT", page)
        self.assertIn("R-2195 · 4.000 KGS", page)

    def test_current_action_uses_existing_task_actions_and_module_anchors(self):
        pi = self.pi("CURRENT-ACTION", status="SHIPPED", received="0")
        task = OrderTask(
            pi_id=pi.id, task_code="PAYMENT_ADVANCE_WAITING", title="等待客户支付预付款",
            source="AUTO", status="ACTION", health="NORMAL", completion_mode="RULE_DATA",
            priority=10, context_payload={"currency": "USD", "expected_amount": "100.00"},
            dedupe_key="current-action",
        )
        arrival = OrderTask(
            pi_id=pi.id, task_code="SHIPPING_ACTUAL_ARRIVAL", title="确认货物已到港",
            source="AUTO", status="ACTION", health="NORMAL", completion_mode="RULE_DATA",
            priority=20, context_payload={"action_target": "ENTER_ARRIVED"},
            dedupe_key="current-arrival",
        )
        db.session.add_all((task, arrival)); db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertIn('id="current-action"', page)
        self.assertIn('/advance-receipt', page)
        self.assertIn('/enter-arrived', page)
        for anchor in ("#shipping", "#customer-payment", "#freight", "#documents",
                       "#generated-files", "#tasks", "#activity"):
            self.assertIn(f'href="{anchor}"', page)

    def test_raw_server_html_has_prototype_module_order_without_relocation_architecture(self):
        pi = self.pi("STRUCTURE", status="PRE_SHIPMENT")
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        markers = [
            'id="order-identity"', 'id="order-lifecycle"', 'class="order-nav"',
            'id="current-action"', 'id="order-overview"', 'id="shipping"',
            'id="customer-payment"', 'id="freight"', 'id="documents"',
            'id="generated-files"', 'id="tasks"', 'id="activity"',
        ]
        positions = [page.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        for legacy in ("order-module-flow", "data-order-module", "Tasks & Activity",
                       "Post-shipment Reference", "Commercial details", "PI item execution details"):
            self.assertNotIn(legacy, page)
        script = (Path(__file__).parents[1] / "v2/static/v2_batch1.js").read_text()
        for relocation in ("appendChild", ".append(", "insertAdjacentElement", "data-order-module"):
            self.assertNotIn(relocation, script)

    def test_current_action_uses_selector_priority_and_hides_secondary_projection(self):
        pi = self.pi("FOCUSED-ACTIONS", status="SHIPPED")
        exception = OrderTask(
            pi_id=pi.id, task_code="MANUAL-EXCEPTION", title="Exception first",
            source="MANUAL", status="ACTION", health="EXCEPTION", completion_mode="MANUAL",
            priority=90, context_payload={"message": "Critical fact"}, dedupe_key="focus-exception",
        )
        routine = OrderTask(
            pi_id=pi.id, task_code="MANUAL-ACTION", title="Routine second",
            source="MANUAL", status="ACTION", health="NORMAL", completion_mode="MANUAL",
            priority=1, context_payload={}, dedupe_key="focus-routine",
        )
        waiting = OrderTask(
            pi_id=pi.id, task_code="MANUAL-WAITING", title="Waiting third",
            source="MANUAL", status="WAITING", health="NORMAL", completion_mode="MANUAL",
            priority=1, waiting_on="CUSTOMER", next_follow_up_at=datetime(2026, 9, 20),
            context_payload={}, dedupe_key="focus-waiting",
        )
        db.session.add_all((routine, waiting, exception)); db.session.commit()

        with patch("v2.selector.utcnow", return_value=datetime(2026, 9, 16, 9)):
            page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        primary = page.split('data-current-action-primary', 1)[1].split('</article>', 1)[0]
        self.assertIn("Exception first", primary)
        self.assertNotIn("Routine second", primary)
        self.assertEqual(page.count("data-current-action-primary"), 1)
        self.assertIn('id="current-action-secondary" hidden', page)
        self.assertIn('aria-expanded="false"', page)
        self.assertIn("Exception 1", page)
        self.assertIn("Action 1", page)
        self.assertIn("Waiting 1", page)
        self.assertIn("Waiting On: CUSTOMER", page)
        self.assertIn("Next Follow-up:", page)

    def test_shipping_has_exactly_five_groups_and_preserves_form_boundaries(self):
        pi = self.pi("SHIPPING-GROUPS", status="PRE_SHIPMENT")
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertEqual(page.count("data-shipping-group"), 5)
        for title in ("装运计划", "货物与集装箱", "订舱与船务", "司机与装柜执行", "路线与单证资料"):
            self.assertIn(title, page)
        summary = page.split('class="order-shipping-summary"', 1)[1].split('</dl>', 1)[0]
        for label in ("计划装柜时间", "ETD / ETA", "实际发运", "实际到达"):
            self.assertIn(label, summary)
        self.assertNotIn("Container Type", summary)
        self.assertIn(f'<form id="shipment-preparation" method="post" action="/v2/orders/{pi.id}/facts"', page)
        self.assertIn('form="shipment-preparation" class="form-control" name="container_type"', page)
        self.assertIn(f'<form id="booking-cargo-information" method="post" action="/v2/orders/{pi.id}/facts"', page)
        self.assertIn(f'<form id="shipping-schedule" method="post" action="/v2/orders/{pi.id}/facts"', page)

    def test_cargo_edit_on_demand_keeps_the_existing_scoped_form_and_csrf_contract(self):
        pi = self.pi("CARGO-EDIT", status="PRE_SHIPMENT")
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertIn('data-cargo-edit aria-controls="cargo-edit-workspace" aria-expanded="false"', page)
        self.assertIn(f'<form id="booking-cargo-information" method="post" action="/v2/orders/{pi.id}/facts" class="order-control-block" data-cargo-form data-v2-inplace-save data-v2-inplace-scope="CARGO">', page)
        cargo = page.split('id="booking-cargo-information"', 1)[1].split('</form>', 1)[0]
        self.assertIn('name="csrf_token"', cargo)
        for name in ('package_count', 'package_unit', 'gross_weight', 'gross_weight_display_unit', 'volume'):
            self.assertIn(f'name="{name}"', cargo)
        script = (Path(__file__).parents[1] / "v2/static/v2_batch1.js").read_text()
        self.assertIn('const expanded = cargoEdit.getAttribute("aria-expanded") === "true"', script)
        self.assertIn('cargoWorkspace.hidden = !expanded', script)
        self.assertIn('cargoEdit.textContent = expanded ? "收起 / Collapse" : "编辑 / Edit"', script)
        self.assertNotIn('cargoForm.reset()', script)
        self.assertIn('headers: { Accept: "application/json", "X-V2-Inplace-Save": "1" }', script)

    def test_activity_is_collapsed_native_disclosure_with_hash_reveal_hook(self):
        pi = self.pi("ACTIVITY-DISCLOSURE")
        task = self.legacy_advance_task(pi)
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        activity = page.split('<details id="activity"', 1)[1].split('</details>', 1)[0]
        self.assertNotIn(' open', activity.split('>', 1)[0])
        self.assertIn('<summary class="order-activity-summary">', activity)
        self.assertIn('任务活动 · 0 项', activity)
        script = (Path(__file__).parents[1] / "v2/static/v2_batch1.js").read_text()
        self.assertIn('window.location.hash !== "#activity"', script)
        self.assertIn('window.addEventListener("hashchange", revealActivityFromHash)', script)

    def test_freight_shows_historical_agreement_and_current_shipment_forwarder_when_they_differ(self):
        historical = FreightForwarder(code="AGREEMENT", name="Agreement Forwarder")
        current = FreightForwarder(code="CURRENT", name="Current Forwarder")
        group = TradeGroup(group_no="TRI-FORWARDER-PRESENTATION")
        db.session.add_all((historical, current, group)); db.session.flush()
        customer = self.pi("FORWARDER-CUSTOMER", status="PRE_SHIPMENT", group=group, role="CUSTOMER_ORDER")
        export = self.pi("FORWARDER-EXPORT", status="PRE_SHIPMENT", group=group, role="EXPORT_ORDER")
        customer.freight_forwarder_id = current.id
        db.session.add(OrderFreightAgreement(pi_id=customer.id, freight_forwarder_id=historical.id,
                                             freight_forwarder_name_snapshot=historical.name,
                                             amount=Decimal("100.00"), currency="USD"))
        db.session.commit()

        page = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)

        self.assertIn('Freight Forwarder</span><strong>Current Forwarder</strong>', page)
        self.assertNotIn('Agreement Forwarder</span>', page)

    def test_order_detail_consumes_dashboard_v35_tokens_instead_of_od_tokens(self):
        pi = self.pi("DASHBOARD-TOKENS")
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertIn('v2_design_system.css', page)
        self.assertIn('v2-modern', page)
        design_system = (Path(__file__).parents[1] / 'v2/static/v2_design_system.css').read_text()
        for token in ('--v2-color-primary:#153b64', '--v2-color-border:#e2e8f0',
                      '--v2-color-muted:#62728a', '--v2-radius-lg:16px',
                      'padding:17px 20px', '--v2-radius-sm:9px'):
            self.assertIn(token, design_system)
        self.assertNotIn('--od-', page)

    def test_documents_generated_files_tasks_and_activity_are_distinct_modules(self):
        pi = self.pi("OPERATIONS", status="SHIPPED")
        pi.coo_required = True
        task = OrderTask(
            pi_id=pi.id, task_code="DOCUMENT_COO", title="办理 COO", source="AUTO",
            status="ACTION", health="NORMAL", completion_mode="MANUAL", priority=20,
            context_payload={}, dedupe_key="document-coo",
        )
        db.session.add(task); db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        self.assertIn("COO / Certificate of Origin", page)
        self.assertIn("需要", page)
        self.assertIn("ACTION", page)
        for backend_name in ("coo_required", "apta_required", "export_license_required", "customs_docs_required"):
            self.assertNotIn(backend_name, page)
        self.assertEqual(page.count('id="tasks"'), 1)
        self.assertEqual(page.count('id="activity"'), 1)
        self.assertNotIn("Tasks & Activity", page)
        self.assertIn(f'/v2/orders/{pi.id}/documents/pi', page)

    def test_completed_order_has_no_current_action_control(self):
        pi = self.pi("COMPLETED-READ-ONLY", status="COMPLETED")
        task = OrderTask(
            pi_id=pi.id, task_code="MANUAL-LEFTOVER", title="Historical active task",
            source="MANUAL", status="ACTION", health="NORMAL", completion_mode="MANUAL",
            priority=1, context_payload={}, dedupe_key="completed-read-only",
        )
        db.session.add(task); db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        current_action = page.split('id="current-action"', 1)[1].split('</section>', 1)[0]
        self.assertIn("当前待处理 · 0项", current_action)
        self.assertNotIn(f'/v2/tasks/{task.id}/done', current_action)
        self.assertIn('id="correction-workflow"', page)
