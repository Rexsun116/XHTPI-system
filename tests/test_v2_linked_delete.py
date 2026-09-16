"""Atomic linked draft deletion on disposable migration databases."""
from unittest import TestCase
from unittest.mock import patch
from pathlib import Path
from sqlalchemy import update, event
from tests import test_v2_order_delete as fixture
from v2.models import PI, TradeGroup, db
from v2.order_deletion import delete_linked_trade, linked_delete_pair, OrderDeletionNotAllowed, _delete_owned_order


class LinkedDeleteTest(TestCase):
    setUp = fixture.V2OrderDeleteTest.setUp
    tearDown = fixture.V2OrderDeleteTest.tearDown
    client = fixture.V2OrderDeleteTest.client
    make_order = fixture.V2OrderDeleteTest.make_order

    def pair(self):
        # Export ID deliberately comes first to exercise role-independent ordering.
        export = self.make_order('EXPORT', with_all_children=True)
        customer = self.make_order('CUSTOMER', with_all_children=True)
        group = TradeGroup(group_no='PAIR')
        db.session.add(group)
        customer.trade_group = export.trade_group = group
        customer.trade_role, export.trade_role = 'CUSTOMER_ORDER', 'EXPORT_ORDER'
        db.session.commit()
        return customer, export, group

    def snapshot(self):
        return {t.name: list(db.session.execute(t.select()).tuples()) for t in db.metadata.sorted_tables}

    def post(self, pi, **changes):
        data = dict(customer_confirmation='CUSTOMER', export_confirmation='EXPORT'); data.update(changes)
        return self.client().post(f'/v2/orders/{pi.id}/delete-linked-trade', data=data)

    def test_both_entry_points_ui_and_single_delete_guards(self):
        customer, export, group = self.pair()
        before = self.snapshot()
        for pi in (customer, export):
            page = self.client().get(f'/v2/orders/{pi.id}').get_data(as_text=True)
            self.assertIn('Delete Linked Trade', page)
            self.assertNotIn('>Delete Order<', page)
            confirmation = self.client().get(f'/v2/orders/{pi.id}/delete-linked-trade')
            self.assertEqual(confirmation.status_code, 200)
            self.assertIn('CUSTOMER', confirmation.get_data(as_text=True))
            self.assertIn('EXPORT', confirmation.get_data(as_text=True))
            self.assertEqual(self.client().post(f'/v2/orders/{pi.id}/delete', data={'confirmation':pi.pi_no}).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_success_removes_owned_rows_only_and_preserves_file(self):
        customer, export, group = self.pair()
        unrelated = self.make_order('UNRELATED', with_all_children=True)
        before = self.snapshot(); ids={customer.id,export.id}; gid=group.id; cid=customer.id
        artifact=Path(self.tmp.name)/'generated.pdf';artifact.write_bytes(b'keep')
        commits=[]
        def committed(session): commits.append(True)
        event.listen(db.session(),'after_commit',committed)
        try: self.assertEqual(self.post(export).status_code,302)
        finally: event.remove(db.session(),'after_commit',committed)
        self.assertEqual(commits,[True])
        self.assertIsNone(db.session.get(TradeGroup,gid))
        self.assertEqual(PI.query.count(),1)
        self.assertEqual(PI.query.one().id,unrelated.id)
        for t in ('customer','exporter','factory','product','freight_forwarder','bank_account','freight_quote','user'):
            self.assertEqual(before[t],self.snapshot()[t])
        for t in ('pi_item','product_batch','order_task','task_activity','order_freight_agreement','freight_settlement','order_correction_session'):
            self.assertEqual(len(self.snapshot()[t]),1,t)
        self.assertEqual(artifact.read_bytes(),b'keep')
        self.assertEqual(db.session.execute(db.text('pragma foreign_key_check')).all(),[])
        self.assertEqual(self.client().post(f'/v2/orders/{cid}/delete-linked-trade').status_code,404)

    def test_invalid_confirmation_and_status_reject_without_mutation(self):
        customer, export, group = self.pair()
        for field in ('customer_confirmation','export_confirmation'):
            before=self.snapshot();self.assertEqual(self.post(customer,**{field:'WRONG'}).status_code,400)
            self.assertEqual(before,self.snapshot())
        for cs,es in (('PRE_SHIPMENT','NEW'),('NEW','PRE_SHIPMENT'),('SHIPPED','COMPLETED')):
            customer.status,export.status=cs,es;db.session.commit();before=self.snapshot()
            self.assertEqual(self.post(customer).status_code,409)
            self.assertNotIn('Delete Linked Trade',self.client().get(f'/v2/orders/{customer.id}').get_data(as_text=True))
            self.assertEqual(before,self.snapshot())

    def test_missing_peer_rejected(self):
        customer, export, group=self.pair()
        export.trade_group=None;export.trade_role=None;db.session.commit();before=self.snapshot()
        self.assertEqual(self.post(customer).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_authentication_and_csrf(self):
        customer,export,group=self.pair();url=f'/v2/orders/{customer.id}/delete-linked-trade'
        self.assertEqual(self.app.test_client().get(url).status_code,302)
        self.app.config['WTF_CSRF_ENABLED']=True
        self.assertEqual(self.client().post(url,data={'customer_confirmation':'CUSTOMER','export_confirmation':'EXPORT'}).status_code,400)

    def test_mid_delete_failure_rolls_back_both(self):
        customer,export,group=self.pair();before=self.snapshot()
        def fail(ident):
            _delete_owned_order(ident)
            raise RuntimeError('injected')
        with patch('v2.order_deletion._delete_owned_order',side_effect=fail):
            with self.assertRaises(RuntimeError):delete_linked_trade(customer,'CUSTOMER','EXPORT')
        self.assertEqual(before,self.snapshot())

    def test_claim_order_and_post_claim_revalidation(self):
        customer,export,group=self.pair();before=self.snapshot();claims=[]
        original=db.session.execute
        def execute(statement,*args,**kwargs):
            result=original(statement,*args,**kwargs)
            if getattr(statement,'is_update',False) and statement.table.name=='pi':
                claims.append(statement.compile().params['id_1'])
                if len(claims)==2:
                    original(update(PI).where(PI.id==customer.id).values(pi_no='RENAMED').execution_options(synchronize_session=False))
            return result
        with patch.object(db.session,'execute',side_effect=execute):
            from v2.order_deletion import OrderDeletionConfirmationError
            with self.assertRaises(OrderDeletionConfirmationError):delete_linked_trade(customer,'CUSTOMER','EXPORT')
        self.assertEqual(claims,sorted([customer.id,export.id]))
        self.assertEqual(before,self.snapshot())

    def test_customer_entry_deletes_pair(self):
        customer,export,group=self.pair()
        self.assertEqual(self.post(customer).status_code,302)
        self.assertEqual(PI.query.count(),0)
        self.assertEqual(TradeGroup.query.count(),0)

    def test_malformed_roles_and_extra_members_fail_closed(self):
        from types import SimpleNamespace
        customer,export,group=self.pair();before=self.snapshot()
        def row(ident,role):return SimpleNamespace(id=ident,trade_role=role,status='NEW')
        for rows in ([row(customer.id,'CUSTOMER_ORDER'),row(export.id,'CUSTOMER_ORDER')],
                     [row(customer.id,'CUSTOMER_ORDER'),row(export.id,'INVALID')],
                     [row(customer.id,'CUSTOMER_ORDER'),row(export.id,'EXPORT_ORDER'),row(999,'EXPORT_ORDER')]):
            with patch.object(db.session,'execute') as execute:
                execute.return_value.all.return_value=rows
                with self.assertRaises(OrderDeletionNotAllowed):linked_delete_pair(customer)
        self.assertEqual(before,self.snapshot())
