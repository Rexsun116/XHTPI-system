"""Clean V2 Flask entrypoint; requires an explicit non-V1 database."""
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, login_required, login_user
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from werkzeug.security import check_password_hash
from .models import BankAccount, Customer, Exporter, Factory, PI, PIItem, Product, User, db
from .services import apply_bank_snapshot, apply_product_snapshot, save_order_with_reconcile


def _validate_database_uri(uri):
    if not uri:
        raise RuntimeError("XHTPI_V2_DATABASE_URL is required")
    if uri.startswith("sqlite:///"):
        target = Path(uri.removeprefix("sqlite:///")).expanduser().resolve()
        v1 = (Path(__file__).resolve().parents[1] / "instance" / "database.db").resolve()
        if target == v1 or target.parent == v1.parent:
            raise RuntimeError("V2 runtime refuses the V1 instance directory")


def create_app(database_uri, *, testing=False, secret_key=None):
    _validate_database_uri(database_uri)
    secret = secret_key or os.environ.get("XHTPI_V2_SECRET_KEY")
    if not testing and not secret:
        raise RuntimeError("XHTPI_V2_SECRET_KEY is required")
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secret or "test-only", SQLALCHEMY_DATABASE_URI=database_uri,
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, TESTING=testing,
                      WTF_CSRF_ENABLED=not testing)
    db.init_app(app)
    with app.app_context():
        def _fk_on(connection, _record):
            cursor = connection.cursor(); cursor.execute("PRAGMA foreign_keys=ON"); cursor.close()
        event.listen(db.engine, "connect", _fk_on)
    CSRFProtect(app)
    login = LoginManager(app); login.login_view = "login"

    @login.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.route("/login", methods=["GET", "POST"], endpoint="login")
    def login_route():
        if request.method == "GET":
            return render_template("v2/login.html")
        user = db.session.scalar(db.select(User).where(User.username == request.form.get("username")))
        if not user or not check_password_hash(user.password_hash, request.form.get("password", "")):
            return render_template("v2/login.html", error="Invalid credentials"), 401
        login_user(user); return redirect(url_for("v2.dashboard"))

    @app.post("/orders")
    @login_required
    def create_order_api():
        data = request.get_json(force=True)
        order_type = data.get("order_type", "SALES")
        if order_type == "SALES" and not data.get("planned_shipment_date"):
            return jsonify({"error": "Planned Shipment Date is required for Sales orders."}), 400
        customer = db.session.get(Customer, data["customer_id"])
        exporter = db.session.get(Exporter, data.get("exporter_id")) if data.get("exporter_id") else None
        factory = db.session.get(Factory, data.get("commission_factory_id")) if data.get("commission_factory_id") else None
        bank = db.session.get(BankAccount, data.get("bank_account_id")) if data.get("bank_account_id") else None
        pi = PI(pi_no=data["pi_no"], pi_date=date.fromisoformat(data["pi_date"]),
                order_type=order_type, status="NEW",
                customer_id=customer.id, exporter_id=exporter.id if exporter else None,
                commission_factory_id=factory.id if factory else None,
                customer_name_snapshot=customer.name, customer_address_snapshot=customer.address,
                customer_country_snapshot=customer.country, customer_email_snapshot=customer.email,
                exporter_name_snapshot=exporter.name if exporter else None,
                exporter_address_snapshot=exporter.address if exporter else None,
                currency=data["currency"], payment_terms=data.get("payment_terms"),
                planned_shipment_date=date.fromisoformat(data["planned_shipment_date"]) if data.get("planned_shipment_date") else None,
                advance_payment_percent=Decimal(str(data.get("advance_payment_percent", 0))),
                commission_rate=Decimal(str(data["commission_rate"])) if data.get("commission_rate") is not None else None,
                commission_currency=data.get("commission_currency"),
                commission_amount_mode=data.get("commission_amount_mode"),
                commission_amount=Decimal(str(data["commission_amount"])) if data.get("commission_amount") is not None else None,
                commission_override_reason=data.get("commission_override_reason"))
        apply_bank_snapshot(pi, bank)
        for row in data["items"]:
            product = db.session.get(Product, row["product_id"])
            quantity, price = Decimal(str(row["quantity"])), Decimal(str(row["unit_price"]))
            item = PIItem(product_id=product.id, unit_price=price, quantity=quantity,
                quantity_unit=row.get("quantity_unit", "MT"), line_total=(price * quantity).quantize(Decimal("0.01")))
            apply_product_snapshot(item, product)
            pi.items.append(item)
        db.session.add(pi); save_order_with_reconcile(pi)
        return jsonify({"id": pi.id, "pi_no": pi.pi_no}), 201

    from .web import blueprint
    app.register_blueprint(blueprint)
    return app


def create_configured_app():
    return create_app(os.environ.get("XHTPI_V2_DATABASE_URL"))


if __name__ == "__main__":
    create_configured_app().run(host="127.0.0.1", port=int(os.environ.get("XHTPI_V2_PORT", "5056")))
