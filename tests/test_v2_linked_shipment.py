"""Atomic departure and shared ETD, using only an isolated temporary database."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import event, update
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import (Customer, Exporter, FreightSettlement, OrderFreightAgreement, OrderTask, PI, PIItem,
                       OrderCorrectionSession, TaskActivity, TradeGroup, User, db)
from v2.linked_shipment import LinkedShipmentError, record_linked_actual_departure, save_linked_etd
from v2.services import reconcile_order_tasks_for_pi
from v2.linked_shipment import _claim_pair
from v2.shipment_ownership import PHYSICAL_FORM_FIELDS, SHARED_PHYSICAL_FIELDS, is_physical_shipment_task


class LinkedShipmentTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'shipment.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="shipment", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="C", name="Customer")
        self.exporter = Exporter(code="E", name="Exporter")
        db.session.add_all((self.user, self.customer, self.exporter)); db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); db.engine.dispose()
        self.ctx.pop(); self.tmp.cleanup()

    def client(self, *, csrf=False):
        self.app.config["WTF_CSRF_ENABLED"] = csrf
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def pi(self, number, *, group=None, role=None):
        row = PI(pi_no=number, pi_date=date(2026, 9, 1), order_type="SALES", status="PRE_SHIPMENT",
                 customer_id=self.customer.id, exporter_id=self.exporter.id,
                 customer_name_snapshot="Customer", exporter_name_snapshot="Exporter", currency="USD",
                 trade_group=group, trade_role=role, payment_terms="Net 30", bank_name_snapshot=number,
                 planned_shipment_date=date(2026, 9, 1), etd=date(2026, 9, 20),
                 advance_payment_amount=Decimal("20"), advance_received_amount=Decimal("0"),
                 balance_payment_amount=Decimal("80"), original_bl_required=True,
                 insurance_original_required=True, original_documents_mail_required=True,
                 telex_release_required=True)
        row.items.append(PIItem(unit_price=Decimal("100"), quantity=Decimal("1"),
                                quantity_unit="MT", line_total=Decimal("100")))
        db.session.add(row); db.session.flush()
        return row

    def pair(self):
        group = TradeGroup(group_no=f"GROUP-{TradeGroup.query.count()}")
        db.session.add(group); db.session.flush()
        # Deliberately misleading prefixes: structured roles are authoritative.
        customer = self.pi(f"XHT-C-{group.id}", group=group, role="CUSTOMER_ORDER")
        export = self.pi(f"WU-E-{group.id}", group=group, role="EXPORT_ORDER")
        export.planned_shipment_date = date(2026, 9, 2)
        db.session.commit()
        return customer, export

    def snapshot(self):
        db.session.flush()
        return {table.name: list(db.session.execute(table.select()).tuples())
                for table in (PI.__table__, PIItem.__table__, OrderTask.__table__, TaskActivity.__table__)}

    def departure(self, row, **extra):
        return self.client().post(f"/v2/orders/{row.id}/enter-shipped", data={
            "actual_departure_date": "2026-09-18", **extra})

    def assert_departed(self, customer, export):
        db.session.expire_all()
        self.assertEqual((customer.status, export.status), ("SHIPPED", "COMPLETED"))
        self.assertEqual(customer.actual_departure_date, date(2026, 9, 18))
        self.assertEqual(customer.actual_departure_date, export.actual_departure_date)

    def test_departure_from_either_role_needs_no_generic_or_post_shipment_gate(self):
        for role in (0, 1):
            with self.subTest(role=role):
                customer, export = self.pair()
                customer.etd = export.etd = None
                db.session.add(FreightSettlement(pi_id=customer.id, usd_bill_required=True,
                                                usd_payment_status="UNPAID"))
                db.session.commit()
                self.assertEqual(self.departure((customer, export)[role]).status_code, 302)
                self.assert_departed(customer, export)

    def test_one_commit_and_no_committed_intermediate_state(self):
        customer, export = self.pair()
        committed = []
        def after_commit(session):
            with db.engine.connect() as connection:
                committed.append(connection.execute(db.select(PI.status).order_by(PI.id)).scalars().all())
        session = db.session()
        event.listen(session, "after_commit", after_commit)
        try:
            record_linked_actual_departure(export, date(2026, 9, 18))
        finally:
            event.remove(session, "after_commit", after_commit)
        self.assertEqual(committed, [["SHIPPED", "COMPLETED"]])

    def test_second_reconcile_failure_rolls_back_both_orders_and_history(self):
        customer, export = self.pair()
        before = self.snapshot()
        calls = []
        def fail_second(row):
            calls.append(row.id)
            reconcile_order_tasks_for_pi(row)
            db.session.flush()
            if len(calls) == 2:
                raise RuntimeError("second reconcile failed")
        with patch("v2.linked_shipment.reconcile_order_tasks_for_pi", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "second reconcile"):
                record_linked_actual_departure(export, date(2026, 9, 18))
        self.assertEqual(calls, [export.id, customer.id])
        self.assertEqual(self.snapshot(), before)

    def test_database_commit_failure_rolls_back_both_sides(self):
        customer, export = self.pair()
        before = self.snapshot()
        with patch.object(db.session, "commit", side_effect=OperationalError("COMMIT", {}, Exception("failure"))):
            self.assertEqual(self.departure(export).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_missing_peer_rejects_departure_and_etd_without_mutation(self):
        for role in ("CUSTOMER_ORDER", "EXPORT_ORDER"):
            group = TradeGroup(group_no=role); db.session.add(group); db.session.flush()
            orphan = self.pi(role, group=group, role=role); db.session.commit()
            before = self.snapshot()
            self.assertEqual(self.departure(orphan).status_code, 409)
            self.assertEqual(self.client().post(f"/v2/orders/{orphan.id}/facts", data={"etd": "2026-09-22"}).status_code, 409)
            self.assertEqual(self.snapshot(), before)

    def test_duplicate_role_constraint_and_defensive_malformed_resolution(self):
        customer, export = self.pair()
        before = self.snapshot()
        with self.assertRaises(IntegrityError):
            self.pi("DUP", group=customer.trade_group, role="CUSTOMER_ORDER")
        db.session.rollback()
        self.assertEqual(self.snapshot(), before)
        with patch.object(db.session, "scalars", return_value=iter([customer, export, export])):
            with self.assertRaises(LinkedShipmentError):
                record_linked_actual_departure(export, date(2026, 9, 18))
        self.assertEqual(self.snapshot(), before)

    def test_missing_trade_group_is_rejected(self):
        customer, export = self.pair()
        before = self.snapshot()
        original_get = db.session.get
        def missing_group(model, ident, **kwargs):
            return None if model is TradeGroup else original_get(model, ident, **kwargs)
        with patch.object(db.session, "get", side_effect=missing_group):
            with self.assertRaises(LinkedShipmentError):
                save_linked_etd(export, date(2026, 9, 22))
        self.assertEqual(self.snapshot(), before)

    def test_invalid_either_state_or_existing_departure_rejects(self):
        customer, export = self.pair()
        for row in (customer, export):
            for status in ("NEW", "SHIPPED", "ARRIVED", "COMPLETED"):
                row.status = status; db.session.commit()
                before = self.snapshot()
                with self.assertRaises(LinkedShipmentError):
                    record_linked_actual_departure(export, date(2026, 9, 18))
                with self.assertRaises(LinkedShipmentError):
                    save_linked_etd(customer, date(2026, 9, 22))
                self.assertEqual(self.snapshot(), before)
                row.status = "PRE_SHIPMENT"; db.session.commit()
        customer.actual_departure_date = date(2026, 9, 17); db.session.commit()
        before = self.snapshot()
        self.assertEqual(self.departure(export).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_stale_session_cannot_repeat_a_committed_handoff(self):
        customer, export = self.pair()
        ids = customer.id, export.id
        # Load both before another writer commits, retaining stale ORM values.
        self.assertEqual((customer.status, export.status), ("PRE_SHIPMENT", "PRE_SHIPMENT"))
        with db.engine.begin() as connection:
            connection.execute(update(PI).where(PI.id == ids[0]).values(status="SHIPPED", actual_departure_date=date(2026, 9, 18)))
            connection.execute(update(PI).where(PI.id == ids[1]).values(status="COMPLETED", actual_departure_date=date(2026, 9, 18)))
        with self.assertRaises(LinkedShipmentError):
            record_linked_actual_departure(export, date(2026, 9, 19))
        self.assert_departed(customer, export)
        self.assertEqual(TaskActivity.query.count(), 0)

    def test_repeat_from_either_role_has_no_duplicate_history(self):
        customer, export = self.pair()
        self.assertEqual(self.departure(export).status_code, 302)
        before = self.snapshot()
        for row in (customer, export):
            self.assertEqual(self.departure(row).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_generic_export_transitions_and_arrival_entry_are_blocked(self):
        customer, export = self.pair()
        for status, target in (("PRE_SHIPMENT", "SHIPPED"), ("SHIPPED", "ARRIVED")):
            export.status = status; db.session.commit()
            before = self.snapshot()
            self.assertEqual(self.client().post(f"/v2/orders/{export.id}/status", data={"status": target}).status_code, 409)
            for method in (self.client().get, self.client().post):
                self.assertEqual(method(f"/v2/orders/{export.id}/enter-arrived").status_code, 409)
            self.assertEqual(self.snapshot(), before)

    def test_ordinary_and_independent_xht_keep_normal_departure_and_local_etd(self):
        for number in ("ORDINARY-CUSTOMER", "XHT-INDEPENDENT"):
            row = self.pi(number)
            row.container_loading_date = date(2026, 9, 17)
            db.session.add(OrderFreightAgreement(pi_id=row.id, freight_forwarder_name_snapshot="FF",
                                                 amount=Decimal("10"), currency="USD"))
            for code in ("SHIPPING_CONTAINER_LOADING", "SHIPPING_FREIGHT_AGREEMENT"):
                db.session.add(OrderTask(pi_id=row.id, task_code=code, title=code, source="AUTO", status="DONE",
                                         health="NORMAL", completion_mode="RULE_DATA",
                                         dedupe_key=f"v2:order:{row.id}:{code.lower()}"))
            db.session.commit()
            self.assertEqual(self.client().post(f"/v2/orders/{row.id}/facts", data={"etd": "2026-09-22"}).status_code, 302)
            self.assertEqual(self.departure(row).status_code, 400)
            self.assertEqual(self.departure(row, shipping_company="Carrier", bill_of_lading_number="BL").status_code, 302)
            self.assertEqual(row.status, "SHIPPED")
            self.assertEqual(row.etd, date(2026, 9, 22))

    def test_customer_etd_edits_sync_and_clearing_is_shared(self):
        customer, export = self.pair()
        plans = customer.planned_shipment_date, export.planned_shipment_date
        for row, value in ((customer, "2026-09-25"), (customer, "2026-09-26"), (customer, "")):
            self.assertEqual(self.client().post(f"/v2/orders/{row.id}/facts", data={"etd": value}).status_code, 302)
            db.session.expire_all()
            self.assertEqual(customer.etd, date.fromisoformat(value) if value else None)
            self.assertEqual(customer.etd, export.etd)
            self.assertEqual((customer.status, export.status), ("PRE_SHIPMENT", "PRE_SHIPMENT"))
            self.assertEqual((customer.planned_shipment_date, export.planned_shipment_date), plans)

    def test_etd_second_reconcile_failure_rolls_back_entire_facts_patch(self):
        customer, export = self.pair()
        before = self.snapshot()
        calls = []
        def fail_second(row):
            calls.append(row.id); reconcile_order_tasks_for_pi(row)
            if len(calls) == 2:
                raise RuntimeError("ETD reconcile failed")
        with patch("v2.linked_shipment.reconcile_order_tasks_for_pi", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "ETD reconcile"):
                self.client().post(f"/v2/orders/{customer.id}/facts", data={"etd": "2026-09-25", "eta": "2026-10-02", "vessel_info": "NEW"})
        self.assertEqual(self.snapshot(), before)

    def test_etd_validates_peer_eta_without_changing_either_date(self):
        customer, export = self.pair()
        customer.eta = date(2026, 9, 21); db.session.commit()
        before = self.snapshot()
        self.assertEqual(self.client().post(f"/v2/orders/{export.id}/facts", data={"etd": "2026-09-25"}).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_etd_route_keeps_all_writes_pending_until_service_claims(self):
        for role, value in ((0, "2026-09-25"), (0, "2026-09-26"), (0, "")):
            customer, export = self.pair()
            subject = (customer, export)[role]
            old_etd = subject.etd
            subject_id = subject.id
            # Force the route's items access to execute its normal lazy query.
            db.session.expire(subject, ["items"])
            writes = []
            def observe(conn, cursor, statement, params, context, many):
                if statement.lstrip().split()[0].upper() in {"UPDATE", "INSERT", "DELETE"}:
                    writes.append((statement, params))
            def service(row, requested, **kwargs):
                self.assertEqual(row.etd, old_etd)
                self.assertEqual(writes, [])
                return save_linked_etd(row, requested, **kwargs)
            def claim(c, e):
                self.assertEqual(writes, [])
                _claim_pair(c, e)
                self.assertEqual(len(writes), 2)
                self.assertEqual([params[0] for _, params in writes], sorted([c.id, e.id]))
                self.assertTrue(all("SET status=pi.status" in sql for sql, _ in writes))
            event.listen(db.engine, "before_cursor_execute", observe)
            try:
                with patch("v2.web.save_linked_etd", side_effect=service), patch("v2.linked_shipment._claim_pair", side_effect=claim):
                    response = self.client().post(f"/v2/orders/{subject_id}/facts", data={
                        "etd": value, "eta": "2026-10-01", "vessel_info": "Pending vessel",
                    })
                    self.assertEqual(response.status_code, 302)
            finally:
                event.remove(db.engine, "before_cursor_execute", observe)
            self.assertEqual(customer.etd, export.etd)
            self.assertEqual(subject.eta, date(2026, 10, 1))
            self.assertIsNone((export if subject is customer else customer).eta)

    def test_etd_reloads_peer_eta_changed_after_resolution_before_claim(self):
        customer, export = self.pair()
        customer.eta = date(2026, 10, 1); db.session.commit()
        self.assertEqual(customer.eta, date(2026, 10, 1))
        customer_id = customer.id
        old_etds = customer.etd, export.etd
        def claim(c, e):
            # Simulate the other writer committing before we acquire locks.
            with db.engine.begin() as connection:
                connection.execute(update(PI).where(PI.id == customer_id).values(eta=date(2026, 9, 21)))
            self.assertEqual(c.eta, date(2026, 10, 1))
            _claim_pair(c, e)
        with patch("v2.linked_shipment._claim_pair", side_effect=claim):
            with self.assertRaisesRegex(LinkedShipmentError, "ETA"):
                save_linked_etd(export, date(2026, 9, 25))
        self.assertEqual((customer.etd, export.etd), old_etds)
        self.assertEqual(customer.eta, date(2026, 9, 21))
        self.assertEqual((OrderTask.query.count(), TaskActivity.query.count()), (0, 0))

    def test_etd_revalidates_status_after_claim_before_mutation(self):
        customer, export = self.pair()
        before = self.snapshot()
        def claim(c, e):
            _claim_pair(c, e)
            db.session.execute(update(PI).where(PI.id == c.id).values(status="SHIPPED")
                               .execution_options(synchronize_session=False))
        with patch("v2.linked_shipment._claim_pair", side_effect=claim):
            with self.assertRaises(LinkedShipmentError):
                save_linked_etd(export, None)
        self.assertEqual(self.snapshot(), before)

    def test_shipping_changes_preserve_commercial_snapshots_and_finances(self):
        customer, export = self.pair()
        ignored = {"status", "actual_departure_date", "etd", "updated_at"}
        def commercial():
            return [{column.name: getattr(row, column.name) for column in PI.__table__.columns if column.name not in ignored}
                    for row in (customer, export)]
        before = commercial()
        items = list(db.session.execute(PIItem.__table__.select()).tuples())
        save_linked_etd(customer, date(2026, 9, 22))
        record_linked_actual_departure(export, date(2026, 9, 18))
        self.assertEqual(commercial(), before)
        self.assertEqual(list(db.session.execute(PIItem.__table__.select()).tuples()), items)

    def test_reconcile_starts_customer_post_shipment_and_retires_export_gates(self):
        customer, export = self.pair()
        for code in ("STAGE_GATE_SHIPPED", "STAGE_GATE_ARRIVED", "ARRIVAL_CUSTOMER_PICKUP"):
            db.session.add(OrderTask(pi_id=export.id, task_code=code, title=code, source="AUTO", status="ACTION",
                                     health="NORMAL", completion_mode="RULE_DATA",
                                     dedupe_key=f"v2:order:{export.id}:{code.lower()}"))
        db.session.commit()
        self.assertEqual(self.departure(export).status_code, 302)
        export_tasks = list(db.session.scalars(db.select(OrderTask).where(OrderTask.pi_id == export.id)))
        self.assertFalse(any(task.status not in {"DONE", "CANCELLED"} and
                             (task.task_code.startswith("STAGE_GATE_") or task.task_code.startswith("SHIPPING_") or task.task_code == "ARRIVAL_CUSTOMER_PICKUP")
                             for task in export_tasks))
        customer_tasks = {task.task_code: task for task in db.session.scalars(db.select(OrderTask).where(OrderTask.pi_id == customer.id))}
        self.assertEqual(customer_tasks["PAYMENT_EMAIL"].status, "ACTION")
        self.assertEqual(customer_tasks["DOCUMENT_ORIGINAL_BL"].status, "ACTION")
        self.assertGreater(TaskActivity.query.count(), 0)

    def test_get_is_read_only_and_linked_form_needs_departure_only(self):
        customer, export = self.pair()
        customer.etd = export.etd = None
        for row in (customer, export): reconcile_order_tasks_for_pi(row)
        db.session.commit()
        statements = []
        def observe(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}: statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", observe)
        try:
            dashboard = self.client().get("/v2/")
            self.assertEqual(dashboard.status_code, 200)
            self.assertIn(f'/v2/orders/{customer.id}/enter-shipped', dashboard.get_data(as_text=True))
            self.assertNotIn(f'/v2/orders/{export.id}/enter-shipped', dashboard.get_data(as_text=True))
            for row in (customer, export):
                page = self.client().get(f"/v2/orders/{row.id}/enter-shipped", follow_redirects=True)
                self.assertEqual(page.status_code, 200)
                self.assertIn("COMPLETED", page.get_data(as_text=True))
                self.assertNotRegex(page.get_data(as_text=True), r'name="(?:shipping_company|bill_of_lading_number)"\s+required')
        finally:
            event.remove(db.engine, "before_cursor_execute", observe)
        self.assertEqual(statements, [])

    def test_missing_departure_and_csrf_are_rejected(self):
        customer, export = self.pair()
        before = self.snapshot()
        self.assertEqual(self.client().post(f"/v2/orders/{export.id}/enter-shipped", data={}).status_code, 400)
        client = self.client(csrf=True)
        self.assertEqual(client.post(f"/v2/orders/{export.id}/enter-shipped", data={"actual_departure_date": "2026-09-18"}).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_completed_export_normal_facts_and_single_order_date_correction_blocked(self):
        customer, export = self.pair()
        self.assertEqual(self.departure(export).status_code, 302)
        client = self.client()
        self.assertEqual(client.post(f"/v2/orders/{export.id}/facts", data={"driver_name": "Changed"}).status_code, 403)
        self.assertEqual(client.post(f"/v2/orders/{export.id}/corrections", data={"module": "SHIPPING", "reason": "Correction"}).status_code, 302)
        correction = db.session.scalar(db.select(OrderCorrectionSession).where(OrderCorrectionSession.pi_id == export.id))
        for field in ("etd", "actual_departure_date"):
            self.assertEqual(client.post(f"/v2/orders/{export.id}/facts", data={field: "2026-09-19"}).status_code, 409)
            self.assertEqual(client.post(f"/v2/corrections/{correction.id}/edit", data={field: ""}).status_code, 409)
        self.assertEqual(client.post(f"/v2/corrections/{correction.id}/edit", data={"driver_name": "Corrected"}).status_code, 302)
        self.assert_departed(customer, export)

    def test_export_physical_editors_reject_values_and_clearing(self):
        customer, export = self.pair()
        client = self.client()
        for status in ("NEW", "PRE_SHIPMENT", "SHIPPED", "ARRIVED"):
            export.status = status; db.session.commit()
            before = self.snapshot()
            for field in PHYSICAL_FORM_FIELDS:
                for value in ("", "2026-09-21"):
                    with self.subTest(status=status, field=field, value=value):
                        self.assertEqual(client.post(f"/v2/orders/{export.id}/facts", data={field: value}).status_code, 409)
            self.assertEqual(client.post(f"/v2/orders/{export.id}/eta", data={"eta": "2026-09-25"}).status_code, 409)
            self.assertEqual(client.post(f"/v2/orders/{export.id}/enter-pre-shipment").status_code, 409)
            self.assertEqual(self.snapshot(), before)

    def test_customer_physical_copies_are_allowlisted_and_clear_atomically(self):
        customer, export = self.pair()
        export.payment_terms = "Export terms"; export.shipping_mark = "Export mark"
        export.driver_name = "Historical driver"; export.eta = date(2026, 10, 15)
        db.session.commit()
        protected = {column.name: getattr(export, column.name) for column in PI.__table__.columns
                     if column.name not in set(SHARED_PHYSICAL_FIELDS) | {"updated_at"}}
        response = self.client().post(f"/v2/orders/{customer.id}/facts", data={
            "container_loading_date": "2026-09-19", "container_loading_period": "PM",
            "container_location": "Warehouse", "container_type": "40HC", "container_count": "2",
            "booking_number": "BOOK-NEW", "vessel_info": "VESSEL-NEW", "package_count": "80",
            "package_unit": "BAGS", "gross_weight": "2", "gross_weight_display_unit": "MT", "volume": "12",
            "driver_name": "Customer driver", "driver_phone": "123", "vehicle_number": "CAR",
        })
        self.assertEqual(response.status_code, 302)
        for field in SHARED_PHYSICAL_FIELDS:
            self.assertEqual(getattr(export, field), getattr(customer, field), field)
        self.assertEqual(export.gross_weight_kg, Decimal("2000"))
        for field, value in protected.items():
            self.assertEqual(getattr(export, field), value, field)
        self.assertEqual(self.client().post(f"/v2/orders/{customer.id}/facts", data={
            "container_loading_date": "", "package_count": "", "gross_weight": "", "volume": "",
        }).status_code, 302)
        for field in ("container_loading_date", "package_count", "gross_weight_kg", "volume_cbm"):
            self.assertIsNone(getattr(customer, field)); self.assertIsNone(getattr(export, field))

    def test_physical_copy_failure_rolls_back_source_peer_and_task_history(self):
        customer, export = self.pair()
        before = self.snapshot()
        def fail_customer(row):
            reconcile_order_tasks_for_pi(row)
            if row.id == customer.id:
                raise RuntimeError("copy reconcile failed")
        with patch("v2.linked_shipment.reconcile_order_tasks_for_pi", side_effect=fail_customer):
            with self.assertRaisesRegex(RuntimeError, "copy reconcile"):
                self.client().post(f"/v2/orders/{customer.id}/facts", data={"container_location": "NEW", "package_count": "4"})
        self.assertEqual(self.snapshot(), before)

    def test_export_shared_context_is_read_only_and_keeps_document_identity(self):
        customer, export = self.pair()
        customer.vessel_info = "CUSTOMER-VESSEL"; customer.container_location = "CUSTOMER-LOCATION"
        customer.driver_name = "CUSTOMER-DRIVER"; customer.customer_name_snapshot = "PRIVATE-CUSTOMER-IDENTITY"
        customer.bank_name_snapshot = "PRIVATE-CUSTOMER-BANK"
        export.customer_name_snapshot = "EXPORT-IDENTITY"; export.bank_name_snapshot = "EXPORT-BANK"
        export.vessel_info = "OLD-EXPORT-VESSEL"
        db.session.commit()
        before = self.snapshot()
        html = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)
        shared = html.split('id="shared-shipment"', 1)[1].split('</section>', 1)[0]
        for value in (customer.pi_no, "CUSTOMER-VESSEL", "CUSTOMER-LOCATION", "CUSTOMER-DRIVER"):
            self.assertIn(value, shared)
        for value in ("PRIVATE-CUSTOMER-IDENTITY", "PRIVATE-CUSTOMER-BANK", "OLD-EXPORT-VESSEL"):
            self.assertNotIn(value, shared)
        for form_id in ('<form id="shipment-preparation"', '<form id="shipping-schedule"', '<form id="booking-cargo-information"'):
            self.assertNotIn(form_id, html)
        self.assertIn("EXPORT-IDENTITY", html); self.assertIn("EXPORT-BANK", html)
        self.assertEqual(self.snapshot(), before)
        response = self.client().get(f"/v2/orders/{export.id}/enter-shipped")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith(f"/v2/orders/{customer.id}/enter-shipped"))

    def test_export_physical_history_retires_without_suppressing_documents(self):
        customer, export = self.pair()
        codes = ("STAGE_GATE_PRE_SHIPMENT", "SHIPPING_PLANNED_DATE_OVERDUE", "SHIPPING_CONTAINER_LOADING",
                 "SHIPPING_DRIVER_INFO", "SHIPPING_ACTUAL_DEPARTURE", "SHIPPING_ACTUAL_ARRIVAL", "ARRIVAL_CUSTOMER_PICKUP")
        for code in codes:
            task = OrderTask(pi_id=export.id, task_code=code, title=code, source="AUTO", status="ACTION",
                             health="NORMAL", completion_mode="RULE_DATA", dedupe_key=f"v2:order:{export.id}:{code.lower()}")
            db.session.add(task); db.session.flush()
            db.session.add(TaskActivity(task_id=task.id, event_type="CREATED", actor_type="SYSTEM"))
        export.export_license_required = True; export.container_loading_date = date(2099, 1, 1)
        db.session.commit()
        original_history = {row.id for row in TaskActivity.query.all()}
        physical = db.session.scalar(db.select(OrderTask).where(OrderTask.pi_id == export.id,
                                                                 OrderTask.task_code == "SHIPPING_DRIVER_INFO"))
        self.assertEqual(self.client().post(f"/v2/tasks/{physical.id}/waiting", data={"waiting_on": "FACTORY"}).status_code, 409)
        self.assertEqual(physical.status, "ACTION")
        reconcile_order_tasks_for_pi(export); db.session.commit()
        tasks = list(db.session.scalars(db.select(OrderTask).where(OrderTask.pi_id == export.id)))
        self.assertFalse(any(is_physical_shipment_task(t.task_code) and t.status not in {"DONE", "CANCELLED"} for t in tasks))
        self.assertTrue(original_history <= {row.id for row in TaskActivity.query.all()})
        license_task = next(t for t in tasks if t.task_code == "DOCUMENT_EXPORT_LICENSE")
        self.assertEqual(license_task.status, "UPCOMING")  # Existing calendar rule, not Batch 3B.

    def test_new_pair_uses_customer_preparation_gate_only(self):
        for generic in (False, True):
            customer, export = self.pair()
            customer.status = export.status = "NEW"
            customer.actual_departure_date = export.actual_departure_date = None
            db.session.add(OrderTask(pi_id=customer.id, task_code="STAGE_GATE_PRE_SHIPMENT", title="Prepare",
                                     source="AUTO", status="ACTION", health="NORMAL", completion_mode="RULE_DATA",
                                     dedupe_key=f"v2:order:{customer.id}:stage_gate_pre_shipment"))
            db.session.commit()
            suffix = "status" if generic else "enter-pre-shipment"
            self.assertEqual(self.client().post(f"/v2/orders/{customer.id}/{suffix}", data={"status": "PRE_SHIPMENT"}).status_code, 302)
            self.assertEqual((customer.status, export.status), ("PRE_SHIPMENT", "PRE_SHIPMENT"))
            self.assertEqual(self.departure(customer).status_code, 302)
            self.assert_departed(customer, export)

    def test_new_pair_preparation_failure_rolls_back_both_stages(self):
        customer, export = self.pair()
        customer.status = export.status = "NEW"
        db.session.add(OrderTask(pi_id=customer.id, task_code="STAGE_GATE_PRE_SHIPMENT", title="Prepare",
                                 source="AUTO", status="ACTION", health="NORMAL", completion_mode="RULE_DATA",
                                 dedupe_key=f"v2:order:{customer.id}:stage_gate_pre_shipment"))
        db.session.commit()
        before = self.snapshot()
        def fail(row):
            reconcile_order_tasks_for_pi(row)
            if row is customer:
                raise RuntimeError("preparation reconcile failed")
        with patch("v2.linked_shipment.reconcile_order_tasks_for_pi", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "preparation reconcile"):
                self.client().post(f"/v2/orders/{customer.id}/enter-pre-shipment")
        self.assertEqual(self.snapshot(), before)
