"""Authenticated, CSRF-protected V2 browser UI."""
from datetime import date, datetime
from decimal import Decimal
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
import re
from order_lifecycle import LifecyclePolicyError, validate_lifecycle_submission
from .models import (BankAccount, Customer, Exporter, Factory, FreightForwarder,
    FreightQuote, FreightSettlement, OrderCorrectionSession, OrderFreightAgreement, OrderTask, PI, PIItem,
    Product, ProductBatch, TaskActivity, db, utcnow)
from .services import (apply_bank_snapshot, apply_product_snapshot, close_correction_session,
    apply_manual_task_business_fact, create_freight_agreement, open_correction_session,
    reconcile_order_tasks_for_pi, save_order_with_reconcile)
from .presenter import present_activity, present_task
from .selector import projected, select_next_action, sort_key
from .task_service import (TaskOperationError, cancel_manual, follow_up, mark_done,
                           move_to_waiting, parse_datetime, reopen)
from .documents import normalize_weight_input

blueprint = Blueprint("v2", __name__, url_prefix="/v2", static_folder="static")

MASTER = {
 "customers": (Customer, ["code","name","address","country","contact_person","phone","email"]),
 "exporters": (Exporter, ["code","name","address","country","contact_person","phone","email"]),
 "factories": (Factory, ["code","name","address","country","contact_person","phone","email"]),
 "forwarders": (FreightForwarder, ["code","name","address","country","contact_person","phone","email"]),
 "products": (Product, ["code","category","brand","model","packaging","hs_code"]),
 "banks": (BankAccount, ["code","name","beneficiary_name","bank_name","bank_address","account_number","swift_code","currency","remittance_information"]),
}


@blueprint.get("/")
@login_required
def dashboard():
    tasks = list(db.session.scalars(db.select(OrderTask)))
    orders = list(db.session.scalars(db.select(PI).order_by(PI.updated_at.desc())))
    ordered = sorted(tasks, key=sort_key)
    grouped = {key: [] for key in ("ACTION","WAITING","UPCOMING","DONE")}
    grouped["EXCEPTION"] = []
    for task in ordered:
        status, health = projected(task)
        task._dashboard_status, task._dashboard_health = status, health
        if health == "EXCEPTION" and status not in {"DONE", "CANCELLED"}:
            grouped["EXCEPTION"].append(task)
        if status in grouped:
            grouped[status].append(task)
    next_by_order = {pi.id: select_next_action([task for task in tasks if task.pi_id == pi.id]) for pi in orders}
    return render_template("v2/dashboard.html", grouped=grouped, orders=orders,
                           next_by_order=next_by_order, present_task=present_task)


@blueprint.route("/master/<kind>", methods=["GET", "POST"])
@login_required
def master_list(kind):
    if kind not in MASTER: abort(404)
    model, fields = MASTER[kind]
    if request.method == "POST":
        row = model()
        for field in fields: setattr(row, field, request.form.get(field) or None)
        db.session.add(row)
        try: db.session.commit()
        except IntegrityError:
            db.session.rollback(); flash("Code already exists or data is referenced.", "danger")
        return redirect(url_for("v2.master_list", kind=kind))
    rows = list(db.session.scalars(db.select(model).order_by(model.id)))
    return render_template("v2/master.html", kind=kind, rows=rows, fields=fields)


@blueprint.post("/master/<kind>/<int:row_id>/toggle")
@login_required
def master_toggle(kind, row_id):
    if kind not in MASTER: abort(404)
    model, _ = MASTER[kind]; row = db.get_or_404(model, row_id)
    row.active = not row.active; db.session.commit()
    return redirect(url_for("v2.master_list", kind=kind))


@blueprint.route("/master/<kind>/<int:row_id>/edit",methods=["GET","POST"])
@login_required
def master_edit(kind,row_id):
    if kind not in MASTER: abort(404)
    model,fields=MASTER[kind]; row=db.get_or_404(model,row_id)
    if request.method=="POST":
        for field in fields: setattr(row,field,request.form.get(field) or None)
        db.session.commit(); return redirect(url_for("v2.master_list",kind=kind))
    rows=list(db.session.scalars(db.select(model).order_by(model.id)))
    return render_template("v2/master.html",kind=kind,rows=rows,fields=fields,edit_row=row)


@blueprint.route("/quotes", methods=["GET", "POST"])
@login_required
def quotes():
    if request.method == "POST":
        q = FreightQuote(freight_forwarder_id=int(request.form["freight_forwarder_id"]),
            shipping_company=request.form.get("shipping_company"), departure_port=request.form["departure_port"],
            destination_port=request.form["destination_port"], route_type=request.form.get("route_type"),
            amount=Decimal(request.form["amount"]), currency=request.form["currency"].upper(),
            quote_date=date.fromisoformat(request.form["quote_date"]) if request.form.get("quote_date") else None,
            valid_until=date.fromisoformat(request.form["valid_until"]) if request.form.get("valid_until") else None,
            note=request.form.get("note"))
        db.session.add(q); db.session.commit(); return redirect(url_for("v2.quotes"))
    return render_template("v2/quotes.html", quotes=list(db.session.scalars(db.select(FreightQuote))),
        forwarders=list(db.session.scalars(db.select(FreightForwarder).where(FreightForwarder.active.is_(True)))))


