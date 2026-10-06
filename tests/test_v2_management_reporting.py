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

    def add_item(self, pi, *, quantity, unit, amount="1", product="R-996"):
        pi.items.append(PIItem(unit_price=Decimal(amount) / Decimal(quantity), quantity=Decimal(quantity),
                               quantity_unit=unit, line_total=Decimal(amount), product_model_snapshot=product))
        db.session.commit()

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

    def test_currency_specific_analytical_population_metadata_is_separate(self):
        self.make_order("USD-ANALYSIS", amount="100", currency="USD", quantity="1", unit="MT",
                        product="Shared Product", country="KE", customer_snapshot="USD Customer")
        self.make_order("CNY-ANALYSIS", customer=self.other_customer, amount="200", currency="CNY", quantity="500", unit="KGS",
                        product="Shared Product", country="CN", customer_snapshot="CNY Customer")
        report = self.report()
        customers = {row["label"]: row for row in report["customers"]}
        product = report["products"][0]
        countries = {row["country_code"]: row for row in report["geography"]["countries"]}

        self.assertEqual(customers["USD Customer"]["order_count_by_currency"], {"USD": 1})
        self.assertEqual(customers["CNY Customer"]["order_count_by_currency"], {"CNY": 1})
        self.assertEqual(product["order_count_by_currency"], {"CNY": 1, "USD": 1})
        self.assertEqual(product["customer_count_by_currency"], {"CNY": 1, "USD": 1})
        self.assertEqual(product["quantity_by_unit_by_currency"], {
            "CNY": {"KGS": Decimal("500")}, "USD": {"MT": Decimal("1")},
        })
        self.assertEqual(countries["KE"]["order_count_by_currency"], {"USD": 1})
        self.assertEqual(countries["CN"]["order_count_by_currency"], {"CNY": 1})

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

    def test_reporting_year_scopes_all_owner_derived_metrics_without_changing_link_selection(self):
        historical = self.make_order("YEAR-2025", amount="100", pi_date=date(2025, 8, 22),
                                     product="Historical Product", country="KE", port="Mombasa",
                                     quantity="2", unit="MT")
        current = self.make_order("YEAR-2026", amount="900", pi_date=date(2026, 8, 22),
                                  product="Current Product", country="CN", port="Shanghai",
                                  quantity="9", unit="MT")
        db.session.add_all([
            FreightSettlement(pi_id=historical.id, usd_bill_required=True, usd_bill_amount=Decimal("10"),
                             usd_bill_confirmed=True, usd_payment_status="UNPAID", cny_bill_required=False),
            FreightSettlement(pi_id=current.id, usd_bill_required=True, usd_bill_amount=Decimal("90"),
                             usd_bill_confirmed=True, usd_payment_status="UNPAID", cny_bill_required=False),
        ])
        db.session.commit()

        report = build_management_report(
            list(db.session.scalars(db.select(PI))), list(db.session.scalars(db.select(FreightSettlement))),
            as_of=date(2025, 12, 31), reporting_year=2025,
        )
        self.assertEqual(report["reporting_owner_ids"], [historical.id])
        self.assertEqual(report["kpis"]["contracted_by_currency"], {"USD": Decimal("100")})
        self.assertEqual(report["kpis"]["open_order_weight_mt"], Decimal("2"))
        self.assertEqual(report["customers"][0]["order_count"], 1)
        self.assertEqual([row["product_snapshot"] for row in report["products"]], ["Historical Product"])
        self.assertEqual([row["country_code"] for row in report["geography"]["countries"]], ["KE"])
        self.assertEqual(report["freight_context"]["freight_unpaid_by_currency"], {"CNY": Decimal("0.00"), "USD": Decimal("10")})

    def test_selected_current_year_uses_ytd_cutoff_while_historical_year_is_full_calendar_year(self):
        current_future = self.make_order("CURRENT-FUTURE", amount="90", pi_date=date(2026, 10, 1))
        historical_late = self.make_order("HISTORICAL-LATE", amount="80", pi_date=date(2025, 12, 15))
        orders = list(db.session.scalars(db.select(PI)))
        current = build_management_report(orders, as_of=date(2026, 9, 30), reporting_year=2026)
        historical = build_management_report(orders, as_of=date(2025, 12, 31), reporting_year=2025)
        self.assertNotIn(current_future.id, current["reporting_owner_ids"])
        self.assertIn(historical_late.id, historical["reporting_owner_ids"])

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
        self.assertEqual(report["freight_context"]["freight_total_billed_by_currency"],
                         {"CNY": Decimal("0.00"), "USD": Decimal("30")})
        self.assertEqual(report["freight_context"]["freight_unpaid_by_currency"],
                         {"CNY": Decimal("0.00"), "USD": Decimal("30")})
        self.assertEqual(report["freight_context"]["orders_with_missing_settlement_count"], 0)
        self.assertEqual(report["freight_context"]["orders_with_incomplete_settlement_count"], 1)

    def test_open_weight_normalizes_mt_and_kgs_per_line(self):
        self.make_order("OPEN-MT", quantity="2.500", unit=" mt ")
        self.make_order("OPEN-KGS", quantity="1500", unit="kGs")
        mixed = self.make_order("OPEN-MIXED", quantity="500", unit="KGS")
        self.add_item(mixed, quantity="1.250", unit="MT")
        report = self.report()
        self.assertEqual(report["kpis"]["open_order_weight_mt"], Decimal("5.750"))
        self.assertEqual(report["kpis"]["unshipped_open_order_weight_mt"], Decimal("5.750"))
        self.assertEqual(report["quality"]["open_order_weight_excluded_line_count"], 0)
        self.assertEqual(report["quality"]["open_order_weight_excluded_units"], [])

    def test_open_weight_excludes_unsupported_units_without_losing_supported_lines(self):
        mixed = self.make_order("OPEN-SUPPORTED", quantity="2", unit="MT")
        self.add_item(mixed, quantity="12", unit="BAGS")
        self.make_order("OPEN-BAGS", quantity="20", unit="BAGS")
        report = self.report()
        self.assertEqual(report["kpis"]["open_order_weight_mt"], Decimal("2"))
        self.assertEqual(report["kpis"]["unshipped_open_order_weight_mt"], Decimal("2"))
        self.assertEqual(report["quality"]["open_order_weight_excluded_line_count"], 2)
        self.assertEqual(report["quality"]["open_order_weight_excluded_units"], ["BAGS"])

    def test_open_weight_excludes_completed_and_unshipped_requires_no_actual_departure(self):
        self.make_order("COMPLETE", status="COMPLETED", quantity="40", unit="MT",
                        departed=date(2026, 9, 1), arrived=date(2026, 9, 4))
        self.make_order("OPEN-SHIPPED", status="SHIPPED", quantity="3", unit="MT",
                        departed=date(2026, 9, 2))
        self.make_order("OPEN-UNSHIPPED", status="PRE_SHIPMENT", quantity="4", unit="MT")
        report = self.report()
        self.assertEqual(report["kpis"]["open_order_weight_mt"], Decimal("7"))
        self.assertEqual(report["kpis"]["unshipped_open_order_weight_mt"], Decimal("4"))

    def test_open_weight_uses_the_existing_linked_reporting_owner_population(self):
        customer = self.make_order("WEIGHT-CUSTOMER", quantity="5", unit="MT")
        export = self.make_order("WEIGHT-EXPORT", quantity="9000", unit="KGS", include=True)
        group = TradeGroup(group_no="REPORT-WEIGHT-PAIR")
        db.session.add(group)
        customer.trade_group = export.trade_group = group
        customer.trade_role, export.trade_role = "CUSTOMER_ORDER", "EXPORT_ORDER"
        malformed = self.make_order("WEIGHT-MALFORMED", quantity="30", unit="MT")
        broken_group = TradeGroup(group_no="REPORT-WEIGHT-BROKEN")
        db.session.add(broken_group)
        malformed.trade_group, malformed.trade_role = broken_group, "CUSTOMER_ORDER"
        db.session.commit()
        report = self.report()
        self.assertEqual(report["kpis"]["open_order_weight_mt"], Decimal("5"))
        self.assertEqual(report["quality"]["malformed_linked_metadata_excluded_count"], 1)

    def test_freight_totals_and_unpaid_are_currency_specific_and_binary(self):
        usd_unpaid = self.make_order("USD-UNPAID")
        usd_paid = self.make_order("USD-PAID")
        cny_unpaid = self.make_order("CNY-UNPAID")
        cny_paid = self.make_order("CNY-PAID")
        unconfirmed = self.make_order("UNCONFIRMED")
        non_required = self.make_order("NOT-REQUIRED")
        db.session.add_all([
            FreightSettlement(pi_id=usd_unpaid.id, usd_bill_required=True, usd_bill_amount=Decimal("10"),
                             usd_bill_confirmed=True, usd_payment_status="UNPAID", cny_bill_required=False),
            FreightSettlement(pi_id=usd_paid.id, usd_bill_required=True, usd_bill_amount=Decimal("20"),
                             usd_bill_confirmed=True, usd_payment_status="PAID", cny_bill_required=False),
            FreightSettlement(pi_id=cny_unpaid.id, cny_bill_required=True, cny_bill_amount=Decimal("30"),
                             cny_bill_confirmed=True, cny_payment_status="UNPAID", usd_bill_required=False),
            FreightSettlement(pi_id=cny_paid.id, cny_bill_required=True, cny_bill_amount=Decimal("40"),
                             cny_bill_confirmed=True, cny_payment_status="PAID", usd_bill_required=False),
            FreightSettlement(pi_id=unconfirmed.id, usd_bill_required=True, usd_bill_amount=Decimal("50"),
                             usd_bill_confirmed=False, cny_bill_required=False),
            FreightSettlement(pi_id=non_required.id, usd_bill_required=False, usd_bill_amount=Decimal("60"),
                             usd_bill_confirmed=True, cny_bill_required=False),
        ])
        db.session.commit()
        freight = self.report()["freight_context"]
        self.assertEqual(freight["freight_total_billed_by_currency"],
                         {"CNY": Decimal("70"), "USD": Decimal("30")})
        self.assertEqual(freight["freight_unpaid_by_currency"],
                         {"CNY": Decimal("30"), "USD": Decimal("10")})
        self.assertEqual(freight["confirmed_bill_amount_by_currency"],
                         freight["freight_total_billed_by_currency"])
        self.assertEqual(freight["orders_with_incomplete_settlement_count"], 1)

    def test_open_weight_and_freight_outputs_are_zero_safe_for_empty_dataset(self):
        report = build_management_report([], [], as_of=date(2026, 9, 30))
        self.assertEqual(report["kpis"]["open_order_weight_mt"], Decimal("0.00"))
        self.assertEqual(report["kpis"]["unshipped_open_order_weight_mt"], Decimal("0.00"))
        self.assertEqual(report["freight_context"]["freight_total_billed_by_currency"],
                         {"CNY": Decimal("0.00"), "USD": Decimal("0.00")})
        self.assertEqual(report["freight_context"]["freight_unpaid_by_currency"],
                         {"CNY": Decimal("0.00"), "USD": Decimal("0.00")})
        self.assertEqual(report["quality"]["open_order_weight_excluded_line_count"], 0)

    def test_empty_dataset_is_a_valid_zero_report(self):
        report = build_management_report([], [], as_of=date(2026, 9, 30))
        self.assertEqual(report["reporting_owner_ids"], [])
        self.assertEqual(report["kpis"]["ytd_reporting_owner_order_count"], 0)
        self.assertEqual(report["kpis"]["contracted_by_currency"], {})
        self.assertEqual(report["customers"], [])
        self.assertEqual(report["products"], [])
        self.assertEqual(report["quality"]["destination_country_coverage_rate"], Decimal("0"))
