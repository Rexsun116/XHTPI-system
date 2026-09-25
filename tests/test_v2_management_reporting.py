"""Isolated coverage for the read-only V2 management reporting layer."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase

from v2.app import create_app
from v2.models import Customer, FreightSettlement, PI, PIItem, TradeGroup, db
from v2.reporting import build_management_report


class ManagementReportingTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Path(self.tmp.name) / "management-reporting.db"
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts" / "init_v2_test_db.py"), str(database)],
                       cwd=root, check=True, capture_output=True, text=True)
        self.app = create_app(f"sqlite:///{database}", testing=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.customer = Customer(code="C1", name="Original Customer")
        self.other_customer = Customer(code="C2", name="Other Customer")
        db.session.add_all([self.customer, self.other_customer])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()
        self.tmp.cleanup()

    def make_order(self, number, *, customer=None, amount="100", currency="USD", pi_date=date(2026, 9, 1),
                   status="NEW", quantity="10", unit="MT", product="R-996", country="KE", port="Mombasa",
                   planned=date(2026, 10, 1), departed=None, arrived=None, advance=None, balance=None,
                   customer_snapshot=None, include=True):
        customer = customer or self.customer
        pi = PI(
            pi_no=number, pi_date=pi_date, order_type="SALES", status=status,
            customer_id=customer.id, customer_name_snapshot=(customer.name if customer_snapshot is None else customer_snapshot),
            currency=currency, destination_country_code=country, destination_port=port,
            planned_shipment_date=planned, actual_departure_date=departed, actual_arrival_date=arrived,
            advance_received_amount=advance, balance_received_amount=balance,
            include_in_business_stats=include,
        )
        pi.items.append(PIItem(unit_price=Decimal(amount) / Decimal(quantity), quantity=Decimal(quantity),
                               quantity_unit=unit, line_total=Decimal(amount), product_model_snapshot=product))
        db.session.add(pi)
        db.session.commit()
        return pi

    def report(self, *, as_of=date(2026, 9, 30)):
        return build_management_report(list(db.session.scalars(db.select(PI))),
                                       list(db.session.scalars(db.select(FreightSettlement))), as_of=as_of)

    def test_standalone_is_included_and_ytd_kpis_use_business_dates(self):
        self.make_order("STANDALONE", amount="100", pi_date=date(2026, 2, 2),
                        departed=date(2026, 3, 3), arrived=date(2026, 4, 4))
        report = self.report()
        kpis = report["kpis"]
        self.assertEqual(kpis["ytd_reporting_owner_order_count"], 1)
        self.assertEqual(kpis["ytd_booked_revenue_by_currency"], {"USD": Decimal("100")})
        self.assertEqual(kpis["ytd_shipped_order_count"], 1)
        self.assertEqual(kpis["ytd_shipped_revenue_by_currency"], {"USD": Decimal("100")})
        self.assertEqual(kpis["ytd_arrived_order_count"], 1)
        self.assertEqual(kpis["ytd_arrived_revenue_by_currency"], {"USD": Decimal("100")})
        self.assertEqual(report["time_series"]["2026-02"]["booked_order_count"], 1)
        self.assertEqual(report["time_series"]["2026-03"]["shipped_order_count"], 1)
        self.assertEqual(report["time_series"]["2026-04"]["arrived_order_count"], 1)

    def test_valid_linked_pair_counts_only_the_customer_order_once(self):
        customer = self.make_order("CUSTOMER", amount="100")
        export = self.make_order("EXPORT", amount="250", include=False)
        group = TradeGroup(group_no="REPORT-PAIR")
        db.session.add(group)
        customer.trade_group = export.trade_group = group
        customer.trade_role, export.trade_role = "CUSTOMER_ORDER", "EXPORT_ORDER"
        customer.include_in_business_stats, export.include_in_business_stats = True, False
        db.session.commit()
        report = self.report()
        self.assertEqual(report["reporting_owner_ids"], [customer.id])
        self.assertEqual(report["kpis"]["ytd_booked_revenue_by_currency"], {"USD": Decimal("100")})
        self.assertEqual(report["quality"]["malformed_linked_metadata_excluded_count"], 0)

    def test_export_order_is_excluded_even_if_its_include_flag_is_crafted_true(self):
        customer = self.make_order("OWNER", amount="100")
        export = self.make_order("EXPORT-CRAFTED", amount="999", include=True)
        group = TradeGroup(group_no="REPORT-EXCLUDE")
        db.session.add(group)
        customer.trade_group = export.trade_group = group
        customer.trade_role, export.trade_role = "CUSTOMER_ORDER", "EXPORT_ORDER"
        db.session.commit()
        report = self.report()
        self.assertEqual(report["reporting_owner_ids"], [customer.id])
        self.assertEqual(report["kpis"]["ytd_booked_revenue_by_currency"], {"USD": Decimal("100")})

    def test_incomplete_link_metadata_is_excluded_and_reported(self):
        malformed = self.make_order("MALFORMED", amount="100")
        group = TradeGroup(group_no="REPORT-BROKEN")
        db.session.add(group)
        malformed.trade_group, malformed.trade_role = group, "CUSTOMER_ORDER"
        db.session.commit()
        report = self.report()
        self.assertEqual(report["reporting_owner_ids"], [])
        self.assertEqual(report["quality"]["malformed_linked_metadata_excluded_count"], 1)
        self.assertEqual(report["quality"]["malformed_linked_metadata_excluded_order_ids"], [malformed.id])

    def test_currency_and_quantity_units_remain_separate(self):
        self.make_order("USD-MT", amount="100", currency="USD", quantity="1", unit="MT")
        self.make_order("EUR-KGS", amount="200", currency="EUR", quantity="500", unit="KGS")
        report = self.report()
        self.assertEqual(report["kpis"]["ytd_booked_revenue_by_currency"],
                         {"EUR": Decimal("200"), "USD": Decimal("100")})
        product = report["products"][0]
        self.assertEqual(product["quantity_by_unit"], {"KGS": Decimal("500"), "MT": Decimal("1")})
        self.assertTrue(report["quality"]["mixed_quantity_units"])
        self.assertEqual(report["quality"]["quantity_unit_types"], ["KGS", "MT"])

    def test_customer_and_product_labels_use_historical_snapshots(self):
        pi = self.make_order("SNAPSHOTS", customer_snapshot="Historical Customer", product="Historical Product")
        self.customer.name = "Renamed Customer"
        pi.items[0].product_model_snapshot = "Historical Product"
        db.session.commit()
        report = self.report()
        self.assertEqual(report["customers"][0]["label"], "Historical Customer")
        self.assertEqual(report["products"][0]["product_snapshot"], "Historical Product")

    def test_customer_snapshot_label_is_stable_when_history_has_multiple_names(self):
        first = self.make_order("CUSTOMER-OLD", customer_snapshot="Zeta Trading")
        second = self.make_order("CUSTOMER-NEW", customer_snapshot="Alpha Trading")
        orders = [first, second]
        forward = build_management_report(orders, as_of=date(2026, 9, 30))
        reverse = build_management_report(list(reversed(orders)), as_of=date(2026, 9, 30))
        self.assertEqual(forward["customers"][0]["label"], "Alpha Trading")
        self.assertEqual(reverse["customers"][0]["label"], "Alpha Trading")

        missing = self.make_order("CUSTOMER-MISSING", customer_snapshot="")
        mixed = build_management_report([missing, first], as_of=date(2026, 9, 30))
        self.assertEqual(mixed["customers"][0]["label"], "Zeta Trading")

    def test_missing_country_and_product_snapshot_are_explicit(self):
        self.make_order("MISSING", country=None, port="", product="", customer_snapshot="")
        report = self.report()
        self.assertEqual(report["geography"]["countries"][0]["country_code"], "Country not recorded")
        self.assertEqual(report["geography"]["destination_ports"], {"Port not recorded": 1})
        self.assertEqual(report["quality"]["missing_destination_country_count"], 1)
        self.assertEqual(report["quality"]["missing_product_snapshot_item_count"], 1)
        self.assertEqual(report["quality"]["missing_customer_snapshot_order_count"], 1)

    def test_receivables_handle_unpaid_partial_paid_and_overpaid(self):
        self.make_order("UNPAID", amount="100")
        self.make_order("PARTIAL", amount="100", advance=Decimal("20"), balance=Decimal("30"))
        self.make_order("PAID", amount="100", advance=Decimal("100"))
        self.make_order("OVERPAID", amount="100", advance=Decimal("120"))
        kpis = self.report()["kpis"]
        self.assertEqual(kpis["contracted_by_currency"], {"USD": Decimal("400")})
        self.assertEqual(kpis["received_by_currency"], {"USD": Decimal("270")})
        self.assertEqual(kpis["outstanding_receivables_by_currency"], {"USD": Decimal("150")})
        self.assertEqual(kpis["collection_rate_by_currency"], {"USD": Decimal("0.675")})
        self.assertEqual(kpis["average_order_value_by_currency"], {"USD": Decimal("100")})

    def test_current_corrected_pi_values_are_reflected(self):
        pi = self.make_order("CORRECTED", amount="100")
        self.assertEqual(self.report()["kpis"]["contracted_by_currency"], {"USD": Decimal("100")})
        pi.items[0].unit_price = Decimal("15")
        pi.items[0].quantity = Decimal("10")
        pi.items[0].line_total = Decimal("150")
        db.session.commit()
        self.assertEqual(self.report()["kpis"]["contracted_by_currency"], {"USD": Decimal("150")})

    def test_transit_upcoming_and_confirmed_freight_context(self):
        transit = self.make_order("TRANSIT", amount="100", departed=date(2026, 9, 10), arrived=None)
        upcoming = self.make_order("UPCOMING", amount="200", planned=date(2026, 10, 2))
        settled = FreightSettlement(pi_id=transit.id, usd_bill_required=True,
                                    usd_bill_amount=Decimal("30"), usd_bill_confirmed=True,
                                    cny_bill_required=False)
        incomplete = FreightSettlement(pi_id=upcoming.id, cny_bill_required=True,
                                       cny_bill_amount=Decimal("5"), cny_bill_confirmed=False)
        db.session.add_all([settled, incomplete])
        db.session.commit()
        report = self.report()
        self.assertEqual(report["kpis"]["orders_in_transit_count"], 1)
        self.assertEqual(report["kpis"]["upcoming_shipments_count"], 1)
        self.assertEqual(report["freight_context"]["confirmed_bill_amount_by_currency"],
                         {"CNY": Decimal("0.00"), "USD": Decimal("30")})
        self.assertEqual(report["freight_context"]["orders_with_missing_settlement_count"], 0)
        self.assertEqual(report["freight_context"]["orders_with_incomplete_settlement_count"], 1)

    def test_empty_dataset_is_a_valid_zero_report(self):
        report = build_management_report([], [], as_of=date(2026, 9, 30))
        self.assertEqual(report["reporting_owner_ids"], [])
        self.assertEqual(report["kpis"]["ytd_reporting_owner_order_count"], 0)
        self.assertEqual(report["kpis"]["contracted_by_currency"], {})
        self.assertEqual(report["customers"], [])
        self.assertEqual(report["products"], [])
        self.assertEqual(report["quality"]["destination_country_coverage_rate"], Decimal("0"))