@blueprint.route("/quotes/<int:quote_id>/edit", methods=["GET", "POST"])
@login_required
def quote_edit(quote_id):
    quote = db.get_or_404(FreightQuote, quote_id)
    if request.method == "POST":
        quote.freight_forwarder_id = int(request.form["freight_forwarder_id"])
        for field in ("shipping_company", "departure_port", "destination_port", "route_type", "currency", "note"):
            setattr(quote, field, request.form.get(field) or None)
        quote.currency = quote.currency.upper()
        quote.amount = Decimal(request.form["amount"])
        quote.quote_date = date.fromisoformat(request.form["quote_date"]) if request.form.get("quote_date") else None
        quote.valid_until = date.fromisoformat(request.form["valid_until"]) if request.form.get("valid_until") else None
        db.session.commit()
        return redirect(url_for("v2.quotes"))
    return render_template("v2/quotes.html", quotes=list(db.session.scalars(db.select(FreightQuote))),
        edit_quote=quote, forwarders=list(db.session.scalars(db.select(FreightForwarder).where(FreightForwarder.active.is_(True)))))


def _order_choices():
    return dict(customers=list(db.session.scalars(db.select(Customer).where(Customer.active.is_(True)))),
        exporters=list(db.session.scalars(db.select(Exporter).where(Exporter.active.is_(True)))),
        factories=list(db.session.scalars(db.select(Factory).where(Factory.active.is_(True)))),
        products=list(db.session.scalars(db.select(Product).where(Product.active.is_(True)))),
        banks=list(db.session.scalars(db.select(BankAccount).where(BankAccount.active.is_(True)))),
        forwarders=list(db.session.scalars(db.select(FreightForwarder).where(FreightForwarder.active.is_(True)))))


DOCUMENT_FACTS = ("coo_required", "apta_required", "export_license_required", "customs_docs_required",
                  "coc_required", "coa_required", "original_bl_required", "obd_electronic_required",
                  "insurance_original_required", "insurance_electronic_required",
                  "original_documents_mail_required", "telex_release_required", "settlement_documents_required")


def _tri_state(value):
    return None if value in (None, "") else value == "true"


def _reject_unexpected_fields(allowed):
    submitted = set(request.form) - {"csrf_token"}
    unexpected = submitted - set(allowed)
    if unexpected:
        abort(400, "Fields are not editable in this lifecycle/module: " + ", ".join(sorted(unexpected)))


def _render_create_form(*, form_data=None, error=None, status=200):
    return render_template("v2/order_form.html", pi=None, form_data=form_data or {}, error=error,
                           **_order_choices()), status


def _required_create_value(name, label):
    value = (request.form.get(name) or "").strip()
    if not value:
        raise ValueError(f"{label} is required.")
    return value


def _create_item_indexes():
    indexes = []
    for key in request.form:
        match = re.fullmatch(r"product_(\d+)", key)
        if match and request.form.get(key):
            indexes.append(int(match.group(1)))
    return sorted(set(indexes))


@blueprint.post("/check-pi-number")
@login_required
def check_pi_number():
    """CSRF-protected, authenticated, read-only V2 PI number availability check."""
    pi_no = (request.form.get("pi_no") or "").strip()
    available = bool(pi_no) and db.session.scalar(
        db.select(PI.id).where(PI.pi_no == pi_no)
    ) is None
    return jsonify({"available": available})


@blueprint.route("/orders/new", methods=["GET", "POST"])
@login_required
def order_new():
    if request.method == "GET":
        return _render_create_form()
    try:
        pi_no = _required_create_value("pi_no", "PI Number")
        pi_date = date.fromisoformat(_required_create_value("pi_date", "PI Date"))
        payment_terms = _required_create_value("payment_terms", "Payment Terms")
        customer_id = int(_required_create_value("customer_id", "Customer"))
        exporter_id = int(_required_create_value("exporter_id", "Exporter"))
        currency = _required_create_value("currency", "Currency").upper()
        order_type = request.form.get("order_type", "SALES")
        if order_type not in {"SALES", "COMMISSION"}:
            raise ValueError("Order Type is invalid.")
        planned_shipment_raw = (request.form.get("planned_shipment_date") or "").strip()
        if order_type == "SALES" and not planned_shipment_raw:
            raise ValueError("Planned Shipment Date is required for Sales orders.")
        if db.session.scalar(db.select(PI.id).where(PI.pi_no == pi_no)) is not None:
            raise ValueError("PI Number already exists.")
        customer = db.session.get(Customer, customer_id)
        exporter = db.session.get(Exporter, exporter_id)
        if customer is None or exporter is None:
            raise ValueError("Customer or Exporter is unavailable.")
        advance_percent = Decimal(request.form.get("advance_payment_percent") or "0")
        if advance_percent < 0 or advance_percent > 100:
            raise ValueError("Advance % must be between 0 and 100.")
        pi = PI(pi_no=pi_no, pi_date=pi_date, order_type=order_type, status="NEW",
            customer_id=customer.id, exporter_id=exporter.id,
            commission_factory_id=int(request.form["commission_factory_id"]) if order_type == "COMMISSION" and request.form.get("commission_factory_id") else None,
            customer_name_snapshot=customer.name, customer_address_snapshot=customer.address,
            customer_country_snapshot=customer.country, customer_email_snapshot=customer.email,
            exporter_name_snapshot=exporter.name, exporter_address_snapshot=exporter.address,
            currency=currency, payment_terms=payment_terms, advance_payment_percent=advance_percent,
            loading_port=request.form.get("loading_port"), destination_port=request.form.get("destination_port"),
            planned_shipment_date=date.fromisoformat(planned_shipment_raw) if planned_shipment_raw else None,
            note=request.form.get("note"), other_document_notes=request.form.get("other_document_notes"))
        if order_type == "COMMISSION":
            pi.commission_rate = Decimal(request.form["commission_rate"]) if request.form.get("commission_rate") else None
            pi.commission_currency = request.form.get("commission_currency") or currency
            pi.commission_amount_mode = request.form.get("commission_amount_mode") or "DERIVED"
            pi.commission_amount = Decimal(request.form["commission_amount"]) if request.form.get("commission_amount") else None
            pi.commission_override_reason = request.form.get("commission_override_reason")
        for field in DOCUMENT_FACTS:
            setattr(pi, field, _tri_state(request.form.get(field)))
        bank = db.session.get(BankAccount, int(request.form["bank_account_id"])) if request.form.get("bank_account_id") else None
        apply_bank_snapshot(pi, bank)
        for index in _create_item_indexes():
            product = db.session.get(Product, int(request.form[f"product_{index}"]))
            if product is None:
                raise ValueError("A selected Product is unavailable.")
            price, qty = Decimal(request.form[f"unit_price_{index}"]), Decimal(request.form[f"quantity_{index}"])
            if price < 0 or qty <= 0:
                raise ValueError("PI Item price must be non-negative and quantity must be positive.")
            item = PIItem(product_id=product.id,
                factory_id=int(request.form[f"factory_{index}"]) if request.form.get(f"factory_{index}") else None,
                trade_term=request.form.get(f"trade_term_{index}"), unit_price=price, quantity=qty,
                quantity_unit=request.form.get(f"quantity_unit_{index}") or "MT",
                line_total=(price * qty).quantize(Decimal("0.01")))
            apply_product_snapshot(item, product)
            pi.items.append(item)
        if not pi.items:
            raise ValueError("At least one PI item is required.")
        db.session.add(pi)
        save_order_with_reconcile(pi)
    except (ValueError, ArithmeticError, IntegrityError) as exc:
        db.session.rollback()
        message = "PI Number already exists." if isinstance(exc, IntegrityError) else str(exc)
        return _render_create_form(form_data=request.form, error=message, status=400)
    return redirect(url_for("v2.order_view", pi_id=pi.id))


