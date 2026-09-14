"""Read-only board presentation and event-time formatting."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest import TestCase
import re

from tests import test_v2_new_order_edit as fixture
from v2.models import PI, PIItem, db
from v2.presenter import format_task_datetime


class EventTimeTest(TestCase):
    def test_naive_utc_and_midnight(self):
        value=datetime(2026,9,14,7,5)
        self.assertEqual(format_task_datetime(value),'2026-09-14 15:05')
        self.assertIsNone(value.tzinfo)
        self.assertEqual(value,datetime(2026,9,14,7,5))
        self.assertEqual(format_task_datetime(datetime(2026,9,14,18,30)),'2026-09-15 02:30')

    def test_aware_utc_and_already_local_no_double_conversion(self):
        self.assertEqual(format_task_datetime(datetime(2026,9,14,7,5,tzinfo=timezone.utc)),'2026-09-14 15:05')
        self.assertEqual(format_task_datetime(datetime(2026,9,14,15,5,tzinfo=ZoneInfo('Asia/Shanghai'))),'2026-09-14 15:05')
        self.assertEqual(format_task_datetime(None),'—')


class BoardPolishTest(TestCase):
    setUp=fixture.NewOrderEditTest.setUp
    tearDown=fixture.NewOrderEditTest.tearDown
    client=fixture.NewOrderEditTest.client
    form=fixture.NewOrderEditTest.form
    order=fixture.NewOrderEditTest.order
    snapshot=fixture.NewOrderEditTest.snapshot

    def board(self, query=''):
        response=self.client().get('/v2/'+query)
        self.assertEqual(response.status_code,200)
        return response.get_data(as_text=True)

    def row(self,page,pi):
        return re.search(r'<tr id="order-'+str(pi.id)+r'".*?</tr>',page,re.S).group()

    def test_all_item_models_and_distinct_units_without_live_master(self):
        pi=self.order()
        pi.items.append(PIItem(product_id=self.product.id,quantity=2,quantity_unit='BAGS',unit_price=3,line_total=6,
                               product_model_snapshot='MODEL-TWO',product_category_snapshot='CATEGORY-TWO'))
        self.product.model='LIVE-CHANGED';db.session.commit()
        row=self.row(self.board(),pi)
        self.assertIn('R504',row);self.assertIn('MODEL-TWO',row)
        self.assertIn('10 MT',row);self.assertIn('2 BAGS',row)
        self.assertNotIn('TITANIUM DIOXIDE',row);self.assertNotIn('LIVE-CHANGED',row)

    def test_blank_model_category_then_dash(self):
        pi=self.order();pi.items[0].product_model_snapshot=None;db.session.commit()
        self.assertIn('TITANIUM DIOXIDE',self.row(self.board(),pi))
        pi.items[0].product_category_snapshot=None;db.session.commit()
        self.assertIn('v35-cell-primary">—',self.row(self.board(),pi))

    def test_departure_annotation_only_for_shipped_with_actual_date(self):
        from datetime import date
        pi=self.order()
        for status,departed,expected in [('SHIPPED',date(2026,9,14),True),('SHIPPED',None,False),
                                       ('PRE_SHIPMENT',date(2026,9,14),False),('COMPLETED',date(2026,9,14),False)]:
            pi.status=status;pi.actual_departure_date=departed;db.session.commit()
            row=self.row(self.board(),pi)
            self.assertEqual('Departed: 2026-09-14' in row,expected)

    def test_sort_modes_ties_invalid_default_and_read_only(self):
        from datetime import date
        first=self.order('FIRST');second=self.order('SECOND');third=self.order('THIRD')
        first.updated_at=datetime(2026,9,13);second.updated_at=third.updated_at=datetime(2026,9,14)
        first.pi_date=second.pi_date=date(2026,9,15);third.pi_date=date(2026,9,10)
        db.session.commit();before=self.snapshot()
        for query,expected in [('',[third.id,second.id,first.id]),('?sort=bad',[third.id,second.id,first.id]),
                               ('?sort=updated',[third.id,second.id,first.id]),
                               ('?sort=pi_date_desc',[second.id,first.id,third.id]),
                               ('?sort=pi_date_asc',[third.id,first.id,second.id])]:
            page=self.board(query)
            self.assertEqual([int(x) for x in re.findall(r'<tr id="order-(\d+)"',page)],expected)
        self.assertEqual(before,self.snapshot())

    def test_sort_selection_preserves_search_and_dashboard_controls(self):
        self.order()
        page=self.board('?sort=pi_date_asc&q=EDIT')
        for text in ('value="pi_date_asc" selected','applyCollapsedState','limits={exception:3,action:5,waiting:4}',
                     "target.searchParams.set('q', search.value)","search.dispatchEvent(new Event('input'))",'aria-expanded'):
            self.assertIn(text,page)

    def test_sort_uses_existing_board_anchor_only_on_interaction(self):
        page = self.board()
        self.assertEqual(page.count('id="control-board"'), 1)
        handler = page.split("addEventListener('change', function()")[1]
        self.assertIn("target.hash = 'control-board';", handler)
        self.assertLess(handler.index("target.hash"), handler.index("window.location.assign"))
        self.assertNotIn("location.hash =", page)
