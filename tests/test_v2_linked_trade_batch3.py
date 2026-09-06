"""Create-linked-export workflow: independent execution PI, atomic relationship."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.linked_trade import financial_owner_for
from v2.models import (Customer, Exporter, Factory, FreightSettlement, OrderCorrectionSession, OrderFreightAgreement,
                       OrderTask, PI, PIItem, Product, ProductBatch, TaskActivity, TradeGroup, User, db)
from v2.linked_trade_creation import create_linked_export_order


class LinkedTradeCreateExportTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'linked-b3.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="linked-create", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="CUS-B3", name="Commercial Customer", active=True)
        self.export_customer = Customer(code="CUS-EXP", name="Export Customer", active=True)
        self.exporter = Exporter(code="EXP-B3", name="Commercial Seller", active=True)
        self.export_seller = Exporter(code="EXP-EXPORT", name="Export Seller", active=True)
        self.factory = Factory(code="FAC-B3", name="Factory", active=True)
        self.product = Product(code="PRD-B3", model="Product B3", hs_code="810890", active=True)
        db.session.add_all((self.user, self.customer, self.export_customer, self.exporter,
                            self.export_seller, self.factory, self.product)); db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def source(self, number="WU-B3", *, status="NEW", multi=False):
        pi = PI(pi_no=number, pi_date=date(2026, 9, 6), order_type="SALES", status=status,
                customer_id=self.customer.id, exporter_id=self.exporter.id,
                customer_name_snapshot=self.customer.name, exporter_name_snapshot=self.exporter.name,
                currency="USD", payment_terms="T/T", planned_shipment_date=date(2026, 9, 30),
                loading_port="SHA", destination_port="LAX", container_loading_date=date(2026, 9, 25),
                container_loading_period="AM", container_location="Shanghai", container_type="20GP",
                container_count=1, shipping_mark="MARK", vessel_info="Vessel", booking_number="BOOK",
                etd=date(2026, 9, 29), eta=date(2026, 10, 15), actual_departure_date=date(2026, 9, 29),
                actual_arrival_date=date(2026, 10, 15), bill_of_lading_number="BL", shipping_company="Carrier",
                container_number="CONTAINER", seal_number="SEAL", vgm_kg=Decimal("100"),
                advance_received_amount=Decimal("10"), advance_payment_amount=Decimal("10"))
        item = PIItem(product_id=self.product.id, factory_id=self.factory.id, trade_term="CIF",
                      unit_price=Decimal("999"), quantity=Decimal("2"), quantity_unit="MT",
                      line_total=Decimal("1998"), product_model_snapshot="Product B3",
                      product_hs_code_snapshot="810890", factory_name_snapshot="Factory")
        pi.items.append(item); db.session.add(pi); db.session.flush()
        if multi:
            pi.items.append(PIItem(product_id=self.product.id, factory_id=self.factory.id, trade_term="CIF",
                unit_price=Decimal("987.65"), quantity=Decimal("3"), quantity_unit="BAGS",
                line_total=Decimal("2962.95"), product_model_snapshot="Product second",
                product_hs_code_snapshot="SECOND-HS", factory_name_snapshot="Factory")); db.session.flush()
        db.session.add(ProductBatch(pi_item_id=item.id, batch_number="BATCH-OLD"))
        db.session.add(OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="FF", amount=Decimal("1"), currency="USD"))
        db.session.add(FreightSettlement(pi_id=pi.id, usd_bill_required=True, usd_bill_amount=Decimal("2"), usd_payment_status="PAID"))
        task = OrderTask(pi_id=pi.id, task_code="MANUAL", title="Manual", source="MANUAL", status="ACTION", health="NORMAL", completion_mode="MANUAL", dedupe_key=f"b3-{pi.id}")
        db.session.add(task); db.session.flush(); db.session.add(TaskActivity(task_id=task.id, event_type="CREATED", actor_type="SYSTEM"))
        db.session.add(OrderCorrectionSession(pi_id=pi.id, module="COMMERCIAL", reason="source history", opened_by_id=self.user.id))
        db.session.commit(); return pi

    def payload(self, source, **extra):
        values = {"pi_no": "XHT-B3", "customer_id": str(self.export_customer.id),
                  "exporter_id": str(self.export_seller.id), "currency": "USD", "payment_terms": "OA90"}
        for item in source.items:
            values[f"unit_price_{item.id}"] = "123.45"; values[f"trade_term_{item.id}"] = "FOB"
        values.update(extra); return values

    def test_success_creates_independent_export_and_preserves_source_business_facts(self):
        source = self.source(); source_id = source.id
        response = self.client().post(f"/v2/orders/{source_id}/create-linked-export", data=self.payload(source), follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        source = db.session.get(PI, source_id)
        export = db.session.scalar(db.select(PI).where(PI.pi_no == "XHT-B3"))
        self.assertEqual((source.trade_role, source.include_in_business_stats), ("CUSTOMER_ORDER", True))
        self.assertEqual((export.trade_role, export.include_in_business_stats, export.status), ("EXPORT_ORDER", False, "NEW"))
        self.assertEqual(export.trade_group_id, source.trade_group_id)
        self.assertIs(financial_owner_for(export).owner, source)
        self.assertEqual((export.payment_terms, export.items[0].trade_term, export.items[0].unit_price), ("OA90", "FOB", Decimal("123.45")))
        self.assertEqual(export.items[0].line_total, Decimal("246.90"))
        self.assertEqual(export.items[0].product_hs_code_snapshot, source.items[0].product_hs_code_snapshot)
        self.assertEqual((export.loading_port, export.etd, export.eta, export.container_type), ("SHA", date(2026, 9, 29), date(2026, 10, 15), "20GP"))
        self.assertIsNone(export.actual_departure_date); self.assertIsNone(export.actual_arrival_date)
        self.assertIsNone(export.bill_of_lading_number); self.assertIsNone(export.container_number)
        self.assertEqual(source.items[0].unit_price, Decimal("999"))
        self.assertEqual(db.session.scalar(db.select(db.func.count(ProductBatch.id)).join(PIItem).where(PIItem.pi_id == export.id)), 0)
        self.assertIsNone(db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id == export.id)))
        self.assertIsNone(db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id == export.id)))
        self.assertEqual(db.session.scalar(db.select(FreightSettlement.usd_bill_amount).where(FreightSettlement.pi_id == source.id)), Decimal("2"))
        self.assertEqual(db.session.scalar(db.select(db.func.count(OrderTask.id)).where(OrderTask.pi_id == source.id)), 1)
        self.assertEqual(db.session.scalar(db.select(db.func.count(TaskActivity.id)).join(OrderTask).where(OrderTask.pi_id == source.id)), 1)

    def test_missing_or_zero_price_rolls_back_all_link_changes(self):
        source = self.source(); payload = self.payload(source, **{f"unit_price_{source.items[0].id}": "0"})
        response = self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=payload)
        self.assertEqual(response.status_code, 400)
        source = db.session.get(PI, source.id)
        self.assertIsNone(source.trade_group_id); self.assertIsNone(source.trade_role)
        self.assertEqual(db.session.scalar(db.select(db.func.count(TradeGroup.id))), 0)
        self.assertIsNone(db.session.scalar(db.select(PI).where(PI.pi_no == "XHT-B3")))

    def test_duplicate_number_and_direct_post_on_ineligible_sources_are_rejected(self):
        source = self.source(); duplicate = self.source("XHT-B3")
        self.assertEqual(self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source)).status_code, 400)
        completed = self.source("WU-COMPLETE", status="COMPLETED")
        self.assertEqual(self.client().get(f"/v2/orders/{completed.id}/create-linked-export").status_code, 409)
        group = TradeGroup(group_no="TRI-EXISTING"); db.session.add(group); db.session.flush()
        source.trade_group = group; source.trade_role = "CUSTOMER_ORDER"; db.session.commit()
        self.assertEqual(self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source, pi_no="XHT-OTHER")).status_code, 400)

    def test_entry_action_is_only_available_for_eligible_unlinked_sales_order(self):
        source = self.source(); client = self.client()
        self.assertIn('id="create-linked-export-order"', client.get(f"/v2/orders/{source.id}").get_data(as_text=True))
        group = TradeGroup(group_no="TRI-UI"); db.session.add(group); db.session.flush(); source.trade_group = group; source.trade_role = "EXPORT_ORDER"; db.session.commit()
        self.assertNotIn('id="create-linked-export-order"', client.get(f"/v2/orders/{source.id}").get_data(as_text=True))

    def test_forged_relationship_values_cannot_change_server_owned_roles(self):
        source = self.source(); data = self.payload(source, trade_role="CUSTOMER_ORDER", include_in_business_stats="true", trade_group_id="999")
        response = self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=data)
        self.assertEqual(response.status_code, 302)
        export = db.session.scalar(db.select(PI).where(PI.pi_no == "XHT-B3"))
        self.assertEqual(export.trade_role, "EXPORT_ORDER"); self.assertFalse(export.include_in_business_stats)
        self.assertEqual(export.trade_group_id, db.session.get(PI, source.id).trade_group_id)

    def test_party_and_price_guards_and_create_page_do_not_leak_source_sale_price(self):
        source = self.source(); client = self.client()
        page = client.get(f"/v2/orders/{source.id}/create-linked-export").get_data(as_text=True)
        self.assertIn("Source commercial PI", page); self.assertIn("Shipment Plan Prefill", page)
        self.assertIn("value=\"OA90\"", page); self.assertIn("value=\"FOB\"", page)
        self.assertNotIn('value="999"', page)
        payload = self.payload(source, customer_id="", **{f"unit_price_{source.items[0].id}": "not-a-number"})
        self.assertEqual(client.post(f"/v2/orders/{source.id}/create-linked-export", data=payload).status_code, 400)
        source = db.session.get(PI, source.id)
        self.assertIsNone(source.trade_group_id)

    def test_prices_and_shipment_plans_remain_independent_after_creation(self):
        source = self.source(); self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source))
        export = db.session.scalar(db.select(PI).where(PI.pi_no == "XHT-B3")); source = db.session.get(PI, source.id)
        source.items[0].unit_price = Decimal("777"); source.eta = date(2026, 11, 1)
        export.items[0].unit_price = Decimal("88"); export.eta = date(2026, 12, 1); db.session.commit()
        self.assertEqual(export.items[0].unit_price, Decimal("88")); self.assertEqual(source.items[0].unit_price, Decimal("777"))
        self.assertEqual(export.eta, date(2026, 12, 1)); self.assertEqual(source.eta, date(2026, 11, 1))

    def test_created_pair_is_protected_by_existing_linked_delete_guard(self):
        source = self.source(); self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source))
        export = db.session.scalar(db.select(PI).where(PI.pi_no == "XHT-B3"))
        self.assertEqual(self.client().post(f"/v2/orders/{source.id}/delete", data={"confirmation": source.pi_no}).status_code, 409)
        self.assertEqual(self.client().post(f"/v2/orders/{export.id}/delete", data={"confirmation": export.pi_no}).status_code, 409)

    def test_multi_item_prices_are_mapped_by_authoritative_item_id_and_missing_price_rolls_back(self):
        source = self.source(multi=True); items = list(source.items)
        page = self.client().get(f"/v2/orders/{source.id}/create-linked-export").get_data(as_text=True)
        self.assertNotIn("987.65", page)
        bad = self.payload(source); bad.pop(f"unit_price_{items[1].id}")
        self.assertEqual(self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=bad).status_code, 400)
        self.assertIsNone(db.session.get(PI, source.id).trade_group_id)
        good = self.payload(source, **{f"unit_price_{items[0].id}": "432.10", f"unit_price_{items[1].id}": "54.32"})
        self.assertEqual(self.client().post(f"/v2/orders/{source.id}/create-linked-export", data=good).status_code, 302)
        export = db.session.scalar(db.select(PI).where(PI.pi_no == "XHT-B3"))
        copied = {item.product_hs_code_snapshot: item for item in export.items}
        self.assertEqual(copied["810890"].unit_price, Decimal("432.10"))
        self.assertEqual(copied["SECOND-HS"].unit_price, Decimal("54.32"))

    def test_failure_injection_after_source_link_and_after_items_rolls_back_everything(self):
        source = self.source(multi=True); payload = self.payload(source)
        real_item = PIItem; calls = []
        def fail_second_item(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2: raise RuntimeError("item failure")
            return real_item(*args, **kwargs)
        with patch("v2.linked_trade_creation.PIItem", side_effect=fail_second_item):
            with self.assertRaises(RuntimeError): create_linked_export_order(source.id, payload)
        source = db.session.get(PI, source.id)
        self.assertIsNone(source.trade_group_id); self.assertIsNone(source.trade_role)
        self.assertEqual(db.session.scalar(db.select(db.func.count(TradeGroup.id))), 0)
        self.assertEqual(db.session.scalar(db.select(db.func.count(PI.id)).where(PI.pi_no == "XHT-B3")), 0)
        self.assertEqual(db.session.scalar(db.select(db.func.count(PIItem.id)).where(PIItem.pi_id != source.id)), 0)

    def test_reconcile_failure_rolls_back_new_group_export_tasks_and_source_link(self):
        source = self.source(); payload = self.payload(source)
        with patch("v2.linked_trade_creation.reconcile_order_tasks_for_pi", side_effect=RuntimeError("reconcile failure")):
            with self.assertRaises(RuntimeError): create_linked_export_order(source.id, payload)
        source = db.session.get(PI, source.id)
        self.assertIsNone(source.trade_group_id); self.assertIsNone(source.trade_role)
        self.assertEqual(db.session.scalar(db.select(db.func.count(TradeGroup.id))), 0)
        self.assertEqual(db.session.scalar(db.select(db.func.count(PI.id)).where(PI.pi_no == "XHT-B3")), 0)
        self.assertEqual(db.session.scalar(db.select(db.func.count(OrderTask.id)).where(OrderTask.pi_id != source.id)), 0)

    def test_inactive_missing_and_race_changed_parties_or_source_are_rejected(self):
        source = self.source(); client = self.client(); client.get(f"/v2/orders/{source.id}/create-linked-export")
        self.export_seller.active = False; db.session.commit()
        self.assertEqual(client.post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source)).status_code, 400)
        self.export_seller.active = True; db.session.commit()
        self.assertEqual(client.post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source, exporter_id="99999")).status_code, 400)
        source.status = "COMPLETED"; db.session.commit()
        self.assertEqual(client.post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source)).status_code, 400)

    def test_link_race_and_malformed_link_are_server_side_rejected(self):
        source = self.source(); client = self.client()
        self.assertEqual(client.get(f"/v2/orders/{source.id}/create-linked-export").status_code, 200)
        group = TradeGroup(group_no="TRI-RACE"); db.session.add(group); db.session.flush()
        source.trade_group = group; source.trade_role = "CUSTOMER_ORDER"; db.session.commit()
        self.assertEqual(client.post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source)).status_code, 400)
        self.assertEqual(client.get(f"/v2/orders/{source.id}/create-linked-export").status_code, 409)

    def test_eligible_lifecycle_statuses_create_new_export_and_completed_or_non_sales_do_not(self):
        client = self.client()
        for index, status in enumerate(("NEW", "PRE_SHIPMENT", "SHIPPED", "ARRIVED")):
            source = self.source(f"WU-STAGE-{status}", status=status)
            response = client.post(f"/v2/orders/{source.id}/create-linked-export", data=self.payload(source, pi_no=f"XHT-STAGE-{index}"))
            self.assertEqual(response.status_code, 302)
            export = db.session.scalar(db.select(PI).where(PI.pi_no == f"XHT-STAGE-{index}")); self.assertEqual(export.status, "NEW")
        commission = self.source("WU-COMMISSION"); commission.order_type = "COMMISSION"; db.session.commit()
        self.assertNotIn('create-linked-export-order', client.get(f"/v2/orders/{commission.id}").get_data(as_text=True))
        self.assertEqual(client.post(f"/v2/orders/{commission.id}/create-linked-export", data=self.payload(commission, pi_no="XHT-COMMISSION")).status_code, 400)