@blueprint.post("/orders/<int:pi_id>/advance-receipt")
@login_required
def advance_receipt(pi_id):
    """Record the structured fact which can auto-resolve an advance task."""
    pi = db.get_or_404(PI, pi_id)
    if pi.order_type != "SALES":
        abort(400, "Advance receipts are available only for Sales orders.")
    try:
        amount = Decimal((request.form.get("advance_received_amount") or "").strip())
        received_at = parse_datetime(request.form.get("advance_received_at"))
        if amount < 0 or received_at is None:
            raise ValueError("Advance received amount and date/time are required.")
        pi.advance_received_amount = amount
        pi.advance_received_at = received_at
        save_order_with_reconcile(pi)
    except (ValueError, ArithmeticError) as exc:
        db.session.rollback()
        abort(400, str(exc))
    return redirect(url_for("v2.order_view", pi_id=pi.id))


@blueprint.get("/orders/<int:pi_id>")
@login_required
def order_view(pi_id):
    pi=db.get_or_404(PI,pi_id); tasks=list(db.session.scalars(db.select(OrderTask).where(OrderTask.pi_id==pi.id)))
    correction=db.session.scalar(db.select(OrderCorrectionSession).where(OrderCorrectionSession.pi_id==pi.id,OrderCorrectionSession.closed_at.is_(None)))
    quotes=list(db.session.scalars(db.select(FreightQuote)))
    agreement=db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id==pi.id))
    settlement=db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id==pi.id))
    open_task = request.args.get("open_task", type=int)
    return render_template("v2/order_view.html",pi=pi,tasks=tasks,correction=correction,quotes=quotes,open_task=open_task,
        agreement=agreement,settlement=settlement,forwarders=list(db.session.scalars(db.select(FreightForwarder))),
        present_task=present_task,present_activity=present_activity,document_facts=DOCUMENT_FACTS)


@blueprint.get("/tasks/<int:task_id>/history")
@login_required
def task_history(task_id):
    """A no-JavaScript history page for one exact task."""
    task = db.get_or_404(OrderTask, task_id)
    activities = list(db.session.scalars(db.select(TaskActivity).where(
        TaskActivity.task_id == task.id
    ).order_by(TaskActivity.created_at.desc(), TaskActivity.id.desc())))
    return render_template("v2/task_history.html", task=task, activities=activities,
                           present_activity=present_activity)


