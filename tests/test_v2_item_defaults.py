"""Creation defaults and free-text trade terms, with disposable databases."""
from unittest import TestCase
from tests import test_v2_create_pi_form as fixture
from v2.models import PI, PIItem, db
from v2.new_order_edit import edit_form_data


class ItemDefaultsTest(TestCase):
    setUp = fixture.V2CreatePIFormTest.setUp
    tearDown = fixture.V2CreatePIFormTest.tearDown
    client = fixture.V2CreatePIFormTest.client
    form = fixture.V2CreatePIFormTest.form

    def test_default_and_explicit_units_and_all_trade_terms(self):
        for term in ('CIF', 'CFR', 'FOB', 'EXW'):
            data = self.form(term)
            data.pop('quantity_unit_0')
            data.update(trade_term_0=term, product_1=str(self.product.id), quantity_1='2', unit_price_1='3')
            self.assertEqual(self.client().post('/v2/orders/new', data=data).status_code, 302)
            pi = db.session.scalar(db.select(PI).where(PI.pi_no == term))
            self.assertEqual([i.quantity_unit for i in pi.items], ['KGS', 'KGS'])
            self.assertEqual(pi.items[0].trade_term, term)
            edit = edit_form_data(pi)
            self.assertEqual(self.client().post(f'/v2/orders/{pi.id}/edit', data=edit).status_code, 302)
            self.assertEqual(pi.items[0].trade_term, term)
        data = self.form('EXPLICIT', quantity_unit_0='MT')
        self.assertEqual(self.client().post('/v2/orders/new', data=data).status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == 'EXPLICIT'))
        self.client().get('/v2/orders/new')
        self.assertEqual(pi.items[0].quantity_unit, 'MT')
        page = self.client().get('/v2/').get_data(as_text=True)
        self.assertIn('KGS', page)

    def test_initial_and_added_rows_share_kgs_default(self):
        page = self.client().get('/v2/orders/new').get_data(as_text=True)
        self.assertIn("values.quantity_unit || 'KGS'", page)
        self.assertNotIn("values.quantity_unit || 'MT'", page)
        self.assertIn('addItem', page)

    def test_orm_new_item_default_does_not_rewrite_existing_unit(self):
        data = self.form('ORM', quantity_unit_0='MT')
        self.client().post('/v2/orders/new', data=data)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == 'ORM'))
        original = pi.items[0]
        item = PIItem(quantity=1, unit_price=1, line_total=1)
        pi.items.append(item); db.session.commit()
        self.assertEqual(item.quantity_unit, 'KGS')
        self.assertEqual(original.quantity_unit, 'MT')

    def test_api_default_and_explicit_units(self):
        response = self.client().post('/orders', json={
            'pi_no': 'API-UNITS', 'pi_date': '2026-09-15', 'planned_shipment_date': '2026-09-30',
            'customer_id': self.customer.id, 'exporter_id': self.exporter.id, 'currency': 'USD',
            'items': [{'product_id': self.product.id, 'quantity': 2, 'unit_price': 3},
                      {'product_id': self.product.id, 'quantity': 4, 'unit_price': 5, 'quantity_unit': 'MT'}]})
        self.assertEqual(response.status_code, 201)
        pi = db.session.get(PI, response.get_json()['id'])
        self.assertEqual([i.quantity_unit for i in pi.items], ['KGS', 'MT'])
