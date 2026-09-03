"""Final clean V2 SQLAlchemy schema."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint


db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class PartyMixin(TimestampMixin):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.String(150), nullable=False)
    address = db.Column(db.Text)
    tax_code = db.Column(db.String(100))
    country = db.Column(db.String(80))
    contact_person = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(150))
    active = db.Column(db.Boolean, nullable=False, default=True)


class Customer(PartyMixin, db.Model):
    __tablename__ = "customer"


class Exporter(PartyMixin, db.Model):
    __tablename__ = "exporter"


class Factory(PartyMixin, db.Model):
    __tablename__ = "factory"


class FreightForwarder(PartyMixin, db.Model):
    __tablename__ = "freight_forwarder"


class Product(TimestampMixin, db.Model):
    __tablename__ = "product"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    category = db.Column(db.String(100))
    brand = db.Column(db.String(100))
    model = db.Column(db.String(100), nullable=False)
    packaging = db.Column(db.String(200))
    hs_code = db.Column(db.String(32))
    active = db.Column(db.Boolean, nullable=False, default=True)


class BankAccount(TimestampMixin, db.Model):
    __tablename__ = "bank_account"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    beneficiary_name = db.Column(db.String(200), nullable=False)
    bank_name = db.Column(db.String(200), nullable=False)
    bank_address = db.Column(db.Text)
    account_number = db.Column(db.String(100), nullable=False)
    swift_code = db.Column(db.String(50))
    currency = db.Column(db.String(10))
    remittance_information = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)


class FreightQuote(TimestampMixin, db.Model):
    __tablename__ = "freight_quote"
    id = db.Column(db.Integer, primary_key=True)
    freight_forwarder_id = db.Column(db.Integer, db.ForeignKey("freight_forwarder.id"), nullable=False)
    shipping_company = db.Column(db.String(150))
    departure_port = db.Column(db.String(120), nullable=False)
    destination_port = db.Column(db.String(120), nullable=False)
    route_type = db.Column(db.String(50))
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    quote_date = db.Column(db.Date)
    valid_until = db.Column(db.Date)
    note = db.Column(db.Text)
    freight_forwarder = db.relationship("FreightForwarder")


class PI(TimestampMixin, db.Model):
    __tablename__ = "pi"
    __table_args__ = (
        CheckConstraint("order_type IN ('SALES','COMMISSION')", name="ck_pi_order_type"),
        CheckConstraint("status IN ('NEW','PRE_SHIPMENT','SHIPPED','ARRIVED','COMPLETED')", name="ck_pi_status"),
        CheckConstraint("commission_amount_mode IS NULL OR commission_amount_mode IN ('DERIVED','EXPLICIT_OVERRIDE')", name="ck_pi_commission_mode"),
    )
    id = db.Column(db.Integer, primary_key=True)
    pi_no = db.Column(db.String(50), nullable=False, unique=True)
    order_type = db.Column(db.String(20), nullable=False, default="SALES")
    status = db.Column(db.String(20), nullable=False, default="NEW")
    pi_date = db.Column(db.Date, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    exporter_id = db.Column(db.Integer, db.ForeignKey("exporter.id"))
    commission_factory_id = db.Column(db.Integer, db.ForeignKey("factory.id"))

    customer_name_snapshot = db.Column(db.String(150), nullable=False)
    customer_address_snapshot = db.Column(db.Text)
    customer_tax_code_snapshot = db.Column(db.String(100))
    customer_country_snapshot = db.Column(db.String(80))
    customer_contact_snapshot = db.Column(db.String(100))
    customer_phone_snapshot = db.Column(db.String(50))
    customer_email_snapshot = db.Column(db.String(150))
    exporter_name_snapshot = db.Column(db.String(150))
    exporter_address_snapshot = db.Column(db.Text)
    exporter_tax_code_snapshot = db.Column(db.String(100))
    exporter_country_snapshot = db.Column(db.String(80))
    exporter_contact_snapshot = db.Column(db.String(100))
    exporter_phone_snapshot = db.Column(db.String(50))
    exporter_email_snapshot = db.Column(db.String(150))

    payment_terms = db.Column(db.String(200))
    currency = db.Column(db.String(10), nullable=False)
    advance_payment_percent = db.Column(db.Numeric(5, 2))
    advance_payment_amount = db.Column(db.Numeric(18, 2))
    advance_received_amount = db.Column(db.Numeric(18, 2))
    advance_received_at = db.Column(db.DateTime)
    balance_payment_amount = db.Column(db.Numeric(18, 2))
    balance_received_amount = db.Column(db.Numeric(18, 2))
    balance_received_at = db.Column(db.DateTime)

    loading_port = db.Column(db.String(100))
    destination_port = db.Column(db.String(100))
    planned_shipment_date = db.Column(db.Date)
    bank_account_id = db.Column(db.Integer, db.ForeignKey("bank_account.id"))
    bank_beneficiary_snapshot = db.Column(db.String(200))
    bank_name_snapshot = db.Column(db.String(200))
    bank_address_snapshot = db.Column(db.Text)
    bank_account_number_snapshot = db.Column(db.String(100))
    bank_swift_snapshot = db.Column(db.String(50))
    bank_currency_snapshot = db.Column(db.String(10))
    bank_remittance_snapshot = db.Column(db.Text)
    note = db.Column(db.Text)

    freight_forwarder_id = db.Column(db.Integer, db.ForeignKey("freight_forwarder.id"))
    container_loading_at = db.Column(db.DateTime)
    # Clean V2 facts: date + coarse period.  Legacy datetime remains read-only
    # compatibility for existing local-trial rows only.
    container_loading_date = db.Column(db.Date)
    container_loading_period = db.Column(db.String(12))
    container_location = db.Column(db.String(200))
    driver_name = db.Column(db.String(100))
    driver_phone = db.Column(db.String(50))
    vehicle_number = db.Column(db.String(50))
    etd = db.Column(db.Date)
    eta = db.Column(db.Date)
    actual_departure_date = db.Column(db.Date)
    actual_arrival_date = db.Column(db.Date)
    notify_party_same_as_consignee = db.Column(db.Boolean, default=True)
    notify_party_name_snapshot = db.Column(db.String(150))
    notify_party_address_snapshot = db.Column(db.Text)
    notify_party_tax_code_snapshot = db.Column(db.String(100))

    coo_required = db.Column(db.Boolean)
    apta_required = db.Column(db.Boolean)
    export_license_required = db.Column(db.Boolean)
    customs_docs_required = db.Column(db.Boolean)
    coc_required = db.Column(db.Boolean)
    coa_required = db.Column(db.Boolean)
    original_bl_required = db.Column(db.Boolean)
    obd_electronic_required = db.Column(db.Boolean)
    insurance_original_required = db.Column(db.Boolean)
    insurance_electronic_required = db.Column(db.Boolean)
    original_documents_mail_required = db.Column(db.Boolean)
    telex_release_required = db.Column(db.Boolean)
    settlement_documents_required = db.Column(db.Boolean)
    other_document_notes = db.Column(db.Text)

    bill_of_lading_number = db.Column(db.String(100))
    shipping_company = db.Column(db.String(100))
    vessel_info = db.Column(db.String(100))
    booking_number = db.Column(db.String(100))
    shipping_mark = db.Column(db.String(100))
    freight_term = db.Column(db.String(50))
    contract_number = db.Column(db.String(100))
    freight_clause = db.Column(db.Text)
    waybill_option = db.Column(db.String(30))
    container_type = db.Column(db.String(20))
    container_count = db.Column(db.Integer)
    container_number = db.Column(db.String(100))
    seal_number = db.Column(db.String(100))
    package_count = db.Column(db.Integer)
    package_unit = db.Column(db.String(20))
    gross_weight_kg = db.Column(db.Numeric(18, 3))
    gross_weight_display_unit = db.Column(db.String(10), default="KGS")
    volume_cbm = db.Column(db.Numeric(18, 3))
    vgm_kg = db.Column(db.Numeric(18, 3))
    vgm_display_unit = db.Column(db.String(10), default="KGS")

    commission_rate = db.Column(db.Numeric(7, 4))
    commission_amount = db.Column(db.Numeric(18, 2))
    commission_currency = db.Column(db.String(10))
    commission_amount_mode = db.Column(db.String(20))
    commission_override_reason = db.Column(db.Text)
    commission_status = db.Column(db.String(20))

    customer = db.relationship("Customer")
    exporter = db.relationship("Exporter")
    commission_factory = db.relationship("Factory", foreign_keys=[commission_factory_id])
    bank_account = db.relationship("BankAccount")
    freight_forwarder = db.relationship("FreightForwarder")
    items = db.relationship("PIItem", back_populates="pi", cascade="all, delete-orphan")

    @property
    def contract_total(self):
        return sum((item.line_total for item in self.items), Decimal("0.00"))

    def derive_commission(self):
        if self.order_type != "COMMISSION" or self.commission_rate is None:
            return None
        if self.commission_amount_mode == "EXPLICIT_OVERRIDE":
            if not self.commission_override_reason or self.commission_amount is None:
                raise ValueError("Commission override requires amount and reason.")
            return self.commission_amount
        self.commission_amount_mode = "DERIVED"
        self.commission_amount = (
            self.contract_total * Decimal(self.commission_rate) / Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self.commission_amount


class PIItem(TimestampMixin, db.Model):
    __tablename__ = "pi_item"
    id = db.Column(db.Integer, primary_key=True)
    pi_id = db.Column(db.Integer, db.ForeignKey("pi.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    factory_id = db.Column(db.Integer, db.ForeignKey("factory.id"))
    trade_term = db.Column(db.String(20))
    unit_price = db.Column(db.Numeric(18, 4), nullable=False)
    quantity = db.Column(db.Numeric(18, 3), nullable=False)
    quantity_unit = db.Column(db.String(20), nullable=False, default="MT")
    line_total = db.Column(db.Numeric(18, 2), nullable=False)
    product_category_snapshot = db.Column(db.String(100))
    product_brand_snapshot = db.Column(db.String(100))
    product_model_snapshot = db.Column(db.String(100))
    product_packaging_snapshot = db.Column(db.String(200))
    product_hs_code_snapshot = db.Column(db.String(32))
    factory_name_snapshot = db.Column(db.String(150))
    factory_address_snapshot = db.Column(db.Text)
    factory_tax_code_snapshot = db.Column(db.String(100))
    factory_country_snapshot = db.Column(db.String(80))
    factory_contact_snapshot = db.Column(db.String(100))
    factory_phone_snapshot = db.Column(db.String(50))
    factory_email_snapshot = db.Column(db.String(150))
    pi = db.relationship("PI", back_populates="items")
    batches = db.relationship("ProductBatch", back_populates="pi_item", cascade="all, delete-orphan")


class ProductBatch(TimestampMixin, db.Model):
    __tablename__ = "product_batch"
    __table_args__ = (UniqueConstraint("pi_item_id", "batch_number", name="uq_batch_item_number"),)
    id = db.Column(db.Integer, primary_key=True)
    pi_item_id = db.Column(db.Integer, db.ForeignKey("pi_item.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_number = db.Column(db.String(100), nullable=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    pi_item = db.relationship("PIItem", back_populates="batches")


class OrderFreightAgreement(TimestampMixin, db.Model):
    __tablename__ = "order_freight_agreement"
    id = db.Column(db.Integer, primary_key=True)
    pi_id = db.Column(db.Integer, db.ForeignKey("pi.id", ondelete="CASCADE"), nullable=False, unique=True)
    source_freight_quote_id = db.Column(db.Integer, db.ForeignKey("freight_quote.id"))
    freight_forwarder_id = db.Column(db.Integer, db.ForeignKey("freight_forwarder.id"))
    freight_forwarder_name_snapshot = db.Column(db.String(150), nullable=False)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    quote_date = db.Column(db.Date)
    agreed_at = db.Column(db.DateTime)
    note = db.Column(db.Text)
    pi = db.relationship("PI")


class FreightSettlement(TimestampMixin, db.Model):
    __tablename__ = "freight_settlement"
    id = db.Column(db.Integer, primary_key=True)
    pi_id = db.Column(db.Integer, db.ForeignKey("pi.id", ondelete="CASCADE"), nullable=False, unique=True)
    usd_bill_required = db.Column(db.Boolean)
    usd_bill_amount = db.Column(db.Numeric(18, 2))
    usd_bill_confirmed = db.Column(db.Boolean)
    cny_bill_required = db.Column(db.Boolean)
    cny_bill_amount = db.Column(db.Numeric(18, 2))
    cny_bill_confirmed = db.Column(db.Boolean)
    invoice_issued = db.Column(db.Boolean)
    invoice_issued_at = db.Column(db.DateTime)
    payment_status = db.Column(db.String(20))
    paid_at = db.Column(db.DateTime)
    pi = db.relationship("PI")


class OrderTask(TimestampMixin, db.Model):
    __tablename__ = "order_task"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_order_task_dedupe_key"),
        CheckConstraint("status IN ('UPCOMING','ACTION','WAITING','DONE','CANCELLED')", name="ck_task_status"),
        CheckConstraint("health IN ('NORMAL','OVERDUE','EXCEPTION')", name="ck_task_health"),
    )
    id = db.Column(db.Integer, primary_key=True)
    pi_id = db.Column(db.Integer, db.ForeignKey("pi.id", ondelete="CASCADE"), nullable=False, index=True)
    task_code = db.Column(db.String(80), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    source = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    health = db.Column(db.String(20), nullable=False, default="NORMAL")
    completion_mode = db.Column(db.String(40), nullable=False)
    context_payload = db.Column(db.JSON)
    priority = db.Column(db.Integer, nullable=False, default=100)
    activation_at = db.Column(db.DateTime)
    due_at = db.Column(db.DateTime)
    waiting_on = db.Column(db.String(40))
    waiting_since = db.Column(db.DateTime)
    next_follow_up_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    completed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    resolution_code = db.Column(db.String(50))
    dedupe_key = db.Column(db.String(255))
    pi = db.relationship("PI")
    activities = db.relationship("TaskActivity", back_populates="task", order_by="TaskActivity.created_at")


class TaskActivity(db.Model):
    __tablename__ = "task_activity"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("order_task.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = db.Column(db.String(40), nullable=False)
    from_status = db.Column(db.String(20))
    to_status = db.Column(db.String(20))
    actor_type = db.Column(db.String(20), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    note = db.Column(db.Text)
    payload = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    task = db.relationship("OrderTask", back_populates="activities")


class OrderCorrectionSession(db.Model):
    __tablename__ = "order_correction_session"
    __table_args__ = (
        CheckConstraint("module IN ('COMMERCIAL','PAYMENT','DOCUMENTS','SHIPPING','FREIGHT','ARRIVAL')", name="ck_correction_module"),
    )
    id = db.Column(db.Integer, primary_key=True)
    pi_id = db.Column(db.Integer, db.ForeignKey("pi.id", ondelete="CASCADE"), nullable=False, index=True)
    module = db.Column(db.String(30), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    opened_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    opened_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    closed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    closed_at = db.Column(db.DateTime)
    close_note = db.Column(db.Text)