@blueprint.post("/orders/<int:pi_id>/facts")
@login_required
def order_facts(pi_id):
    pi=db.get_or_404(PI,pi_id)
    if pi.status=="COMPLETED":
        correction=db.session.scalar(db.select(OrderCorrectionSession).where(OrderCorrectionSession.pi_id==pi.id,OrderCorrectionSession.closed_at.is_(None)))
        if not correction: abort(403,"Use a correction session")
        correction_fields = {
            "COMMERCIAL": {"payment_terms","note","loading_port","destination_port","shipping_mark","freight_term","contract_number","freight_clause","waybill_option"},
            "PAYMENT": {"advance_payment_percent","advance_payment_amount","balance_payment_amount","advance_received_amount","advance_received_at","balance_received_amount","balance_received_at"},
            "DOCUMENTS": set(DOCUMENT_FACTS),
            "SHIPPING": {"container_type","container_location","container_loading_date","container_loading_period","driver_name","driver_phone","vehicle_number","etd","eta","actual_departure_date"},
            "FREIGHT": {"usd_bill_required","cny_bill_required","usd_bill_amount","cny_bill_amount","usd_bill_confirmed","cny_bill_confirmed","invoice_issued","payment_status","paid_at","agreement_amount","agreement_currency","agreement_note"},
            "ARRIVAL": {"actual_arrival_date"},
        }
        _reject_unexpected_fields(correction_fields[correction.module])
        if correction.module=="COMMERCIAL":
            for f in ("payment_terms","note","loading_port","destination_port","shipping_mark","freight_term","contract_number","freight_clause","waybill_option"):
                if f in request.form: setattr(pi,f,request.form.get(f) or None)
        elif correction.module=="PAYMENT":
            for f in ("advance_payment_percent","advance_payment_amount","balance_payment_amount","advance_received_amount","balance_received_amount"):
                if f in request.form: setattr(pi,f,Decimal(request.form[f])) if request.form[f] else setattr(pi,f,None)
            for f in ("advance_received_at","balance_received_at"):
                if f in request.form: setattr(pi,f,datetime.fromisoformat(request.form[f])) if request.form[f] else setattr(pi,f,None)
        elif correction.module=="SHIPPING":
            for f in ("container_type","container_location","driver_name","driver_phone","vehicle_number","vessel_info","booking_number"):
                if f in request.form: setattr(pi,f,request.form.get(f) or None)
            if "container_loading_date" in request.form:
                pi.container_loading_date = date.fromisoformat(request.form["container_loading_date"]) if request.form["container_loading_date"] else None
            if "container_loading_period" in request.form:
                period = request.form["container_loading_period"] or None
                if period not in {None, "AM", "PM", "UNKNOWN"}: abort(400, "Container loading period is invalid.")
                pi.container_loading_period = period
            for f in ("etd","eta","actual_departure_date"):
                if f in request.form: setattr(pi,f,date.fromisoformat(request.form[f])) if request.form[f] else setattr(pi,f,None)
        elif correction.module=="ARRIVAL" and "actual_arrival_date" in request.form: pi.actual_arrival_date=date.fromisoformat(request.form["actual_arrival_date"]) if request.form["actual_arrival_date"] else None
        elif correction.module=="DOCUMENTS":
            for f in DOCUMENT_FACTS:
                if f in request.form: setattr(pi,f,_tri_state(request.form.get(f)))
        elif correction.module=="FREIGHT":
            settlement=db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id==pi.id))
            if not settlement:
                settlement=FreightSettlement(pi_id=pi.id); db.session.add(settlement)
            for f in ("usd_bill_required","cny_bill_required","usd_bill_confirmed","cny_bill_confirmed","invoice_issued"):
                if f in request.form: setattr(settlement,f,_tri_state(request.form.get(f)))
            for f in ("usd_bill_amount","cny_bill_amount"):
                if f in request.form: setattr(settlement,f,Decimal(request.form[f])) if request.form[f] else setattr(settlement,f,None)
            if "payment_status" in request.form: settlement.payment_status=request.form.get("payment_status") or None
            if "paid_at" in request.form: settlement.paid_at=datetime.fromisoformat(request.form["paid_at"]) if request.form["paid_at"] else None
            agreement=db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id==pi.id))
            if agreement and (request.form.get("agreement_amount") or request.form.get("agreement_currency")):
                old=f"{agreement.currency} {agreement.amount}"
                agreement.amount=Decimal(request.form.get("agreement_amount") or agreement.amount)
                agreement.currency=(request.form.get("agreement_currency") or agreement.currency).upper()
                agreement.note="\n".join(filter(None,[agreement.note,
                    f"Correction {utcnow().isoformat()}: {old} -> {agreement.currency} {agreement.amount}. {request.form.get('agreement_note') or correction.reason}"]))
        save_order_with_reconcile(pi); return redirect(url_for("v2.order_view",pi_id=pi.id))
    try:
        validate_lifecycle_submission(pi, request.form)
    except LifecyclePolicyError as exc:
        abort(400, str(exc))
    if pi.status == "PRE_SHIPMENT" and any(field in request.form for field in DOCUMENT_FACTS):
        abort(400, "Use the dedicated Document Requirements editor.")
    if pi.status in {"SHIPPED", "ARRIVED"} and any(
        field in request.form for field in ("usd_bill_confirmed", "cny_bill_confirmed", "invoice_issued")
    ):
        abort(400, "Freight amount/invoice confirmation must use the corresponding Task action.")
    if pi.status=="NEW":
        for f in ("payment_terms","currency","loading_port","destination_port","note","other_document_notes"):
            if f in request.form: setattr(pi,f,request.form.get(f) or None)
        for f in ("advance_payment_percent","advance_payment_amount","balance_payment_amount"):
            if f in request.form: setattr(pi,f,Decimal(request.form[f])) if request.form[f] else setattr(pi,f,None)
        if "planned_shipment_date" in request.form:
            pi.planned_shipment_date=date.fromisoformat(request.form["planned_shipment_date"]) if request.form["planned_shipment_date"] else None
        for f in DOCUMENT_FACTS:
            if f in request.form: setattr(pi,f,_tri_state(request.form.get(f)))
    elif pi.status=="PRE_SHIPMENT":
        # Several distinct forms submit to this endpoint.  Patch only the
        # fields owned by the submitting form: absent is not an instruction to
        # clear a fact from a different PRE_SHIPMENT module.
        if "container_type" in request.form:
            pi.container_type = request.form.get("container_type") or None
        if "container_count" in request.form:
            pi.container_count = int(request.form["container_count"]) if request.form.get("container_count") else None
        if "container_loading_date" in request.form:
            pi.container_loading_date = date.fromisoformat(request.form["container_loading_date"]) if request.form.get("container_loading_date") else None
        if "container_loading_period" in request.form:
            period = request.form.get("container_loading_period") or None
            if period not in {None, "AM", "PM", "UNKNOWN"}: abort(400, "Container loading period is invalid.")
            pi.container_loading_period = period
        for f in ("container_location","driver_name","driver_phone","vehicle_number","vessel_info","booking_number",
                  "shipping_mark","freight_term","contract_number","freight_clause","waybill_option"):
            if f in request.form: setattr(pi,f,request.form.get(f) or None)
        if "package_count" in request.form:
            pi.package_count = int(request.form["package_count"]) if request.form["package_count"] else None
        if "package_unit" in request.form:
            pi.package_unit = request.form.get("package_unit") or "BAGS"
        elif "package_count" in request.form:
            # Do not rely on the visual input default for a normal cargo save.
            pi.package_unit = "BAGS"
        if "gross_weight" in request.form or "gross_weight_display_unit" in request.form:
            unit = (request.form.get("gross_weight_display_unit") or pi.gross_weight_display_unit or "KGS").upper()
            if unit not in {"KGS", "MT"}:
                abort(400, "Gross weight display unit must be KGS or MT.")
            pi.gross_weight_display_unit = unit
            if "gross_weight" in request.form:
                pi.gross_weight_kg = normalize_weight_input(request.form.get("gross_weight"), unit)
        if "volume" in request.form:
            pi.volume_cbm = Decimal(request.form["volume"]) if request.form["volume"] else None
        if "freight_forwarder_id" in request.form:
            pi.freight_forwarder_id=int(request.form["freight_forwarder_id"]) if request.form.get("freight_forwarder_id") else None
        # The PRE_SHIPMENT preparation form intentionally does not submit
        # ETD/ETA.  Do not turn omitted fields into destructive NULL updates;
        # only an explicit, lifecycle-authorized field may change them.
        if "etd" in request.form:
            pi.etd = date.fromisoformat(request.form["etd"]) if request.form["etd"] else None
        if "eta" in request.form:
            pi.eta = date.fromisoformat(request.form["eta"]) if request.form["eta"] else None
        if "usd_bill_required" in request.form or "cny_bill_required" in request.form:
            settlement=db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id==pi.id)) or FreightSettlement(pi_id=pi.id)
            if "usd_bill_required" in request.form:
                settlement.usd_bill_required = _tri_state(request.form.get("usd_bill_required"))
            if "cny_bill_required" in request.form:
                settlement.cny_bill_required = _tri_state(request.form.get("cny_bill_required"))
            db.session.add(settlement)
        if "notify_party_same_as_consignee" in request.form:
            same_notify = request.form.get("notify_party_same_as_consignee") == "true"
            pi.notify_party_same_as_consignee = same_notify
            if not same_notify:
                for field in (
                    "notify_party_name_snapshot", "notify_party_address_snapshot",
                    "notify_party_tax_code_snapshot",
                ):
                    if field in request.form:
                        setattr(pi, field, request.form.get(field) or None)
        for item in pi.items:
            field = f"product_hs_code_snapshot_{item.id}"
            if field in request.form:
                item.product_hs_code_snapshot = request.form.get(field) or None
        if request.form.get("quote_id") and not db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id==pi.id)):
            db.session.add(create_freight_agreement(pi,db.session.get(FreightQuote,int(request.form["quote_id"]))))
        elif request.form.get("agreement_amount") and not db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id==pi.id)):
            forwarder=db.session.get(FreightForwarder,pi.freight_forwarder_id) if pi.freight_forwarder_id else None
            db.session.add(OrderFreightAgreement(pi_id=pi.id,freight_forwarder_id=pi.freight_forwarder_id,
                freight_forwarder_name_snapshot=forwarder.name if forwarder else "Freight forwarder not confirmed",
                amount=Decimal(request.form["agreement_amount"]),currency=request.form["agreement_currency"].upper(),
                agreed_at=utcnow(),note=request.form.get("agreement_note")))
    elif pi.status in {"SHIPPED","ARRIVED"}:
        pi.actual_departure_date=date.fromisoformat(request.form["actual_departure_date"]) if request.form.get("actual_departure_date") else pi.actual_departure_date
        pi.actual_arrival_date=date.fromisoformat(request.form["actual_arrival_date"]) if request.form.get("actual_arrival_date") else pi.actual_arrival_date
        for f in ("advance_received_amount","balance_received_amount"):
            if request.form.get(f): setattr(pi,f,Decimal(request.form[f]))
        for f in ("advance_received_at","balance_received_at"):
            if request.form.get(f): setattr(pi,f,datetime.fromisoformat(request.form[f]))
        for f in ("bill_of_lading_number","shipping_company","booking_number","shipping_mark"):
            if f in request.form: setattr(pi,f,request.form.get(f) or None)
        for f in ("container_number", "seal_number"):
            if f in request.form: setattr(pi, f, request.form.get(f) or None)
        if "vgm" in request.form or "vgm_display_unit" in request.form:
            unit = (request.form.get("vgm_display_unit") or pi.vgm_display_unit or "KGS").upper()
            if unit not in {"KGS", "MT"}:
                abort(400, "VGM display unit must be KGS or MT.")
            pi.vgm_display_unit = unit
            if "vgm" in request.form:
                pi.vgm_kg = normalize_weight_input(request.form.get("vgm"), unit)
        settlement=db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id==pi.id)) or FreightSettlement(pi_id=pi.id)
        if request.form.get("usd_bill_amount"): settlement.usd_bill_amount=Decimal(request.form["usd_bill_amount"])
        if request.form.get("cny_bill_amount"): settlement.cny_bill_amount=Decimal(request.form["cny_bill_amount"])
        if "usd_bill_confirmed" in request.form: settlement.usd_bill_confirmed=_tri_state(request.form.get("usd_bill_confirmed"))
        if "cny_bill_confirmed" in request.form: settlement.cny_bill_confirmed=_tri_state(request.form.get("cny_bill_confirmed"))
        if "invoice_issued" in request.form: settlement.invoice_issued=_tri_state(request.form.get("invoice_issued"))
        settlement.payment_status=request.form.get("payment_status") or settlement.payment_status
        settlement.paid_at=datetime.fromisoformat(request.form["paid_at"]) if request.form.get("paid_at") else settlement.paid_at; db.session.add(settlement)
    save_order_with_reconcile(pi); return redirect(url_for("v2.order_view",pi_id=pi.id))


