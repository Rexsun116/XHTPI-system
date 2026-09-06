"""Atomic creation of the execution-side PI for a linked trade."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import Customer, Exporter, PI, PIItem, TradeGroup, db
from .services import reconcile_order_tasks_for_pi


class LinkedExportCreationError(ValueError):
    """A user-visible validation error; no partial linked-trade data remains."""


DOCUMENT_FACTS = (
    "coo_required", "apta_required", "export_license_required", "customs_docs_required",
    "coc_required", "coa_required", "original_bl_required", "obd_electronic_required",
    "insurance_original_required", "insurance_electronic_required",
    "original_documents_mail_required", "telex_release_required", "settlement_documents_required",
)


def linked_export_creation_error(source):
    """Return the reason a PI cannot become the commercial half of a new pair."""
    if source.status == "COMPLETED":
        return "Completed orders cannot create a linked export order."
    if source.trade_group_id is not None or source.trade_role is not None:
        if source.trade_role == "EXPORT_ORDER":
            return "An EXPORT_ORDER cannot create another linked export order."
        return "This order is already linked or has an incomplete linked-trade configuration."
    if source.order_type != "SALES":
        return "Only commercial Sales orders can create a linked export order."
    if not source.items:
        return "The commercial order must contain at least one PI item."
    return None


def _tri_state(value):
    return None if value in (None, "") else value == "true"


def _required(form, name, label):
    value = (form.get(name) or "").strip()
    if not value:
        raise LinkedExportCreationError(f"{label} is required.")
    return value


def _new_group_number():
    # Relationship identity is deliberately opaque: no customer/export PI number inference.
    return f"TRI-{uuid4().hex[:16].upper()}"


def create_linked_export_order(source_id, form):
    """Create both link metadata and export PI in one database transaction.

    Form values are only inputs.  Role, stats ownership, copied item identity and
    source eligibility are all decided by this service.
    """
    try:
        source = db.session.get(PI, source_id)
        if source is None:
            raise LinkedExportCreationError("Source order no longer exists.")
        error = linked_export_creation_error(source)
        if error:
            raise LinkedExportCreationError(error)

        pi_no = _required(form, "pi_no", "Export PI Number")
        if db.session.scalar(select(PI.id).where(PI.pi_no == pi_no)) is not None:
            raise LinkedExportCreationError("PI Number already exists.")
        try:
            customer_id = int(_required(form, "customer_id", "Export customer"))
            exporter_id = int(_required(form, "exporter_id", "Export seller"))
        except ValueError as exc:
            raise LinkedExportCreationError("Export customer and seller are invalid.") from exc
        customer = db.session.get(Customer, customer_id)
        exporter = db.session.get(Exporter, exporter_id)
        if customer is None or exporter is None or not customer.active or not exporter.active:
            raise LinkedExportCreationError("Export customer or seller is unavailable.")

        prices = {}
        for item in source.items:
            try:
                price = Decimal(_required(form, f"unit_price_{item.id}", f"Export unit price for item {item.id}"))
            except Exception as exc:
                if isinstance(exc, LinkedExportCreationError):
                    raise
                raise LinkedExportCreationError("Export unit prices must be valid numbers.") from exc
            if price <= 0:
                raise LinkedExportCreationError("Every export item needs a positive independent unit price.")
            prices[item.id] = price

        group = TradeGroup(group_no=_new_group_number())
        db.session.add(group)
        source.trade_group = group
        source.trade_role = "CUSTOMER_ORDER"
        source.include_in_business_stats = True

        payment_terms = _required(form, "payment_terms", "Payment Terms")
        export = PI(
            pi_no=pi_no, pi_date=date.today(), order_type="SALES", status="NEW",
            customer_id=customer.id, exporter_id=exporter.id,
            customer_name_snapshot=customer.name, customer_address_snapshot=customer.address,
            customer_country_snapshot=customer.country, customer_contact_snapshot=customer.contact_person,
            customer_phone_snapshot=customer.phone, customer_email_snapshot=customer.email,
            exporter_name_snapshot=exporter.name, exporter_address_snapshot=exporter.address,
            exporter_country_snapshot=exporter.country, exporter_contact_snapshot=exporter.contact_person,
            exporter_phone_snapshot=exporter.phone, exporter_email_snapshot=exporter.email,
            currency=(form.get("currency") or source.currency or "USD").upper(), payment_terms=payment_terms,
            trade_group=group, trade_role="EXPORT_ORDER", include_in_business_stats=False,
            planned_shipment_date=source.planned_shipment_date,
            loading_port=source.loading_port, destination_port=source.destination_port,
            container_loading_date=source.container_loading_date,
            container_loading_period=source.container_loading_period,
            container_location=source.container_location, container_type=source.container_type,
            container_count=source.container_count, shipping_mark=source.shipping_mark,
            freight_forwarder_id=source.freight_forwarder_id, vessel_info=source.vessel_info,
            booking_number=source.booking_number, etd=source.etd, eta=source.eta,
        )
        for fact in DOCUMENT_FACTS:
            setattr(export, fact, _tri_state(form.get(fact)))
        for source_item in source.items:
            price = prices[source_item.id]
            export.items.append(PIItem(
                product_id=source_item.product_id, factory_id=source_item.factory_id,
                trade_term=(form.get(f"trade_term_{source_item.id}") or "FOB").strip() or "FOB",
                unit_price=price, quantity=source_item.quantity, quantity_unit=source_item.quantity_unit,
                line_total=(price * source_item.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                product_category_snapshot=source_item.product_category_snapshot,
                product_brand_snapshot=source_item.product_brand_snapshot,
                product_model_snapshot=source_item.product_model_snapshot,
                product_packaging_snapshot=source_item.product_packaging_snapshot,
                product_hs_code_snapshot=source_item.product_hs_code_snapshot,
                factory_name_snapshot=source_item.factory_name_snapshot,
                factory_address_snapshot=source_item.factory_address_snapshot,
                factory_tax_code_snapshot=source_item.factory_tax_code_snapshot,
                factory_country_snapshot=source_item.factory_country_snapshot,
                factory_contact_snapshot=source_item.factory_contact_snapshot,
                factory_phone_snapshot=source_item.factory_phone_snapshot,
                factory_email_snapshot=source_item.factory_email_snapshot,
            ))
        db.session.add(export)
        db.session.flush()
        reconcile_order_tasks_for_pi(export)
        db.session.commit()
        return export
    except (LinkedExportCreationError, IntegrityError, ValueError):
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise
