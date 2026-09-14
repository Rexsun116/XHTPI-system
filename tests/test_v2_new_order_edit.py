"""Full NEW editor regression with disposable databases only."""
from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from tests import test_v2_create_pi_form as fixture
from v2.models import (BankAccount, Customer, Exporter, PI, PIItem, Product, ProductBatch,
                       OrderTask, TaskActivity, TradeGroup, db)
from v2.new_order_edit import edit_form_data


class NewOrderEditTest(TestCase):
    setUp = fixture.V2CreatePIFormTest.setUp
    tearDown = fixture.V2CreatePIFormTest.tearDown
    client = fixture.V2CreatePIFormTest.client
    form = fixture.V2CreatePIFormTest.form

    def order(self, number="EDIT"):
        self.assertEqual(self.client().post('/v2/orders/new', data=self.form(number)).status_code, 302)
        return db.session.scalar(db.select(PI).where(PI.pi_no == number))

    def post(self, pi, **changes):
        data = edit_form_data(pi); data.update(changes)
        return self.client().post(f'/v2/orders/{pi.id}/edit', data=data)

    def snapshot(self):
        return {table.name: list(db.session.execute(table.select()).tuples()) for table in
                (PI.__table__, PIItem.__table__, ProductBatch.__table__, OrderTask.__table__, TaskActivity.__table__)}

    def bank(self, code):
        bank = BankAccount(code=code, name=code, beneficiary_name=code, bank_name=code,
                           account_number=code, currency="USD")
        db.session.add(bank); db.session.commit(); return bank

    def test_get_and_detail_offer_editor_only_for_new(self):
        pi = self.order()
        before = self.snapshot()
        self.assertEqual(self.client().get(f'/v2/orders/{pi.id}/edit').status_code, 200)
        self.assertIn('Edit Order', self.client().get(f'/v2/orders/{pi.id}').get_data(as_text=True))
        self.assertEqual(before, self.snapshot())
        for status in ('PRE_SHIPMENT', 'SHIPPED', 'ARRIVED', 'COMPLETED'):
            pi.status = status; db.session.commit()
            before = self.snapshot()
            self.assertEqual(self.client().get(f'/v2/orders/{pi.id}/edit').status_code, 409)
            self.assertEqual(self.post(pi, note='Not saved').status_code, 409)
            self.assertNotIn('>Edit Order</a>', self.client().get(f'/v2/orders/{pi.id}').get_data(as_text=True))
            self.assertEqual(before, self.snapshot())

    def test_party_change_refreshes_all_snapshots(self):
        pi = self.order()
        buyer = Customer(code='B2', name='New Buyer', address='Buyer address', tax_code='TAX',
                         country='CN', contact_person='Buyer contact', phone='123', email='buyer@example.test')
        seller = Exporter(code='S2', name='New Seller', address='Seller address', tax_code='STAX',
                          country='CN', contact_person='Seller contact', phone='456', email='seller@example.test')
        db.session.add_all([buyer, seller]); db.session.commit()
        response = self.post(pi, customer_id=str(buyer.id), exporter_id=str(seller.id))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        for prefix, master in (('customer', buyer), ('exporter', seller)):
            for name in ('name', 'address', 'tax_code', 'country', 'phone', 'email'):
                self.assertEqual(getattr(pi, f'{prefix}_{name}_snapshot'), getattr(master, name))
            self.assertEqual(getattr(pi, f'{prefix}_contact_snapshot'), master.contact_person)

    def test_bank_change_and_clear_remove_all_stale_snapshots(self):
        pi = self.order(); first = self.bank('BANK1'); second = self.bank('BANK2')
        for bank in (first, second):
            self.assertEqual(self.post(pi, bank_account_id=str(bank.id)).status_code, 302)
            self.assertEqual(pi.bank_name_snapshot, bank.name)
        self.assertEqual(self.post(pi, bank_account_id='').status_code, 302)
        self.assertIsNone(pi.bank_account_id)
        for column in PI.__table__.columns:
            if column.name.startswith('bank_') and column.name.endswith('_snapshot'):
                self.assertIsNone(getattr(pi, column.name))

    def test_core_and_local_snapshot_edit(self):
        pi = self.order(); ident = pi.items[0].id
        response = self.post(pi, pi_no='EDITED', pi_date='2026-09-14', payment_terms='Net 60',
            trade_term_0='FOB', customer_address_snapshot='Order-specific address',
            product_model_snapshot_0='Custom Model', quantity_unit_0='BAGS',
            notify_party_same_as_consignee='false', notify_party_name_snapshot='Notify',
            planned_shipment_date='2026-10-01', loading_port='NINGBO', destination_port='LAX')
        self.assertEqual(response.status_code, 302)
        self.assertEqual((pi.pi_no, pi.pi_date, pi.payment_terms), ('EDITED', date(2026,9,14), 'Net 60'))
        self.assertEqual((pi.items[0].id, pi.items[0].trade_term, pi.items[0].product_model_snapshot), (ident, 'FOB', 'Custom Model'))
        self.assertEqual(pi.customer_address_snapshot, 'Order-specific address')
        self.assertEqual(pi.notify_party_name_snapshot, 'Notify')
        self.assertEqual(self.product.model, 'R504')

    def test_payment_drivers_require_explicit_choice_and_rollback_all(self):
        pi = self.order()
        for changes in ({'quantity_0':'20'}, {'unit_price_0':'110'}, {'currency':'CNY'}, {'advance_payment_percent':'30'},
                        {'product_1':str(self.product.id),'quantity_1':'1','unit_price_1':'2','quantity_unit_1':'MT'}):
            before = self.snapshot()
            response = self.post(pi, pi_no='NOT SAVED', customer_name_snapshot='NOT SAVED', **changes)
            self.assertEqual(response.status_code, 400)
            self.assertIn('Payment-plan inputs changed', response.get_data(as_text=True))
            self.assertEqual(before, self.snapshot())

    def test_recalculate_updates_non_null_plan_with_project_rounding(self):
        pi = self.order()
        response = self.post(pi, quantity_0='3', unit_price_0='1.115', advance_payment_percent='50', payment_plan_choice='recalculate')
        self.assertEqual(response.status_code, 302)
        self.assertEqual((pi.contract_total, pi.advance_payment_amount, pi.balance_payment_amount),
                         (Decimal('3.34'), Decimal('1.67'), Decimal('1.67')))

    def test_keep_edit_plan_validates_new_total(self):
        pi = self.order()
        self.assertEqual(self.post(pi, quantity_0='20', payment_plan_choice='keep',
                                   advance_payment_amount='300', balance_payment_amount='1700').status_code, 302)
        self.assertEqual((pi.advance_payment_amount, pi.balance_payment_amount), (Decimal('300'), Decimal('1700')))
        before = self.snapshot()
        response = self.post(pi, quantity_0='30', payment_plan_choice='keep', pi_no='NO')
        self.assertEqual(response.status_code, 400)
        self.assertIn('must equal', response.get_data(as_text=True))
        self.assertEqual(before, self.snapshot())

    def test_no_driver_change_preserves_manual_plan_and_nulls(self):
        pi = self.order()
        for advance, balance in ((Decimal('333'), Decimal('667')), (None, None)):
            pi.advance_payment_amount, pi.balance_payment_amount = advance, balance
            db.session.commit()
            self.assertEqual(self.post(pi, note='Only notes').status_code, 302)
            self.assertEqual((pi.advance_payment_amount, pi.balance_payment_amount), (advance,balance))

    def test_currency_change_requires_choice_even_if_total_matches(self):
        pi = self.order()
        self.assertEqual(self.post(pi, currency='CNY').status_code,400)
        self.assertEqual(self.post(pi, currency='CNY',payment_plan_choice='keep').status_code,302)
        self.assertEqual(pi.currency,'CNY')

    def test_changed_quantities_and_prices_require_choice_even_at_same_total(self):
        pi = self.order()
        self.assertEqual(self.post(pi, quantity_0='5',unit_price_0='200').status_code,400)

    def test_product_change_refreshes_snapshots_and_preserves_item_id(self):
        pi = self.order(); ident = pi.items[0].id
        product = Product(code='P2',model='MODEL2', category='CAT2', brand='BRAND2',packaging='DRUM',hs_code='123')
        db.session.add(product);db.session.commit()
        self.assertEqual(self.post(pi,product_0=str(product.id)).status_code,302)
        self.assertEqual((pi.items[0].id,pi.items[0].product_model_snapshot,pi.items[0].product_hs_code_snapshot), (ident,'MODEL2','123'))

    def test_add_and_remove_preserves_surviving_item_identity(self):
        pi = self.order(); ident=pi.items[0].id
        self.assertEqual(self.post(pi,product_1=str(self.product.id), quantity_1='2',unit_price_1='5',quantity_unit_1='BAGS',payment_plan_choice='recalculate').status_code,302)
        db.session.expire(pi,['items'])
        self.assertEqual(len(pi.items),2)
        data=edit_form_data(pi)
        data={key:value for key,value in data.items() if not key.endswith('_1')}
        data['payment_plan_choice']='recalculate'
        self.assertEqual(self.client().post(f'/v2/orders/{pi.id}/edit',data=data).status_code,302)
        db.session.expire(pi,['items'])
        self.assertEqual([item.id for item in pi.items],[ident])
        self.assertEqual(pi.contract_total,Decimal('1000'))

    def test_batch_dependency_blocks_removal_atomically(self):
        pi=self.order(); db.session.add(ProductBatch(pi_item_id=pi.items[0].id,batch_number='PROTECTED'));db.session.commit()
        data=edit_form_data(pi);data['item_id_0']='';data['payment_plan_choice']='recalculate'
        before=self.snapshot()
        response=self.client().post(f'/v2/orders/{pi.id}/edit',data=data)
        self.assertEqual(response.status_code,400)
        self.assertIn('existing batches',response.get_data(as_text=True))
        self.assertEqual(before,self.snapshot())

    def test_foreign_duplicate_item_ids_and_invalid_inputs_rejected(self):
        pi=self.order(); other=self.order('OTHER')
        for changes in ({'item_id_0':str(other.items[0].id)}, {'product_0':'99999'}, {'quantity_0':'0'},
                        {'unit_price_0':'NaN'},{'quantity_unit_0':''},{'customer_id':'99999'}, {'pi_no':'OTHER'},
                        {'pi_date':'invalid'}, {'status':'SHIPPED'}, {'etd':'2026-10-01'},
                        {'advance_payment_amount':'999'}, {'advance_payment_percent':'101'}):
            before=self.snapshot()
            self.assertEqual(self.post(pi,**changes).status_code,400,changes)
            self.assertEqual(before,self.snapshot())

    def test_stale_form_rejected(self):
        pi=self.order(); old=edit_form_data(pi)
        self.assertEqual(self.post(pi,note='First save').status_code,302)
        before=self.snapshot()
        self.assertEqual(self.client().post(f'/v2/orders/{pi.id}/edit',data=old).status_code,400)
        self.assertEqual(before,self.snapshot())

    def test_duplicate_item_and_empty_order_rejected(self):
        pi=self.order();data=edit_form_data(pi)
        duplicate={key[:-1]+'1':value for key,value in data.items() if key.endswith('_0')}
        before=self.snapshot()
        self.assertEqual(self.post(pi,**duplicate).status_code,400)
        empty={key:value for key,value in data.items() if not key.endswith('_0')}
        self.assertEqual(self.client().post(f'/v2/orders/{pi.id}/edit',data=empty).status_code,400)
        self.assertEqual(before,self.snapshot())

    def test_task_and_receipt_fields_cannot_bypass_operational_routes(self):
        pi=self.order()
        for field in ('advance_received_amount','advance_received_at','balance_received_amount',
                      'container_loading_date','eta','trade_group_id','include_in_business_stats'):
            before=self.snapshot()
            self.assertEqual(self.post(pi,**{field:'1'}).status_code,400)
            self.assertEqual(before,self.snapshot())

    def test_recalculate_half_up_and_keep_requires_cent_precision(self):
        pi=self.order()
        self.assertEqual(self.post(pi,quantity_0='1',unit_price_0='1.01',advance_payment_percent='50',
                                   payment_plan_choice='recalculate').status_code,302)
        self.assertEqual((pi.advance_payment_amount,pi.balance_payment_amount),(Decimal('.51'),Decimal('.50')))
        before=self.snapshot()
        self.assertEqual(self.post(pi,payment_plan_choice='keep',advance_payment_amount='.505',balance_payment_amount='.505').status_code,400)
        self.assertEqual(before,self.snapshot())

    def test_post_claim_status_refresh_rejects_without_business_writes(self):
        from sqlalchemy import update
        pi=self.order();before=self.snapshot();refresh=db.session.refresh
        def changed(row, **kwargs):
            db.session.execute(update(PI).where(PI.id==row.id).values(status='PRE_SHIPMENT')
                               .execution_options(synchronize_session=False))
            refresh(row,**kwargs)
        with patch.object(db.session,'refresh',side_effect=changed):
            self.assertEqual(self.post(pi,note='NO').status_code,400)
        self.assertEqual(before,self.snapshot())

    def test_success_has_one_commit(self):
        from sqlalchemy import event
        pi=self.order();session=db.session();commits=[]
        def committed(session):
            commits.append(True)
        event.listen(session,'after_commit',committed)
        try:
            self.assertEqual(self.post(pi,quantity_0='20',payment_plan_choice='keep',
                                       advance_payment_amount='300',balance_payment_amount='1700').status_code,302)
        finally:
            event.remove(session,'after_commit',committed)
        self.assertEqual(commits,[True])

    def test_reconcile_failure_rolls_back_core_items_and_history(self):
        pi=self.order();before=self.snapshot()
        with patch('v2.new_order_edit.reconcile_order_tasks_for_pi',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.post(pi,quantity_0='20',payment_plan_choice='recalculate',note='NO')
        self.assertEqual(before,self.snapshot())

    def test_linked_commercial_independence_and_shipment_guards(self):
        customer=self.order('CUSTOMER');export=self.order('EXPORT');bank=self.bank('EXP-BANK')
        group=TradeGroup(group_no='LINK');db.session.add(group)
        customer.trade_group=export.trade_group=group
        customer.trade_role='CUSTOMER_ORDER';export.trade_role='EXPORT_ORDER';export.bank_account_id=bank.id
        db.session.commit()
        for subject,peer in ((customer,export),(export,customer)):
            peer_before={column.name:getattr(peer,column.name) for column in PI.__table__.columns}
            peer_price=peer.items[0].unit_price
            changes = ({f"unit_price_{subject.items[0].id}": "200"} if subject.trade_role == "EXPORT_ORDER"
                       else {"unit_price_0": "200", "payment_plan_choice": "recalculate"})
            self.assertEqual(self.post(subject, **changes).status_code, 302)
            self.assertEqual({column.name:getattr(peer,column.name) for column in PI.__table__.columns},peer_before)
            self.assertEqual(peer.items[0].unit_price,peer_price)
        for changes in ({'loading_port':'NO'}, {'planned_shipment_date':'2099-01-01'},
                        {'actual_departure_date':'2026-09-14'}, {'bank_account_id':''}, {'trade_role':'CUSTOMER_ORDER'}):
            before=self.snapshot()
            self.assertEqual(self.post(export,**changes).status_code,400)
            self.assertEqual(before,self.snapshot())

    def linked_pair(self):
        from v2.linked_trade_creation import create_linked_export_order
        customer = self.order("CUSTOMER")
        bank = self.bank("EXPORT-BANK")
        export = create_linked_export_order(customer.id, {
            "pi_no": "EXPORT", "customer_id": str(customer.customer_id),
            "exporter_id": str(customer.exporter_id), "bank_account_id": str(bank.id),
            "payment_terms": "OA90", "currency": "USD",
            f"unit_price_{customer.items[0].id}": "80"})
        return customer, export, bank

    def test_customer_choice_and_export_price_currency_without_payment_plan(self):
        customer, export, bank = self.linked_pair()
        before = self.snapshot()
        self.assertEqual(self.post(customer, unit_price_0="110").status_code, 400)
        self.assertEqual(before, self.snapshot())
        customer_before = {c.name: getattr(customer, c.name) for c in PI.__table__.columns}
        export.advance_payment_amount = Decimal("3")
        export.balance_payment_amount = Decimal("7")
        db.session.commit()
        self.assertEqual(self.post(export, **{f"unit_price_{export.items[0].id}": "95",
                                             "currency": "EUR"}).status_code, 302)
        self.assertEqual(export.contract_total, Decimal("950"))
        self.assertEqual(export.advance_payment_amount, Decimal("3"))
        self.assertEqual(export.balance_payment_amount, Decimal("7"))
        self.assertEqual(customer_before, {c.name: getattr(customer, c.name) for c in PI.__table__.columns})

    def test_export_creation_scope_in_form_and_server(self):
        import re
        customer, export, bank = self.linked_pair()
        page = self.client().get(f"/v2/orders/{export.id}/edit").get_data(as_text=True)
        fields = set(re.findall(r'name="([^"]+)"', page))
        self.assertTrue((set(edit_form_data(export)) | {"csrf_token"}) <= fields)
        for field in ("payment_plan_choice", "advance_payment_percent", "advance_payment_amount",
                      "balance_payment_amount", "quantity_0", "product_0", "pi_date", "note",
                      "planned_shipment_date", "loading_port", "commission_rate", "shipping_mark",
                      "customer_name_snapshot", "notify_party_same_as_consignee", "order_type"):
            self.assertNotIn(field, fields)
            before = self.snapshot()
            self.assertEqual(self.post(export, **{field: "unexpected"}).status_code, 400, field)
            self.assertEqual(before, self.snapshot())
        original = export.items[0]
        item_before = {c.name: getattr(original, c.name) for c in PIItem.__table__.columns}
        self.assertEqual(self.post(export, **{f"trade_term_{original.id}": "CIF",
                                             "settlement_documents_required": "true"}).status_code, 302)
        for field, value in item_before.items():
            if field not in ("trade_term", "updated_at"):
                self.assertEqual(getattr(original, field), value)
        self.assertTrue(export.settlement_documents_required)

    def test_export_bank_required_active_and_snapshot_selection(self):
        customer, export, bank = self.linked_pair()
        replacement = self.bank("REPLACEMENT")
        for value in ("", "999999"):
            before = self.snapshot()
            self.assertEqual(self.post(export, bank_account_id=value).status_code, 400)
            self.assertEqual(before, self.snapshot())
        bank.active = False
        db.session.commit()
        before = self.snapshot()
        self.assertEqual(self.post(export).status_code, 400)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.post(export, bank_account_id=str(replacement.id)).status_code, 302)
        self.assertEqual(export.bank_account_id, replacement.id)
        self.assertEqual(export.bank_name_snapshot, replacement.bank_name)
        self.assertIsNone(customer.bank_account_id)

    def test_commission_configuration_and_derived_amount(self):
        pi=self.order()
        self.assertEqual(self.post(pi,order_type='COMMISSION',commission_rate='5',commission_amount_mode='DERIVED').status_code,302)
        self.assertEqual(pi.commission_amount,Decimal('50'))
        before=self.snapshot()
        self.assertEqual(self.post(pi,commission_amount_mode='EXPLICIT_OVERRIDE',commission_override_reason='').status_code,400)
        self.assertEqual(before,self.snapshot())

    def test_document_regeneration_uses_saved_facts_and_does_not_lock_new(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        pi=self.order(); pi.items[0].trade_term='FOB';pi.loading_port='SHA';pi.destination_port='LAX';db.session.commit()
        self.app.jinja_loader  # Resolve the real template directory before isolating generated output.
        # Exercise the actual generation routes with a disposable output root.
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/'app';root.mkdir()
            class HTMLCapture:
                def __init__(self, *, string):
                    self.string = string
                def write_pdf(self, target):
                    Path(target).write_bytes(self.string.encode())
            with patch.object(self.app,'root_path',str(root)), patch('weasyprint.HTML',HTMLCapture):
                for kind in ('pi','contract'):
                    response=self.client().get(f'/v2/orders/{pi.id}/documents/{kind}')
                    self.assertEqual(response.status_code,200);response.close()
                self.assertEqual(self.post(pi,pi_no='REGENERATED',product_model_snapshot_0='EDITED-MODEL',unit_price_0='200',payment_plan_choice='recalculate').status_code,302)
                for kind in ('pi','contract'):
                    response=self.client().get(f'/v2/orders/{pi.id}/documents/{kind}')
                    self.assertEqual(response.status_code,200)
                    self.assertIn('EDITED-MODEL',response.get_data(as_text=True))
                    self.assertIn('REGENERATED',response.get_data(as_text=True));response.close()

    def test_csrf_required_and_transaction_choice_not_persisted(self):
        pi=self.order();self.app.config['WTF_CSRF_ENABLED']=True
        self.assertEqual(self.post(pi,note='NO').status_code,400)
        self.assertNotIn('payment_plan_choice',PI.__table__.columns)