@blueprint.route("/orders/<int:pi_id>/document-requirements", methods=["GET", "POST"])
@login_required
def document_requirements_editor(pi_id):
    pi = db.get_or_404(PI, pi_id)
    if pi.status != "PRE_SHIPMENT":
        abort(403, "Document requirements can be revised from PRE_SHIPMENT only.")
    if request.method == "POST":
        for fact in DOCUMENT_FACTS:
            if fact in request.form:
                setattr(pi, fact, _tri_state(request.form.get(fact)))
        save_order_with_reconcile(pi)
        return redirect(url_for("v2.order_view", pi_id=pi.id))
    return render_template("v2/document_requirements_editor.html", pi=pi, document_facts=DOCUMENT_FACTS)


@blueprint.route("/orders/<int:pi_id>/booking-hs-codes", methods=["GET", "POST"])
@login_required
def booking_hs_codes(pi_id):
    pi = db.get_or_404(PI, pi_id)
    if pi.status != "PRE_SHIPMENT":
        abort(403, "HS Code snapshots are editable from PRE_SHIPMENT only.")
    if request.method == "POST":
        for item in pi.items:
            field = f"product_hs_code_snapshot_{item.id}"
            if field in request.form:
                item.product_hs_code_snapshot = request.form.get(field) or None
        save_order_with_reconcile(pi)
        return redirect(url_for("v2.order_view", pi_id=pi.id))
    return render_template("v2/booking_hs_codes.html", pi=pi)


