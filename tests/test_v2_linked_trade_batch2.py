"""Role-aware linked-trade workflow checks (Batch 2)."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.linked_trade import financial_owner_for
from v2.models import (Customer, Exporter, FreightForwarder, FreightQuote,
                       FreightSettlement, OrderCorrectionSession, OrderFreightAgreement,
                       OrderTask, PI, PIItem, TaskActivity, TradeGroup, User, db)
from v2.services import EXPORT_FINANCIAL_TASK_CODES, completion_check, reconcile_order_tasks_for_pi
from v2.web import _shipped_gate_is_ready


class LinkedTradeRoleAwareWorkflowTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'linked-b2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="role-aware", password_hash=generate_password_hash("pw"))
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

    def pi(self, number, *, group=None, role=None, status="NEW"):
        pi = PI(pi_no=number, pi_date=date(2026, 9, 5), order_type="SALES", status=status,
                customer_id=self.customer.id, exporter_id=self.exporter.id,
                customer_name_snapshot=self.customer.name, exporter_name_snapshot=self.exporter.name,
                currency="USD", planned_shipment_date=date(2026, 9, 20),
                trade_group=group, trade_role=role)
        pi.items.append(PIItem(unit_price=Decimal("100"), quantity=Decimal("1"), quantity_unit="MT", line_total=Decimal("100")))
        db.session.add(pi); db.session.flush()
        return pi

    def pair(self, *, export_status="NEW"):
        group = TradeGroup(group_no="TRI-B2")
        db.session.add(group); db.session.flush()
        owner = self.pi("WU-B2", group=group, role="CUSTOMER_ORDER")
        export = self.pi("XHT-B2", group=group, role="EXPORT_ORDER", status=export_status)
        db.session.commit()
        return owner, export

    def task(self, pi, code, status="ACTION"):
        task = OrderTask(pi_id=pi.id, task_code=code, title=code, source="AUTO", status=status,
                         health="NORMAL", completion_mode="RULE_DATA",
                         dedupe_key=f"v2:order:{pi.id}:{code.lower()}")
        db.session.add(task); db.session.flush(); return task

    def prepare_gate(self, pi):
        pi.status = "PRE_SHIPMENT"
        pi.container_loading_date = date(2026, 9, 20)
        self.task(pi, "SHIPPING_CONTAINER_LOADING", "DONE")

    def agreement(self, pi):
        row = OrderFreightAgreement(
            pi_id=pi.id, freight_forwarder_name_snapshot="FF", amount=Decimal("25"),
            currency="USD", agreed_at=datetime(2026, 9, 5),
        )
        db.session.add(row); db.session.flush(); return row

    def test_financial_owner_resolution_normal_customer_export_and_orphan(self):
        normal = self.pi("XHT-NORMAL")
        owner, export = self.pair()
        orphan_group = TradeGroup(group_no="TRI-ORPHAN"); db.session.add(orphan_group); db.session.flush()
        orphan = self.pi("XHT-ORPHAN", group=orphan_group, role="EXPORT_ORDER")
        self.assertIs(financial_owner_for(normal).owner, normal)
        self.assertIs(financial_owner_for(owner).owner, owner)
        self.assertIs(financial_owner_for(export).owner, owner)
        self.assertFalse(financial_owner_for(orphan).valid)
        self.assertIn("no CUSTOMER_ORDER", financial_owner_for(orphan).error)

    def test_export_reconcile_cancels_financial_tasks_without_peer_side_effects(self):
        owner, export = self.pair()
        peer_task = self.task(owner, "PAYMENT_EMAIL")
        export_task = self.task(export, "PAYMENT_EMAIL")
        db.session.commit()
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20, 12))
        self.assertEqual(export_task.status, "CANCELLED")
        self.assertEqual(peer_task.status, "ACTION")
        self.assertIsNone(db.session.get(OrderFreightAgreement, export.id))
        self.assertIsNone(db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id == export.id)))

    def test_export_forged_financial_posts_and_corrections_are_rejected(self):
        _, export = self.pair(export_status="SHIPPED")
        client = self.client()
        for url, payload in (
            (f"/v2/orders/{export.id}/advance-receipt", {"advance_received_amount": "10", "advance_received_at": "2026-09-05"}),
            (f"/v2/orders/{export.id}/facts", {"advance_payment_amount": "10"}),
            (f"/v2/orders/{export.id}/facts", {"advance_received_amount": "10"}),
            (f"/v2/orders/{export.id}/facts", {"_form_scope": "FREIGHT_SETTLEMENT", "usd_bill_amount": "10"}),
            (f"/v2/orders/{export.id}/facts", {"_form_scope": "FREIGHT_SETTLEMENT", "usd_payment_status": "PAID", "usd_paid_at": "2026-09-05"}),
            (f"/v2/orders/{export.id}/facts", {"quote_id": "1"}),
            (f"/v2/orders/{export.id}/facts", {"usd_bill_confirmed": "true"}),
            (f"/v2/orders/{export.id}/facts", {"usd_invoice_issued": "true"}),
            (f"/v2/orders/{export.id}/freight-agreement", {"amount": "10", "currency": "USD"}),
            (f"/v2/orders/{export.id}/corrections", {"module": "PAYMENT", "reason": "test"}),
            (f"/v2/orders/{export.id}/corrections", {"module": "FREIGHT", "reason": "test"}),
        ):
            self.assertEqual(client.post(url, data=payload).status_code, 409)
        export.status = "PRE_SHIPMENT"; db.session.commit()
        self.assertEqual(client.post(f"/v2/orders/{export.id}/facts", data={"container_location": "Shanghai"}).status_code, 302)

    def test_export_stage_gate_uses_linked_agreement_without_own_freight_task(self):
        owner, export = self.pair(export_status="PRE_SHIPMENT")
        export.container_loading_date = date(2026, 9, 20)
        self.task(export, "SHIPPING_CONTAINER_LOADING", "DONE")
        db.session.add(OrderFreightAgreement(pi_id=owner.id, freight_forwarder_name_snapshot="FF", amount=Decimal("25"), currency="USD", agreed_at=datetime(2026, 9, 5)))
        db.session.commit()
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20, 12))
        gate = db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == export.id, OrderTask.task_code == "STAGE_GATE_SHIPPED"))
        self.assertEqual(gate.status, "ACTION")
        self.assertTrue(_shipped_gate_is_ready(export))
        self.assertIsNone(db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id == export.id)))
        self.assertIsNone(db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == export.id, OrderTask.task_code == "SHIPPING_FREIGHT_AGREEMENT", OrderTask.status != "CANCELLED")))

    def test_export_completion_uses_peer_as_na_and_orphan_fails(self):
        owner, export = self.pair(export_status="ARRIVED")
        export.telex_release_required = False
        check = completion_check(export)
        self.assertTrue(check["overall_complete"])
        self.assertIs(check["payment"]["managed_by"], owner)
        page = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)
        self.assertIn(f"/v2/orders/{owner.id}", page)
        self.assertIn("Linked Financial Owner", page)
        orphan_group = TradeGroup(group_no="TRI-COMPLETE-ORPHAN"); db.session.add(orphan_group); db.session.flush()
        orphan = self.pi("XHT-COMPLETE-ORPHAN", group=orphan_group, role="EXPORT_ORDER", status="ARRIVED")
        orphan.telex_release_required = False
        self.assertFalse(completion_check(orphan)["overall_complete"])
        self.assertIn("no CUSTOMER_ORDER", completion_check(orphan)["payment"]["configuration_error"])

    def test_export_page_shows_linked_financial_owner_read_only(self):
        owner, export = self.pair(export_status="SHIPPED")
        db.session.add(OrderFreightAgreement(pi_id=owner.id, freight_forwarder_name_snapshot="FF", amount=Decimal("25"), currency="USD", agreed_at=datetime(2026, 9, 5)))
        db.session.add(FreightSettlement(pi_id=owner.id, usd_bill_required=True, usd_bill_amount=Decimal("30"), usd_payment_status="PAID", usd_paid_at=datetime(2026, 9, 6)))
        db.session.commit()
        page = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)
        self.assertIn("Linked Financial Owner", page)
        self.assertIn(owner.pi_no, page)
        self.assertIn(f"/v2/orders/{owner.id}", page)
        self.assertNotIn('id="payment-receipts"', page)
        self.assertNotIn('id="freight-settlement"', page)

    def test_pre_shipment_export_shows_read_only_linked_financial_owner(self):
        owner, export = self.pair(export_status="PRE_SHIPMENT")
        self.agreement(owner)
        db.session.add(FreightSettlement(pi_id=owner.id, usd_bill_required=True,
                                         usd_bill_amount=Decimal("1234.56"), usd_payment_status="UNPAID"))
        db.session.commit()
        page = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)
        self.assertIn('id="linked-financial-owner"', page)
        self.assertIn("Linked Financial Owner", page)
        self.assertIn(owner.pi_no, page)
        self.assertIn("Customer Payment:</b> Managed by linked customer order", page)
        self.assertIn("Freight Finance:</b> Managed by linked customer order", page)
        self.assertIn("USD 25", page)
        self.assertIn(f"/v2/orders/{owner.id}", page)
        self.assertNotIn('id="payment-receipts"', page)
        self.assertNotIn('id="freight-settlement"', page)
        self.assertNotIn('name="advance_received_amount"', page)

    def test_pre_shipment_customer_and_independent_keep_existing_finance_visibility(self):
        customer_order, _ = self.pair(export_status="PRE_SHIPMENT")
        independent = self.pi("INDEPENDENT-PRE", status="PRE_SHIPMENT")
        db.session.commit()
        for pi in (customer_order, independent):
            page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)
            self.assertNotIn('id="linked-financial-owner"', page)
            self.assertNotIn('id="payment-receipts"', page)
            self.assertNotIn('id="freight-settlement"', page)

    def test_pre_shipment_orphan_displays_financial_owner_configuration_error(self):
        group = TradeGroup(group_no="TRI-PRE-UI-ORPHAN"); db.session.add(group); db.session.flush()
        orphan = self.pi("XHT-PRE-UI-ORPHAN", group=group, role="EXPORT_ORDER", status="PRE_SHIPMENT")
        db.session.commit()
        page = self.client().get(f"/v2/orders/{orphan.id}").get_data(as_text=True)
        self.assertIn('id="linked-financial-owner"', page)
        self.assertIn("Linked trade configuration problem", page)
        self.assertIn("no CUSTOMER_ORDER", page)

    def test_normal_xht_and_customer_order_retain_financial_task_workflow(self):
        normal = self.pi("XHT-PREFIX-NORMAL")
        owner, _ = self.pair()
        for pi in (normal, owner):
            pi.advance_payment_amount = Decimal("40")
            reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 10, 12))
            advance = db.session.scalar(db.select(OrderTask).where(
                OrderTask.pi_id == pi.id, OrderTask.task_code == "PAYMENT_ADVANCE_WAITING"))
            self.assertIsNotNone(advance)
            self.assertNotEqual(advance.status, "CANCELLED")
        client = self.client()
        for pi in (normal, owner):
            response = client.post(f"/v2/orders/{pi.id}/advance-receipt", data={
                "advance_received_amount": "40", "advance_received_at": "2026-09-10"})
            self.assertEqual(response.status_code, 302)

    def test_export_suppresses_all_financial_codes_and_preserves_history(self):
        owner, export = self.pair(export_status="SHIPPED")
        peer_task = self.task(owner, "PAYMENT_EMAIL")
        for code in EXPORT_FINANCIAL_TASK_CODES:
            task = self.task(export, code)
            db.session.add(TaskActivity(task_id=task.id, event_type="CREATED", to_status="ACTION", actor_type="SYSTEM"))
        db.session.commit()
        before_history = db.session.scalar(db.select(db.func.count(TaskActivity.id)).join(OrderTask).where(OrderTask.pi_id == export.id))
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20, 12)); db.session.flush()
        rows = list(db.session.scalars(db.select(OrderTask).where(
            OrderTask.pi_id == export.id, OrderTask.task_code.in_(EXPORT_FINANCIAL_TASK_CODES))))
        self.assertTrue(rows)
        self.assertTrue(all(row.status == "CANCELLED" for row in rows))
        after_history = db.session.scalar(db.select(db.func.count(TaskActivity.id)).join(OrderTask).where(OrderTask.pi_id == export.id))
        self.assertGreater(after_history, before_history)
        self.assertEqual(peer_task.status, "ACTION")

    def test_export_gate_blocks_missing_agreement_quote_only_and_orphan(self):
        forwarder = FreightForwarder(code="FF", name="Forwarder")
        db.session.add(forwarder); db.session.flush()
        owner, export = self.pair()
        self.prepare_gate(export)
        db.session.add(FreightQuote(freight_forwarder_id=forwarder.id, departure_port="SHA",
                                    destination_port="LAX", amount=Decimal("25"), currency="USD"))
        db.session.commit()
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20, 12))
        self.assertFalse(_shipped_gate_is_ready(export))
        gate = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == export.id, OrderTask.task_code == "STAGE_GATE_SHIPPED"))
        self.assertIn("no final accepted freight agreement", " ".join(gate.context_payload["missing_preparation"]))
        orphan_group = TradeGroup(group_no="TRI-GATE-ORPHAN"); db.session.add(orphan_group); db.session.flush()
        orphan = self.pi("XHT-GATE-ORPHAN", group=orphan_group, role="EXPORT_ORDER")
        self.prepare_gate(orphan); db.session.flush()
        reconcile_order_tasks_for_pi(orphan, now=datetime(2026, 9, 20, 12))
        self.assertFalse(_shipped_gate_is_ready(orphan))
        orphan_gate = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == orphan.id, OrderTask.task_code == "STAGE_GATE_SHIPPED"))
        self.assertIn("no CUSTOMER_ORDER", " ".join(orphan_gate.context_payload["missing_preparation"]))

    def test_normal_and_customer_gate_still_require_own_done_task_and_agreement(self):
        normal = self.pi("NORMAL-GATE")
        owner, _ = self.pair()
        for pi in (normal, owner):
            self.prepare_gate(pi)
            reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 20, 12)); db.session.flush()
            self.agreement(pi)
            reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 20, 12)); db.session.flush()
            freight_task = db.session.scalar(db.select(OrderTask).where(
                OrderTask.pi_id == pi.id, OrderTask.task_code == "SHIPPING_FREIGHT_AGREEMENT"))
            self.assertEqual(freight_task.status, "DONE")
            reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, 20, 12)); db.session.flush()
            self.assertTrue(_shipped_gate_is_ready(pi))

    def test_telex_uses_peer_payment_and_orphan_fails_visibly(self):
        owner, export = self.pair(export_status="SHIPPED")
        export.telex_release_required = True
        export.original_documents_mail_required = True
        export.original_bl_required = True
        export.advance_received_amount = Decimal("100")  # Must not control readiness.
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20, 12))
        telex = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == export.id, OrderTask.task_code == "DOCUMENT_TELEX_RELEASE"))
        self.assertEqual(telex.status, "UPCOMING")
        mail = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == export.id, OrderTask.task_code == "ORIGINAL_DOCUMENTS_MAIL"))
        self.assertEqual(mail.status, "UPCOMING")
        owner.advance_received_amount = Decimal("100")
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20, 12))
        self.assertEqual(telex.status, "ACTION")
        self.assertEqual(mail.status, "UPCOMING")
        orphan_group = TradeGroup(group_no="TRI-TELEX-ORPHAN"); db.session.add(orphan_group); db.session.flush()
        orphan = self.pi("XHT-TELEX-ORPHAN", group=orphan_group, role="EXPORT_ORDER", status="SHIPPED")
        orphan.telex_release_required = True; orphan.advance_received_amount = Decimal("100")
        reconcile_order_tasks_for_pi(orphan, now=datetime(2026, 9, 20, 12))
        orphan_telex = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == orphan.id, OrderTask.task_code == "DOCUMENT_TELEX_RELEASE"))
        self.assertEqual((orphan_telex.status, orphan_telex.health), ("ACTION", "EXCEPTION"))
        self.assertIn("no CUSTOMER_ORDER", orphan_telex.context_payload["message"])

    def test_completion_documents_and_post_recompute_cannot_be_bypassed(self):
        owner, export = self.pair(export_status="ARRIVED")
        export.original_documents_mail_required = True
        db.session.commit()
        self.assertFalse(completion_check(export)["overall_complete"])
        response = self.client().post(f"/v2/orders/{export.id}/enter-completed")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(export.status, "ARRIVED")
        self.assertEqual(owner.status, "NEW")
        export.original_documents_mail_required = False; db.session.commit()
        response = self.client().post(f"/v2/orders/{export.id}/enter-completed")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(export.status, "COMPLETED")
        self.assertEqual(owner.status, "NEW")

    def test_orphan_ui_writes_gate_and_completion_all_fail_safely(self):
        group = TradeGroup(group_no="TRI-ALL-ORPHAN"); db.session.add(group); db.session.flush()
        orphan = self.pi("XHT-ALL-ORPHAN", group=group, role="EXPORT_ORDER", status="ARRIVED")
        db.session.commit(); client = self.client()
        page = client.get(f"/v2/orders/{orphan.id}").get_data(as_text=True)
        self.assertIn("Linked trade configuration problem", page)
        self.assertEqual(client.post(f"/v2/orders/{orphan.id}/advance-receipt", data={
            "advance_received_amount": "1", "advance_received_at": "2026-09-05"}).status_code, 409)
        self.assertEqual(client.post(f"/v2/orders/{orphan.id}/facts", data={
            "_form_scope": "FREIGHT_SETTLEMENT", "usd_bill_amount": "1"}).status_code, 409)
        self.assertFalse(completion_check(orphan)["overall_complete"])
        self.assertEqual(client.post(f"/v2/orders/{orphan.id}/enter-completed").status_code, 409)
        orphan.status = "PRE_SHIPMENT"; self.prepare_gate(orphan); db.session.flush()
        reconcile_order_tasks_for_pi(orphan, now=datetime(2026, 9, 20, 12))
        self.assertFalse(_shipped_gate_is_ready(orphan))

    def test_direct_financial_task_actions_are_rejected(self):
        _, export = self.pair(export_status="SHIPPED")
        client = self.client()
        for code in ("PAYMENT_EMAIL", "FREIGHT_USD_AMOUNT_CONFIRM", "FREIGHT_USD_INVOICE_ISSUED", "FREIGHT_USD_PAYMENT_CONFIRM"):
            task = self.task(export, code); db.session.commit()
            self.assertEqual(client.post(f"/v2/tasks/{task.id}/done").status_code, 409)
            self.assertEqual(task.status, "ACTION")

    def test_export_allows_nonfinancial_completed_correction(self):
        _, export = self.pair(export_status="COMPLETED")
        response = self.client().post(f"/v2/orders/{export.id}/corrections", data={
            "module": "SHIPPING", "reason": "Correct carrier"})
        self.assertEqual(response.status_code, 302)
        session = db.session.scalar(db.select(OrderCorrectionSession).where(OrderCorrectionSession.pi_id == export.id))
        self.assertEqual(session.module, "SHIPPING")

    def test_export_operations_do_not_mutate_peer_snapshot(self):
        owner, export = self.pair(export_status="PRE_SHIPMENT")
        owner.advance_received_amount = Decimal("12")
        agreement = self.agreement(owner)
        settlement = FreightSettlement(pi_id=owner.id, usd_bill_required=True,
                                       usd_bill_amount=Decimal("30"), usd_payment_status="UNPAID")
        peer_task = self.task(owner, "PAYMENT_EMAIL")
        db.session.add(settlement); db.session.commit()
        before = (owner.status, owner.advance_received_amount, agreement.amount,
                  settlement.usd_bill_amount, settlement.usd_payment_status,
                  peer_task.id, peer_task.status,
                  db.session.scalar(db.select(db.func.count(TaskActivity.id)).join(OrderTask).where(OrderTask.pi_id == owner.id)))
        self.prepare_gate(export); db.session.flush()
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20, 12))
        self.assertTrue(_shipped_gate_is_ready(export))
        response = self.client().post(f"/v2/orders/{export.id}/enter-shipped", data={
            "actual_departure_date": "2026-09-20", "shipping_company": "Carrier", "bill_of_lading_number": "BL-1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(export.status, "SHIPPED")
        after = (owner.status, owner.advance_received_amount, agreement.amount,
                 settlement.usd_bill_amount, settlement.usd_payment_status,
                 peer_task.id, peer_task.status,
                 db.session.scalar(db.select(db.func.count(TaskActivity.id)).join(OrderTask).where(OrderTask.pi_id == owner.id)))
        self.assertEqual(after, before)
