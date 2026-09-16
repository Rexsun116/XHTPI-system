"""BL completion + 25 calendar days, gated by existing issued-invoice facts."""
from datetime import datetime
from unittest import TestCase
from tests import test_v2_linked_shipment as fixture
from v2.models import OrderTask, TaskActivity, FreightSettlement, db
from v2.services import reconcile_order_tasks_for_pi
from v2.task_service import mark_done
from v2.selector import projected


class ForwarderPaymentTest(TestCase):
    setUp=fixture.LinkedShipmentTest.setUp
    tearDown=fixture.LinkedShipmentTest.tearDown
    pi=fixture.LinkedShipmentTest.pi
    pair=fixture.LinkedShipmentTest.pair

    def order(self):
        pi=self.pi('PAY');pi.status='SHIPPED'
        pi.obd_electronic_required=pi.original_bl_required=True
        self.settlement=FreightSettlement(pi_id=pi.id,usd_bill_required=True,cny_bill_required=True)
        db.session.add(self.settlement);db.session.commit();return pi

    def done(self,pi,code,when):
        task = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == pi.id, OrderTask.task_code == code))
        if task is None:
            task = OrderTask(pi_id=pi.id, task_code=code, title=code, source='AUTO',
                             health='NORMAL', completion_mode='MANUAL',
                             dedupe_key=f'v2:order:{pi.id}:{code.lower()}')
            db.session.add(task)
        task.status = 'DONE'
        task.completed_at = when
        db.session.flush()
        db.session.add(TaskActivity(task_id=task.id,event_type='COMPLETED',to_status='DONE',actor_type='USER',
                                    actor_id=self.user.id,created_at=when));db.session.commit();return task

    def reconcile(self,pi,when):
        reconcile_order_tasks_for_pi(pi,now=when);db.session.commit()
        return db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id==pi.id,OrderTask.task_code=='FREIGHT_FORWARDER_PAYMENT'))

    def test_no_anchor_no_task_and_no_fallback(self):
        pi=self.order();self.settlement.usd_invoice_issued=True
        self.assertIsNone(self.reconcile(pi,datetime(2026,10,1)))
        task=self.done(pi,'DOCUMENT_OBD_BL',datetime(2026,9,1))
        db.session.query(TaskActivity).filter_by(task_id=task.id).delete();task.completed_at=None;db.session.commit()
        self.assertIsNone(self.reconcile(pi,datetime(2026,10,1)))

    def test_each_anchor_and_invoice_combination(self):
        for bl in ('DOCUMENT_OBD_BL','DOCUMENT_ORIGINAL_BL'):
            for issued in (('USD',),('CNY',),('USD','CNY')):
                pi=self.pi(f'{bl}-{len(issued)}-{issued[0]}');pi.status='SHIPPED'
                pi.obd_electronic_required=pi.original_bl_required=True
                settlement=FreightSettlement(pi_id=pi.id,usd_bill_required=True,cny_bill_required=True)
                for currency in issued:setattr(settlement,currency.lower()+'_invoice_issued',True)
                db.session.add(settlement);self.done(pi,bl,datetime(2026,9,1,8))
                task=self.reconcile(pi,datetime(2026,9,25,8));self.assertEqual(task.status,'UPCOMING')
                task=self.reconcile(pi,datetime(2026,9,26,0));self.assertEqual(task.status,'ACTION')
                self.assertEqual(task.context_payload['payment_due_date'],'2026-09-26')
                ident=task.id;count=len(task.activities)
                self.reconcile(pi,datetime(2026,9,26,0));self.assertEqual(len(task.activities),count)
                mark_done(task,self.user.id);db.session.commit()
                self.assertEqual(self.reconcile(pi,datetime(2026,10,1)).status,'DONE')
                self.assertEqual(task.id,ident)

    def test_earliest_activity_and_shanghai_boundary(self):
        pi=self.order();self.settlement.cny_invoice_issued=True
        task=self.done(pi,'DOCUMENT_OBD_BL',datetime(2026,8,31,16,30))
        task.completed_at=datetime(2026,9,10);db.session.commit()
        self.done(pi,'DOCUMENT_ORIGINAL_BL',datetime(2026,9,3))
        payment=self.reconcile(pi,datetime(2026,9,25,15,59));self.assertEqual(payment.status,'UPCOMING')
        self.assertEqual(payment.context_payload['bl_done_date'],'2026-09-01')
        self.assertEqual(projected(payment,datetime(2026,9,25,16))[0],'ACTION')
        self.assertEqual(self.reconcile(pi,datetime(2026,9,25,16)).status,'ACTION')

    def test_late_invoice_and_invoice_revocation(self):
        pi=self.order();self.done(pi,'DOCUMENT_OBD_BL',datetime(2026,9,1))
        self.assertIsNone(self.reconcile(pi,datetime(2026,9,26)))
        self.settlement.usd_invoice_issued=True
        task=self.reconcile(pi,datetime(2026,9,30));self.assertEqual(task.status,'ACTION')
        self.settlement.usd_invoice_issued=False
        self.assertEqual(self.reconcile(pi,datetime(2026,10,1)).status,'CANCELLED')
        self.assertEqual(projected(task,datetime(2026,10,1))[0],'CANCELLED')

    def test_linked_owner_only_and_terminal_cleanup(self):
        customer,export=self.pair();customer.status='SHIPPED';export.status='COMPLETED'
        customer.obd_electronic_required=True
        settlement=FreightSettlement(pi_id=customer.id,cny_bill_required=True,cny_invoice_issued=True)
        db.session.add(settlement);self.done(customer,'DOCUMENT_OBD_BL',datetime(2026,9,1))
        self.assertEqual(self.reconcile(customer,datetime(2026,9,26)).status,'ACTION')
        self.assertIsNone(self.reconcile(export,datetime(2026,9,26)))
        self.assertEqual(export.status,'COMPLETED')
        customer.status='COMPLETED'
        self.assertEqual(self.reconcile(customer,datetime(2026,9,27)).status,'CANCELLED')