@blueprint.post("/orders/<int:pi_id>/status")
@login_required
def order_status(pi_id):
    pi=db.get_or_404(PI,pi_id); target=request.form["status"]
    allowed={"NEW":"PRE_SHIPMENT","PRE_SHIPMENT":"SHIPPED","SHIPPED":"ARRIVED","ARRIVED":"COMPLETED"}
    if allowed.get(pi.status)!=target: abort(400,"Invalid lifecycle transition")
    if pi.status == "NEW" and target == "PRE_SHIPMENT":
        gate = db.session.scalar(db.select(OrderTask).where(
            OrderTask.pi_id == pi.id,
            OrderTask.task_code == "STAGE_GATE_PRE_SHIPMENT",
            OrderTask.status == "ACTION",
        ))
        if gate is None:
            abort(409, "Pre-shipment stage gate is not available.")
    if pi.status == "PRE_SHIPMENT" and target == "SHIPPED":
        abort(409, "Use the controlled Enter Shipped flow with Actual Departure Date.")
    pi.status=target; save_order_with_reconcile(pi); return redirect(url_for("v2.order_view",pi_id=pi.id))


@blueprint.post("/orders/<int:pi_id>/enter-pre-shipment")
@login_required
def enter_pre_shipment(pi_id):
    pi = db.get_or_404(PI, pi_id)
    gate = db.session.scalar(db.select(OrderTask).where(
        OrderTask.pi_id == pi.id, OrderTask.task_code == "STAGE_GATE_PRE_SHIPMENT",
        OrderTask.status == "ACTION",
    ))
    if pi.status != "NEW" or gate is None:
        abort(409, "Pre-shipment stage gate is not available.")
    try:
        pi.status = "PRE_SHIPMENT"
        save_order_with_reconcile(pi)
    except Exception:
        db.session.rollback()
        raise
    return redirect(url_for("v2.order_view", pi_id=pi.id))


def _shipped_gate_is_ready(pi):
    gate = db.session.scalar(db.select(OrderTask).where(
        OrderTask.pi_id == pi.id, OrderTask.task_code == "STAGE_GATE_SHIPPED",
        OrderTask.status == "ACTION",
    ))
    loading = db.session.scalar(db.select(OrderTask).where(
        OrderTask.pi_id == pi.id, OrderTask.task_code == "SHIPPING_CONTAINER_LOADING",
        OrderTask.status == "DONE",
    ))
    agreement_task = db.session.scalar(db.select(OrderTask).where(
        OrderTask.pi_id == pi.id, OrderTask.task_code == "SHIPPING_FREIGHT_AGREEMENT",
        OrderTask.status == "DONE",
    ))
    agreement = db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id == pi.id))
    loading_date = pi.container_loading_date or (pi.container_loading_at.date() if pi.container_loading_at else None)
    return bool(gate and loading and agreement_task and loading_date and agreement)


@blueprint.route("/orders/<int:pi_id>/enter-shipped", methods=["GET", "POST"])
@login_required
def enter_shipped(pi_id):
    pi = db.get_or_404(PI, pi_id)
    if pi.status != "PRE_SHIPMENT" or not _shipped_gate_is_ready(pi):
        abort(409, "Shipment stage gate is not ready.")
    if request.method == "GET":
        return render_template("v2/enter_shipped.html", pi=pi)
    raw = (request.form.get("actual_departure_date") or "").strip()
    if not raw:
        abort(400, "Actual Departure Date is required.")
    try:
        pi.actual_departure_date = date.fromisoformat(raw)
        pi.status = "SHIPPED"
        save_order_with_reconcile(pi)
    except (ValueError, ArithmeticError) as exc:
        db.session.rollback()
        abort(400, str(exc))
    return redirect(url_for("v2.order_view", pi_id=pi.id))


