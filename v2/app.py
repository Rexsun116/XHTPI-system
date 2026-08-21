"""Minimal clean V2 Flask runtime used for isolated boot and baseline trials."""

from datetime import date
from decimal import Decimal

from flask import Flask, jsonify, request
from flask_login import LoginManager, current_user, login_required, login_user
from sqlalchemy import event
from werkzeug.security import check_password_hash

from .models import BankAccount, Customer, Exporter, Factory, PI, PIItem, Product, User, db
from .services import apply_bank_snapshot, save_order_with_reconcile


def create_app(database_uri, *, testing=False):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="v2-test-only-change-before-production",
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=testing,
    )
    db.init_app(app)
    with app.app_context():
        def _sqlite_fk_on(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        event.listen(db.engine, "connect", _sqlite_fk_on)
    login = LoginManager(app)
    login.login_view = "login"

    @login.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.route("/login", methods=["GET", "POST"])
    def login_route():
        if request.method == "GET":
            return "V2 Login", 200
        user = db.session.scalar(db.select(User).where(User.username == request.form.get("username")))
        if not user or not check_password_hash(user.password_hash, request.form.get("password", "")):
            return "Invalid credentials", 401
        login_user(user)
        return "OK", 200

    app.add_url_rule("/login", endpoint="login", view_func=login_route, methods=["GET", "POST"])

    @app.get("/")
    @login_required
    def dashboard():
        from .models import OrderTask
        counts = {
            status: db.session.scalar(db.select(db.func.count()).select_from(OrderTask).where(OrderTask.status == status))
            for status in ("ACTION", "WAITING", "UPCOMING", "DONE")
        }
        return jsonify(counts)

    @app.post("/orders")
    @login_required
    def create_order():
        data = request.get_json(force=True)
        customer = db.session.get(Customer, data["customer_id"])
        exporter = db.session.get(Exporter, data.get("exporter_id")) if data.get("exporter_id") else None
        commission_factory = db.session.get(Factory, data.get("commission_factory_id")) if data.get("commission_factory_id") else None
        bank = db.session.get(BankAccount, data.get("bank_account_id")) if data.get("bank_account_id") else None
        pi = PI(
            pi_no=data["pi_no"], pi_date=date.fromisoformat(data["pi_date"]),
            order_type=data.get("order_type", "SALES"), status="NEW",
            customer_id=customer.id, exporter_id=exporter.id if exporter else None,
            commission_factory_id=commission_factory.id if commission_factory else None,
            customer_name_snapshot=customer.name, customer_address_snapshot=customer.address,
            customer_country_snapshot=customer.country, customer_email_snapshot=customer.email,
            exporter_name_snapshot=exporter.name if exporter else None,
            exporter_address_snapshot=exporter.address if exporter else None,
            currency=data["currency"], payment_terms=data.get("payment_terms"),
            advance_payment_percent=Decimal(str(data.get("advance_payment_percent", 0))),
            commission_rate=Decimal(str(data["commission_rate"])) if data.get("commission_rate") is not None else None,
            commission_currency=data.get("commission_currency"),
            commission_amount_mode=data.get("commission_amount_mode"),
            commission_amount=Decimal(str(data["commission_amount"])) if data.get("commission_amount") is not None else None,
            commission_override_reason=data.get("commission_override_reason"),
        )
        apply_bank_snapshot(pi, bank)
        for row in data["items"]:
            product = db.session.get(Product, row["product_id"])
            quantity, price = Decimal(str(row["quantity"])), Decimal(str(row["unit_price"]))
            pi.items.append(PIItem(
                product_id=product.id, unit_price=price, quantity=quantity,
                quantity_unit=row.get("quantity_unit", "MT"),
                line_total=(price * quantity).quantize(Decimal("0.01")),
                product_model_snapshot=product.model,
                product_packaging_snapshot=product.packaging,
            ))
        db.session.add(pi)
        save_order_with_reconcile(pi)
        return jsonify({"id": pi.id, "pi_no": pi.pi_no}), 201

    return app
