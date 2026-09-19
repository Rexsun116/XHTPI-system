"""Country B data foundation tests using only disposable SQLite databases."""

from datetime import date
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest import TestCase

from v2.app import create_app
from v2.iso3166 import ISO_3166_1_ALPHA2
from v2.models import Customer, PI, db
from v2.shipment_ownership import (
    RouteGeographyValidationError, copy_physical_facts, normalize_country_code,
    validate_route_geography,
)


ROOT = Path(__file__).resolve().parents[1]


class ShipmentCountryContractTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xhtpi-v2-country-")
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'country.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.customer = Customer(code="COUNTRY-C", name="Country customer")
        db.session.add(self.customer); db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); db.engine.dispose()
        self.ctx.pop(); self.tmp.cleanup()

    def pi(self, number):
        row = PI(pi_no=number, pi_date=date(2026, 9, 19), order_type="SALES", status="NEW",
                 customer_id=self.customer.id, customer_name_snapshot=self.customer.name,
                 currency="USD")
        db.session.add(row); db.session.flush()
        return row

    def test_model_allows_null_and_persists_valid_explicit_codes(self):
        historical = self.pi("COUNTRY-NULL")
        explicit = self.pi("COUNTRY-VALID")
        explicit.loading_country_code = normalize_country_code(" ke ")
        explicit.destination_country_code = normalize_country_code("cn")
        db.session.commit(); db.session.expire_all()
        self.assertIsNone(db.session.get(PI, historical.id).loading_country_code)
        self.assertEqual((db.session.get(PI, explicit.id).loading_country_code,
                          db.session.get(PI, explicit.id).destination_country_code), ("KE", "CN"))

    def test_normalization_and_complete_iso_membership(self):
        self.assertEqual(normalize_country_code("ke"), "KE")
        self.assertEqual(normalize_country_code(" Ke "), "KE")
        self.assertIsNone(normalize_country_code(" \t "))
        self.assertEqual(len(ISO_3166_1_ALPHA2), 249)
        for code in ("CN", "KE", "SA", "AE", "IN", "ZA", "UG", "JP", "KR", "BR", "DE", "NZ"):
            self.assertEqual(normalize_country_code(code.lower()), code)
        for invalid in ("KEN", "Kenya", "XX", "ZZ", "K1", "K", "ABC"):
            with self.assertRaises(RouteGeographyValidationError):
                normalize_country_code(invalid)

    def test_country_aware_route_validation_has_the_approved_asymmetry(self):
        self.assertEqual(validate_route_geography(
            loading_port="Shanghai", loading_country_code="cn",
            destination_port="Mombasa", destination_country_code="ke",
        ), ("CN", "KE"))
        self.assertEqual(validate_route_geography(
            loading_country_code="sa", destination_country_code="ae",
        ), ("SA", "AE"))
        self.assertEqual(validate_route_geography(), (None, None))
        self.assertEqual(validate_route_geography(loading_port="  ", destination_port="\t"), (None, None))
        for kwargs in (
            {"loading_port": "Shanghai"},
            {"destination_port": "Mombasa"},
            {"loading_port": "Shanghai", "destination_country_code": "KE"},
            {"destination_port": "Mombasa", "loading_country_code": "CN"},
        ):
            with self.assertRaises(RouteGeographyValidationError):
                validate_route_geography(**kwargs)

    def test_historical_port_without_country_remains_readable_without_validation(self):
        historical = self.pi("COUNTRY-HISTORICAL")
        historical.loading_port, historical.destination_port = "Shanghai", "Mombasa"
        db.session.commit(); db.session.expire_all()
        restored = db.session.get(PI, historical.id)
        self.assertEqual((restored.loading_port, restored.destination_port), ("Shanghai", "Mombasa"))
        self.assertIsNone(restored.loading_country_code)
        self.assertIsNone(restored.destination_country_code)

    def test_shared_physical_copy_keeps_customer_country_authoritative(self):
        customer, export = self.pi("COUNTRY-CUSTOMER"), self.pi("COUNTRY-EXPORT")
        customer.loading_country_code, customer.destination_country_code = "CN", "KE"
        export.loading_country_code, export.destination_country_code = "AE", "SA"
        copy_physical_facts(customer, export)
        self.assertEqual((export.loading_country_code, export.destination_country_code), ("CN", "KE"))
        self.assertEqual((customer.loading_country_code, customer.destination_country_code), ("CN", "KE"))


class ShipmentCountryMigrationTest(TestCase):
    def command(self, path, *args):
        env = os.environ | {"XHTPI_V2_DATABASE_URL": f"sqlite:///{path}"}
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "migrations_v2/alembic.ini", *args],
            cwd=ROOT, env=env, check=True, capture_output=True, text=True,
        )

    def test_upgrade_preserves_historical_null_codes_and_downgrade_removes_columns(self):
        with tempfile.TemporaryDirectory(prefix="xhtpi-v2-0006-") as directory:
            path = Path(directory) / "migration.db"
            self.command(path, "upgrade", "v2_0005")
            now = "2026-09-19 12:00:00"
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO customer (id, code, name, active, created_at, updated_at) VALUES (1, 'C1', 'Customer', 1, ?, ?)", (now, now))
                connection.execute("INSERT INTO pi (id, pi_no, pi_date, order_type, status, customer_id, customer_name_snapshot, currency, loading_port, destination_port, created_at, updated_at) VALUES (1, 'COUNTRY-HIST', '2026-09-19', 'SALES', 'NEW', 1, 'Customer', 'USD', 'Shanghai', 'Mombasa', ?, ?)", (now, now))
                connection.commit()
            self.command(path, "upgrade", "v2_0006")
            with sqlite3.connect(path) as connection:
                columns = {row[1]: row for row in connection.execute("PRAGMA table_info(pi)")}
                self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "v2_0006")
                self.assertEqual((columns["loading_country_code"][2], columns["loading_country_code"][3]), ("VARCHAR(2)", 0))
                self.assertEqual(connection.execute("SELECT loading_port, destination_port, loading_country_code, destination_country_code FROM pi WHERE id=1").fetchone(), ("Shanghai", "Mombasa", None, None))
            self.command(path, "downgrade", "v2_0005")
            with sqlite3.connect(path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(pi)")}
                self.assertNotIn("loading_country_code", columns)
                self.assertNotIn("destination_country_code", columns)
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