@blueprint.post("/orders/<int:pi_id>/freight-agreement")
@login_required
def freight_agreement_manual(pi_id):
    pi=db.get_or_404(PI,pi_id)
    if pi.status!="PRE_SHIPMENT": abort(403)
    if db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id==pi.id)):
        abort(409,"Accepted agreement is immutable; use a controlled correction.")
    forwarder=db.session.get(FreightForwarder,pi.freight_forwarder_id) if pi.freight_forwarder_id else None
    if not request.form.get("amount") or not request.form.get("currency"): abort(400)
    db.session.add(OrderFreightAgreement(pi_id=pi.id,freight_forwarder_id=pi.freight_forwarder_id,
        freight_forwarder_name_snapshot=forwarder.name if forwarder else "Freight forwarder not confirmed",
        amount=Decimal(request.form["amount"]),currency=request.form["currency"].upper(),agreed_at=utcnow(),
        note=request.form.get("note")))
    save_order_with_reconcile(pi)
    return redirect(url_for("v2.order_view",pi_id=pi.id))


@blueprint.post("/orders/<int:pi_id>/batches")
@login_required
def batches(pi_id):
    pi=db.get_or_404(PI,pi_id)
    if pi.status not in {"SHIPPED","ARRIVED"}: abort(403)
    item=db.session.get(PIItem,int(request.form["pi_item_id"]));
    if not item or item.pi_id!=pi.id: abort(404)
    item.batches.append(ProductBatch(batch_number=request.form["batch_number"].strip(),display_order=len(item.batches)))
    try: save_order_with_reconcile(pi)
    except IntegrityError: db.session.rollback(); flash("Duplicate batch number", "danger")
    return redirect(url_for("v2.order_view",pi_id=pi.id))


@blueprint.post("/orders/<int:pi_id>/corrections")
@login_required
def correction_open(pi_id):
    pi=db.get_or_404(PI,pi_id); open_correction_session(pi,request.form["module"],request.form.get("reason"),current_user.id); db.session.commit()
    return redirect(url_for("v2.order_view",pi_id=pi.id))


@blueprint.post("/corrections/<int:session_id>/close")
@login_required
def correction_close(session_id):
    session=db.get_or_404(OrderCorrectionSession,session_id); pi_id=session.pi_id
    close_correction_session(session,current_user.id,note=request.form.get("note")); return redirect(url_for("v2.order_view",pi_id=pi_id))


@blueprint.route("/corrections/<int:session_id>/edit", methods=["GET", "POST"])
@login_required
def correction_edit(session_id):
    session=db.get_or_404(OrderCorrectionSession,session_id)
    if session.closed_at: abort(409,"Correction session is closed")
    pi=db.get_or_404(PI,session.pi_id); settlement=db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id==pi.id))
    agreement=db.session.scalar(db.select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id==pi.id))
    if request.method=="GET":
        return render_template("v2/correction.html",pi=pi,correction=session,settlement=settlement,agreement=agreement,
                               document_facts=DOCUMENT_FACTS,**_order_choices())
    correction_allowed={
        "COMMERCIAL":{"pi_no","pi_date","payment_terms","loading_port","destination_port","note","customer_id","exporter_id","bank_account_id","commission_rate","commission_currency","commission_amount_mode","commission_amount","commission_override_reason"}|{f"item_{item.id}_{field}" for item in pi.items for field in ("quantity","quantity_unit","unit_price","trade_term")},
        "PAYMENT":{"advance_payment_percent","advance_payment_amount","balance_payment_amount","advance_received_amount","advance_received_at","balance_received_amount","balance_received_at"},
        "DOCUMENTS":set(DOCUMENT_FACTS)|{"other_document_notes"},
        "SHIPPING":{"container_type","container_count","container_loading_at","container_location","driver_name","driver_phone","vehicle_number","etd","eta","actual_departure_date","vessel_info","booking_number","shipping_mark","freight_term","contract_number","freight_clause","waybill_option","container_number","seal_number","package_unit"},
        "FREIGHT":{"usd_bill_required","cny_bill_required","usd_bill_amount","cny_bill_amount","payment_status","paid_at","agreement_amount","agreement_currency"},
        "ARRIVAL":{"actual_arrival_date"},
    }
    _reject_unexpected_fields(correction_allowed[session.module])
    if session.module=="COMMERCIAL":
        customer=db.session.get(Customer,int(request.form["customer_id"])); exporter=db.session.get(Exporter,int(request.form["exporter_id"])) if request.form.get("exporter_id") else None
        pi.customer_id=customer.id; pi.customer_name_snapshot=customer.name; pi.customer_address_snapshot=customer.address; pi.customer_tax_code_snapshot=customer.tax_code
        pi.exporter_id=exporter.id if exporter else None; pi.exporter_name_snapshot=exporter.name if exporter else None; pi.exporter_address_snapshot=exporter.address if exporter else None
        for f in ("pi_no","payment_terms","loading_port","destination_port","note"): setattr(pi,f,request.form.get(f) or None)
        if request.form.get("pi_date"): pi.pi_date=date.fromisoformat(request.form["pi_date"])
        bank=db.session.get(BankAccount,int(request.form["bank_account_id"])) if request.form.get("bank_account_id") else None
        if bank: apply_bank_snapshot(pi,bank)
        for item in pi.items:
            prefix=f"item_{item.id}_"
            if request.form.get(prefix+"quantity"):
                item.quantity=Decimal(request.form[prefix+"quantity"]); item.unit_price=Decimal(request.form[prefix+"unit_price"])
                item.quantity_unit=request.form.get(prefix+"quantity_unit") or item.quantity_unit; item.trade_term=request.form.get(prefix+"trade_term") or None
                item.line_total=(item.quantity*item.unit_price).quantize(Decimal("0.01"))
        for f in ("commission_rate","commission_amount"):
            if f in request.form: setattr(pi,f,Decimal(request.form[f])) if request.form[f] else setattr(pi,f,None)
        for f in ("commission_currency","commission_amount_mode","commission_override_reason"):
            if f in request.form: setattr(pi,f,request.form.get(f) or None)
    elif session.module=="PAYMENT":
        for f in ("advance_payment_percent","advance_payment_amount","balance_payment_amount","advance_received_amount","balance_received_amount"):
            if f in request.form: setattr(pi,f,Decimal(request.form[f])) if request.form[f] else setattr(pi,f,None)
        for f in ("advance_received_at","balance_received_at"):
            if f in request.form: setattr(pi,f,datetime.fromisoformat(request.form[f])) if request.form[f] else setattr(pi,f,None)
    elif session.module=="DOCUMENTS":
        for f in DOCUMENT_FACTS: setattr(pi,f,_tri_state(request.form.get(f)))
        pi.other_document_notes=request.form.get("other_document_notes") or None
    elif session.module=="SHIPPING":
        for f in ("container_type","container_location","driver_name","driver_phone","vehicle_number","vessel_info","booking_number","shipping_mark","freight_term","contract_number","freight_clause","waybill_option","container_number","seal_number","package_unit"):
            setattr(pi,f,request.form.get(f) or None)
        pi.container_count=int(request.form["container_count"]) if request.form.get("container_count") else None
        pi.container_loading_at=datetime.fromisoformat(request.form["container_loading_at"]) if request.form.get("container_loading_at") else None
        for f in ("etd","eta","actual_departure_date"): setattr(pi,f,date.fromisoformat(request.form[f])) if request.form.get(f) else setattr(pi,f,None)
    elif session.module=="FREIGHT":
        settlement=settlement or FreightSettlement(pi_id=pi.id); db.session.add(settlement)
        for f in ("usd_bill_required","cny_bill_required"): setattr(settlement,f,_tri_state(request.form.get(f)))
        for f in ("usd_bill_amount","cny_bill_amount"): setattr(settlement,f,Decimal(request.form[f])) if request.form.get(f) else setattr(settlement,f,None)
        settlement.payment_status=request.form.get("payment_status") or None
        settlement.paid_at=datetime.fromisoformat(request.form["paid_at"]) if request.form.get("paid_at") else None
        if agreement and request.form.get("agreement_amount") and request.form.get("agreement_currency"):
            old=f"{agreement.currency} {agreement.amount}"
            agreement.amount=Decimal(request.form["agreement_amount"]); agreement.currency=request.form["agreement_currency"].upper()
            agreement.note="\n".join(filter(None,[agreement.note,
                f"Correction {utcnow().isoformat()}: {old} -> {agreement.currency} {agreement.amount}. {session.reason}"]))
    elif session.module=="ARRIVAL":
        pi.actual_arrival_date=date.fromisoformat(request.form["actual_arrival_date"]) if request.form.get("actual_arrival_date") else None
    save_order_with_reconcile(pi)
    return redirect(url_for("v2.correction_edit",session_id=session.id))


