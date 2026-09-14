"""Atomic NEW-only commercial editing. No shipment commands or schema changes."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from sqlalchemy import select, update

from .models import BankAccount, Customer, Exporter, Factory, PI, PIItem, Product, ProductBatch, TradeGroup, db, utcnow
from .linked_trade_creation import DOCUMENT_FACTS
from .services import apply_bank_snapshot, apply_product_snapshot, reconcile_order_tasks_for_pi


class NewOrderEditError(ValueError):
    pass


PARTY_FIELDS = ("name", "address", "tax_code", "country", "contact", "phone", "email")
CORE_TEXT = ("pi_no", "payment_terms", "currency", "note", "other_document_notes",
             "loading_port", "destination_port", "shipping_mark", "freight_term", "contract_number",
             "freight_clause", "waybill_option", "notify_party_name_snapshot",
             "notify_party_address_snapshot", "notify_party_tax_code_snapshot")
PARTY_SNAPSHOTS = tuple(f"{party}_{field}_snapshot" for party in ("customer", "exporter") for field in PARTY_FIELDS)
ITEM_FIELDS = ("item_id", "product", "factory", "trade_term", "quantity", "quantity_unit", "unit_price",
               "product_model_snapshot", "product_category_snapshot", "product_brand_snapshot",
               "product_packaging_snapshot", "product_hs_code_snapshot")
EDIT_FIELDS = set(CORE_TEXT + PARTY_SNAPSHOTS + DOCUMENT_FACTS) | {
    "csrf_token", "edit_version", "pi_date", "order_type", "customer_id", "exporter_id", "bank_account_id",
    "planned_shipment_date", "advance_payment_percent", "advance_payment_amount", "balance_payment_amount",
    "payment_plan_choice", "commission_factory_id", "commission_rate", "commission_currency",
    "commission_amount_mode", "commission_amount", "commission_override_reason", "notify_party_same_as_consignee",
}


def _number(raw, label, places, *, optional=False):
    if raw in (None, "") and optional:
        return None
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value < 0 or value >= Decimal("100000000000000"):
            raise ValueError()
        if value != value.quantize(Decimal(places)):
            raise ValueError()
        return value
    except (InvalidOperation, ValueError):
        raise NewOrderEditError(f"{label} must be a non-negative number with precision {places}.") from None


def _master(model, raw, label, current=None, *, required=False):
    if not raw:
        if required:
            raise NewOrderEditError(f"{label} is required.")
        return None
    try:
        row = db.session.get(model, int(raw))
    except (ValueError, TypeError):
        row = None
    if row is None or (not row.active and row.id != current):
        raise NewOrderEditError(f"{label} is unavailable.")
    return row


def validate_new_order(pi):
    if pi.status != "NEW":
        raise NewOrderEditError("Full order editing is available only while the order is NEW.")
    if pi.trade_group_id is not None or pi.trade_role is not None:
        group = db.session.get(TradeGroup, pi.trade_group_id) if pi.trade_group_id else None
        members = list(db.session.scalars(select(PI).where(PI.trade_group_id == pi.trade_group_id))) if group else []
        if (len(members) != 2 or {row.trade_role for row in members} != {"CUSTOMER_ORDER", "EXPORT_ORDER"}
                or pi.id not in {row.id for row in members}):
            raise NewOrderEditError("A valid structured customer/export pair is required.")


def edit_form_data(pi):
    if pi.trade_role == "EXPORT_ORDER":
        fields = ("pi_no", "customer_id", "exporter_id", "bank_account_id", "payment_terms", "currency")
        data = {field: str(getattr(pi, field) or "") for field in fields}
        data.update({field: "" if getattr(pi, field) is None else str(getattr(pi, field)).lower()
                     for field in DOCUMENT_FACTS})
        data["edit_version"] = pi.updated_at.isoformat()
        for item in pi.items:
            data[f"unit_price_{item.id}"] = str(item.unit_price)
            data[f"trade_term_{item.id}"] = item.trade_term or "FOB"
        return data
    data = {field: str(getattr(pi, field)) if getattr(pi, field, None) is not None else ""
            for field in EDIT_FIELDS if hasattr(PI, field)}
    for field in DOCUMENT_FACTS + ("notify_party_same_as_consignee",):
        data[field] = "" if getattr(pi, field) is None else str(getattr(pi, field)).lower()
    data["edit_version"] = pi.updated_at.isoformat()
    data["payment_plan_choice"] = ""
    for index, item in enumerate(pi.items):
        for field in ITEM_FIELDS:
            attr = {"item_id": "id", "product": "product_id", "factory": "factory_id"}.get(field, field)
            value = getattr(item, attr)
            data[f"{field}_{index}"] = "" if value is None else str(value)
    return data


def item_indexes(form):
    return sorted({int(match[1]) for key in form if (match := re.fullmatch(r"product_(\d+)", key))})



def _apply_export_edit(pi, form):
    """Mirror linked-export creation inputs; copied item/shipment facts stay fixed."""
    allowed = {"csrf_token", "edit_version", "pi_no", "customer_id", "exporter_id",
               "bank_account_id", "payment_terms", "currency"} | set(DOCUMENT_FACTS)
    allowed |= {f"{field}_{item.id}" for item in pi.items for field in ("unit_price", "trade_term")}
    unexpected = set(form) - allowed
    if unexpected:
        raise NewOrderEditError("Fields are not editable here: " + ", ".join(sorted(unexpected)))
    values = {}
    for field in ("pi_no", "payment_terms", "currency"):
        values[field] = (form.get(field) or "").strip()
        if not values[field]:
            raise NewOrderEditError(f"{field} is required.")
    values["currency"] = values["currency"].upper()
    if len(values["pi_no"]) > 50 or len(values["currency"]) > 10:
        raise NewOrderEditError("PI Number or Currency is too long.")
    if db.session.scalar(select(PI.id).where(PI.pi_no == values["pi_no"], PI.id != pi.id)):
        raise NewOrderEditError("PI Number already exists.")
    customer = _master(Customer, form.get("customer_id"), "Customer", required=True)
    seller = _master(Exporter, form.get("exporter_id"), "Exporter", required=True)
    bank = _master(BankAccount, form.get("bank_account_id"), "Bank Account", required=True)
    parsed = []
    for item in pi.items:
        price = _number(form.get(f"unit_price_{item.id}"), "Export unit price", ".0001")
        if price <= 0:
            raise NewOrderEditError("Every export item needs a positive independent unit price.")
        term = (form.get(f"trade_term_{item.id}") or "FOB").strip() or "FOB"
        if len(term) > 20:
            raise NewOrderEditError("Trade Term is too long.")
        parsed.append((item, price, term))
    for field in DOCUMENT_FACTS:
        raw = form.get(field, "")
        if raw not in ("", "true", "false"):
            raise NewOrderEditError("Invalid document choice.")
        values[field] = None if raw == "" else raw == "true"
    for party, row in (("customer", customer), ("exporter", seller)):
        if row.id != getattr(pi, f"{party}_id"):
            values[f"{party}_id"] = row.id
            for field in ("name", "address", "country", "contact", "phone", "email"):
                values[f"{party}_{field}_snapshot"] = getattr(row, "contact_person" if field == "contact" else field)
    for field, value in values.items():
        setattr(pi, field, value)
    if bank.id != pi.bank_account_id:
        apply_bank_snapshot(pi, bank)
    for item, price, term in parsed:
        item.unit_price, item.trade_term = price, term
        item.line_total = (price * item.quantity).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def save_new_order(pi, form):
    """Validate under a NEW row claim; apply the complete patch and commit once."""
    try:
        with db.session.no_autoflush:
            validate_new_order(pi)
            version = form.get("edit_version")
            if version != pi.updated_at.isoformat():
                raise NewOrderEditError("Order changed. Reload the editor before saving.")
            claimed = db.session.execute(update(PI).where(
                PI.id == pi.id, PI.status == "NEW", PI.updated_at == pi.updated_at,
            ).values(updated_at=PI.updated_at).execution_options(synchronize_session=False))
            if claimed.rowcount != 1:
                raise NewOrderEditError("Order changed or left NEW. Reload before saving.")
            db.session.refresh(pi, with_for_update=True)
            db.session.expire(pi, ["items"])
            validate_new_order(pi)
            if pi.trade_role == "EXPORT_ORDER":
                _apply_export_edit(pi, form)
            else:
                indexes = item_indexes(form)
                allowed = EDIT_FIELDS | {f"{field}_{index}" for index in indexes for field in ITEM_FIELDS}
                unexpected = set(form) - allowed
                if unexpected:
                    raise NewOrderEditError("Fields are not editable here: " + ", ".join(sorted(unexpected)))
                if pi.trade_group_id and form.get("order_type", pi.order_type) != pi.order_type:
                    raise NewOrderEditError("Linked order type must remain unchanged.")
                values = {}
                for field in CORE_TEXT:
                    values[field] = (form.get(field, getattr(pi, field)) or "").strip() or None
                for field in ("pi_no", "currency", "payment_terms"):
                    if not values[field]:
                        raise NewOrderEditError(f"{field.replace('_', ' ').title()} is required.")
                values["currency"] = values["currency"].upper()
                if len(values["pi_no"]) > 50 or len(values["currency"]) > 10:
                    raise NewOrderEditError("PI Number or Currency is too long.")
                if db.session.scalar(select(PI.id).where(PI.pi_no == values["pi_no"], PI.id != pi.id)):
                    raise NewOrderEditError("PI Number already exists.")
                try:
                    values["pi_date"] = date.fromisoformat(form.get("pi_date", ""))
                    raw = form.get("planned_shipment_date", pi.planned_shipment_date.isoformat() if pi.planned_shipment_date else "")
                    values["planned_shipment_date"] = date.fromisoformat(raw) if raw else None
                except ValueError:
                    raise NewOrderEditError("PI Date and Planned Shipment Date must be valid calendar dates.") from None
                values["order_type"] = form.get("order_type", pi.order_type)
                if values["order_type"] not in {"SALES", "COMMISSION"}:
                    raise NewOrderEditError("Order Type is invalid.")
                if values["order_type"] == "SALES" and not values["planned_shipment_date"]:
                    raise NewOrderEditError("Planned Shipment Date is required for Sales orders.")
                customer = _master(Customer, form.get("customer_id"), "Customer", pi.customer_id, required=True)
                seller = _master(Exporter, form.get("exporter_id"), "Exporter", pi.exporter_id, required=True)
                bank = _master(BankAccount, form.get("bank_account_id"), "Bank Account", pi.bank_account_id, required=False)
                values.update(customer_id=customer.id, exporter_id=seller.id)
                for party, row in (("customer", customer), ("exporter", seller)):
                    changed = row.id != getattr(pi, f"{party}_id")
                    for field in PARTY_FIELDS:
                        attr = f"{party}_{field}_snapshot"
                        master_value = getattr(row, "contact_person" if field == "contact" else field, None)
                        values[attr] = master_value if changed else (form.get(attr, getattr(pi, attr)) or None)
                    if not values[f"{party}_name_snapshot"]:
                        raise NewOrderEditError(f"{party.title()} snapshot name is required.")
                for field in DOCUMENT_FACTS + ("notify_party_same_as_consignee",):
                    if field in form:
                        if form[field] not in ("", "true", "false"):
                            raise NewOrderEditError("Invalid document/notify-party choice.")
                        values[field] = None if form[field] == "" else form[field] == "true"

                existing = {item.id: item for item in pi.items}
                seen, parsed = set(), []
                total, drivers_changed = Decimal("0.00"), False
                if not indexes:
                    raise NewOrderEditError("At least one PI item is required.")
                for index in indexes:
                    raw_id = form.get(f"item_id_{index}")
                    try:
                        ident = int(raw_id) if raw_id else None
                    except ValueError:
                        raise NewOrderEditError("Invalid PI item ID.") from None
                    if ident is not None and (ident not in existing or ident in seen):
                        raise NewOrderEditError("PI item does not belong to this order or is duplicated.")
                    old = existing.get(ident)
                    if ident:
                        seen.add(ident)
                    product = _master(Product, form.get(f"product_{index}"), "Product", old.product_id if old else None, required=True)
                    factory = _master(Factory, form.get(f"factory_{index}"), "Factory", old.factory_id if old else None)
                    qty = _number(form.get(f"quantity_{index}"), "Quantity", ".001")
                    price = _number(form.get(f"unit_price_{index}"), "Unit price", ".0001")
                    unit = (form.get(f"quantity_unit_{index}") or "").strip()
                    term = (form.get(f"trade_term_{index}") or "").strip() or None
                    if qty <= 0 or not unit or len(unit) > 20 or (term and len(term) > 20):
                        raise NewOrderEditError("Item needs positive quantity, a unit, and a valid price/Incoterm.")
                    line = (qty * price).quantize(Decimal(".01"))
                    total += line
                    drivers_changed |= old is None or old.quantity != qty or old.unit_price != price
                    snapshots = {}
                    for field in ITEM_FIELDS:
                        if field.startswith("product_") and field != "product":
                            snapshots[field] = form.get(f"{field}_{index}", getattr(old, field, None)) or None
                    parsed.append((old, product, factory, qty, price, unit, term, line, snapshots))
                removed = set(existing) - seen
                if removed and db.session.scalar(select(ProductBatch.id).where(ProductBatch.pi_item_id.in_(removed)).limit(1)):
                    raise NewOrderEditError("Cannot remove an item with existing batches. No dependent records were deleted.")
                percent = _number(form.get("advance_payment_percent", pi.advance_payment_percent), "Advance %", ".01", optional=True)
                if percent is not None and percent > 100:
                    raise NewOrderEditError("Advance % must be between 0 and 100.")
                drivers_changed |= bool(removed) or total != pi.contract_total or values["currency"] != pi.currency or percent != pi.advance_payment_percent
                choice = form.get("payment_plan_choice", "")
                if choice not in ("", "recalculate", "keep"):
                    raise NewOrderEditError("Invalid payment-plan choice.")
                if drivers_changed and not choice:
                    raise NewOrderEditError("Payment-plan inputs changed. Choose Recalculate from Advance % or Keep / Edit Planned Amounts.")
                advance, balance = pi.advance_payment_amount, pi.balance_payment_amount
                if choice == "recalculate":
                    if percent is None:
                        raise NewOrderEditError("Advance % is required to recalculate the payment plan.")
                    advance = (total * percent / Decimal("100")).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
                    balance = (total - advance).quantize(Decimal(".01"))
                elif choice == "keep":
                    advance = _number(form.get("advance_payment_amount"), "Planned advance", ".01")
                    balance = _number(form.get("balance_payment_amount"), "Planned balance", ".01")
                    if advance + balance != total:
                        raise NewOrderEditError(
                            f"Planned advance + planned balance must equal the current order total: {values['currency']} {total:.2f}.")
                elif any(field in form and _number(form[field], field, ".01", optional=True) != getattr(pi, field)
                         for field in ("advance_payment_amount", "balance_payment_amount")):
                    raise NewOrderEditError("Choose Keep / Edit Planned Amounts to change planned amounts.")
                values.update(advance_payment_percent=percent, advance_payment_amount=advance, balance_payment_amount=balance)
                factory = _master(Factory, form.get("commission_factory_id"), "Commission Factory", pi.commission_factory_id)
                values["commission_factory_id"] = factory.id if factory else None
                for field in ("commission_rate", "commission_amount"):
                    values[field] = _number(form.get(field, getattr(pi, field)), field, ".0001" if field == "commission_rate" else ".01", optional=True)
                for field in ("commission_currency", "commission_amount_mode", "commission_override_reason"):
                    values[field] = form.get(field, getattr(pi, field)) or None
                if values["commission_amount_mode"] not in (None, "DERIVED", "EXPLICIT_OVERRIDE"):
                    raise NewOrderEditError("Invalid commission mode.")
                if values["order_type"] == "COMMISSION" and values["commission_amount_mode"] == "EXPLICIT_OVERRIDE" and (
                    values["commission_amount"] is None or not values["commission_override_reason"]):
                    raise NewOrderEditError("Commission override requires amount and reason.")

                # All validation has passed. Only now mutate order-local business data.
                for field, value in values.items():
                    setattr(pi, field, value)
                if bank is None:
                    pi.bank_account_id = None
                    for column in PI.__table__.columns:
                        if column.name.startswith("bank_") and column.name.endswith("_snapshot"):
                            setattr(pi, column.name, None)
                elif bank.id != pi.bank_account_id:
                    apply_bank_snapshot(pi, bank)
                for old, product, factory, qty, price, unit, term, line, snapshots in parsed:
                    item = old or PIItem()
                    if old is None or old.product_id != product.id:
                        apply_product_snapshot(item, product)
                    else:
                        for field, value in snapshots.items():
                            setattr(item, field, value)
                    if old is None or old.factory_id != (factory.id if factory else None):
                        for field in ("name", "address", "tax_code", "country", "contact", "phone"):
                            setattr(item, f"factory_{field}_snapshot", getattr(factory, "contact_person" if field == "contact" else field, None))
                    item.product_id, item.factory_id = product.id, factory.id if factory else None
                    item.quantity, item.unit_price, item.quantity_unit, item.trade_term, item.line_total = qty, price, unit, term, line
                    if old is None:
                        pi.items.append(item)
                for ident in removed:
                    pi.items.remove(existing[ident])
                pi.derive_commission()
            pi.updated_at = utcnow()
        db.session.flush()
        reconcile_order_tasks_for_pi(pi)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