@blueprint.post("/tasks/<int:task_id>/<action>")
@login_required
def task_action(task_id,action):
    task=db.get_or_404(OrderTask,task_id)
    try:
        if action=="done":
            payload=apply_manual_task_business_fact(task) or {}
            payload.update({key: request.form.get(key) for key in ("tracking_number","carrier") if request.form.get(key)})
            mark_done(task,current_user.id,note=request.form.get("note"),payload=payload)
            reconcile_order_tasks_for_pi(task.pi)
        elif action=="waiting":
            if task.status == "WAITING":
                follow_up(task,current_user.id,waiting_on=request.form.get("waiting_on") or task.waiting_on,
                          next_follow_up_at=parse_datetime(request.form.get("next_follow_up_at")),
                          note=request.form.get("note"),continue_waiting=True)
            else:
                move_to_waiting(task,current_user.id,waiting_on=request.form.get("waiting_on"),
                                next_follow_up_at=parse_datetime(request.form.get("next_follow_up_at")),note=request.form.get("note"))
        elif action=="followup":
            follow_up(task,current_user.id,waiting_on=request.form.get("waiting_on") or task.waiting_on,
                      next_follow_up_at=parse_datetime(request.form.get("next_follow_up_at")),note=request.form.get("note"),
                      continue_waiting=request.form.get("continue_waiting","true")=="true")
        elif action=="reopen": reopen(task,current_user.id,reason=request.form.get("reason"))
        elif action=="cancel": cancel_manual(task,current_user.id,reason=request.form.get("reason"))
        else: abort(404)
        db.session.commit()
    except (TaskOperationError, ValueError) as exc:
        db.session.rollback(); abort(400,str(exc))
    return redirect(url_for("v2.order_view",pi_id=task.pi_id))


@blueprint.get("/orders/<int:pi_id>/documents/<kind>")
@login_required
def document(pi_id,kind):
    from pathlib import Path
    from flask import current_app
    from .documents import booking_missing_fields, render_booking_docx, render_invoice_html
    pi=db.get_or_404(PI,pi_id)
    root=Path(current_app.root_path).parent/"static_v2"/"generated"/kind; root.mkdir(parents=True,exist_ok=True)
    if kind=="booking":
        missing = booking_missing_fields(pi)
        if missing:
            abort(400, "Booking is missing: " + ", ".join(missing))
        path=root/f"booking_{pi.pi_no}.docx"
        path.write_bytes(render_booking_docx(pi, Path(current_app.root_path)/"templates"/"word"/"BN-Sample.docx").getvalue())
    else:
        from weasyprint import HTML
        if kind not in {"pi","invoice","packing"}: abort(404)
        html=render_invoice_html(pi,kind)
        path=root/f"{kind}_{pi.pi_no}.pdf"; HTML(string=html).write_pdf(path)
    return send_file(path,as_attachment=True)
