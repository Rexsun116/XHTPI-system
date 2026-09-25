"""Read-only V2 management-report aggregates.

This module deliberately accepts already-loaded order data.  It contains no
database writes, no Flask concerns, and no FX or unit-conversion policy.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from order_lifecycle import OrderStage


ZERO = Decimal("0.00")
COUNTRY_NOT_RECORDED = "Country not recorded"
PORT_NOT_RECORDED = "Port not recorded"
CUSTOMER_NOT_RECORDED = "Customer not recorded"
PRODUCT_NOT_RECORDED = "Product not recorded"
UNIT_NOT_RECORDED = "Unit not recorded"


def _money(value):
    return Decimal(value) if value is not None else ZERO


def _currency(order):
    value = str(getattr(order, "currency", "") or "").strip().upper()
    return value or "Currency not recorded"


def _unit(item):
    value = str(getattr(item, "quantity_unit", "") or "").strip().upper()
    return value or UNIT_NOT_RECORDED


def _customer_label_key(label):
    """Prefer recorded historical labels, then select one deterministically."""
    return (label == CUSTOMER_NOT_RECORDED, label)


def _month_key(value):
    return f"{value.year:04d}-{value.month:02d}"


def _is_ytd(value, as_of):
    return value is not None and value.year == as_of.year and value <= as_of


def _new_money_map():
    return defaultdict(lambda: ZERO)


def _as_dict(values):
    return dict(sorted(values.items()))


def select_reporting_owners(orders):
    """Return formal reporting owners and explicit excluded-link quality data.

    A linked group is reportable only when the supplied data contains exactly
    one CUSTOMER_ORDER and one EXPORT_ORDER.  This deliberately excludes
    malformed or partial link metadata rather than guessing from PI numbers.
    """
    orders = list(orders)
    groups = defaultdict(list)
    owners = []
    malformed_ids = set()
    disabled_ids = set()

    for order in orders:
        group_id = getattr(order, "trade_group_id", None)
        role = getattr(order, "trade_role", None)
        if group_id is None and role is None:
            if getattr(order, "include_in_business_stats", None) is True:
                owners.append(order)
            else:
                disabled_ids.add(getattr(order, "id", None))
        elif group_id is None or role is None:
            malformed_ids.add(getattr(order, "id", None))
        else:
            groups[group_id].append(order)

    for members in groups.values():
        customers = [row for row in members if getattr(row, "trade_role", None) == "CUSTOMER_ORDER"]
        exports = [row for row in members if getattr(row, "trade_role", None) == "EXPORT_ORDER"]
        valid_pair = len(members) == 2 and len(customers) == 1 and len(exports) == 1
        if not valid_pair:
            malformed_ids.update(getattr(row, "id", None) for row in members)
            continue
        customer = customers[0]
        if getattr(customer, "include_in_business_stats", None) is True:
            owners.append(customer)
        else:
            disabled_ids.add(getattr(customer, "id", None))

    malformed_ids.discard(None)
    disabled_ids.discard(None)
    return owners, {
        "malformed_linked_metadata_excluded_count": len(malformed_ids),
        "malformed_linked_metadata_excluded_order_ids": sorted(malformed_ids),
        "reporting_disabled_order_count": len(disabled_ids),
        "reporting_disabled_order_ids": sorted(disabled_ids),
    }


def _blank_month():
    return {
        "booked_order_count": 0,
        "booked_revenue_by_currency": {},
        "shipped_order_count": 0,
        "arrived_order_count": 0,
    }


def _month_series(as_of):
    return {
        f"{as_of.year:04d}-{month:02d}": _blank_month()
        for month in range(1, as_of.month + 1)
    }


def _order_total(order):
    return _money(getattr(order, "contract_total", ZERO))


def _weight_mt(item):
    """Return the only supported management-weight normalization, or None.

    ``quantity_unit`` remains free text in the order model.  Reporting is
    intentionally narrower: current MT and KGS conventions are accepted after
    the same conservative trim/case canonicalization used by ``_unit``.
    """
    quantity = _money(getattr(item, "quantity", None))
    unit = _unit(item)
    if unit == "MT":
        return quantity
    if unit == "KGS":
        return quantity / Decimal("1000")
    return None


def _freight_context(owners, freight_settlements):
    settlements = {getattr(row, "pi_id", None): row for row in freight_settlements}
    confirmed = {"USD": ZERO, "CNY": ZERO}
    unpaid = {"USD": ZERO, "CNY": ZERO}
    missing, incomplete = 0, 0
    for order in owners:
        settlement = settlements.get(getattr(order, "id", None))
        if settlement is None:
            missing += 1
            continue
        branches = []
        for currency in ("USD", "CNY"):
            required = getattr(settlement, f"{currency.lower()}_bill_required", None)
            amount = getattr(settlement, f"{currency.lower()}_bill_amount", None)
            confirmed_flag = getattr(settlement, f"{currency.lower()}_bill_confirmed", None)
            if required is True:
                branches.append((amount, confirmed_flag))
                if amount is not None and confirmed_flag is True:
                    confirmed[currency] += _money(amount)
                    # Payment status is the authoritative binary settlement
                    # fact.  ``paid_at`` is derived from it and cannot safely
                    # distinguish a paid from an unpaid branch on its own.
                    if getattr(settlement, f"{currency.lower()}_payment_status", None) != "PAID":
                        unpaid[currency] += _money(amount)
        if any(amount is None or confirmed_flag is not True for amount, confirmed_flag in branches):
            incomplete += 1
        elif not branches and (getattr(settlement, "usd_bill_required", None) is None
                               or getattr(settlement, "cny_bill_required", None) is None):
            incomplete += 1
    return {
        "confirmed_bill_amount_by_currency": confirmed,
        "freight_total_billed_by_currency": confirmed,
        "freight_unpaid_by_currency": unpaid,
        "orders_with_missing_settlement_count": missing,
        "orders_with_incomplete_settlement_count": incomplete,
    }


def build_management_report(orders, freight_settlements=(), *, as_of=None):
    """Build current management aggregates from an already-read V2 dataset.

    Monetary values remain separate by currency and quantities remain separate
    by recorded unit.  Customer/product/geography sections describe all valid
    reporting owners in the supplied dataset; the explicitly YTD KPI and time
    series sections use ``as_of.year``.
    """
    as_of = as_of or date.today()
    owners, link_quality = select_reporting_owners(orders)
    months = _month_series(as_of)
    booked_revenue = _new_money_map()
    shipped_revenue = _new_money_map()
    arrived_revenue = _new_money_map()
    contracted = _new_money_map()
    received = _new_money_map()
    outstanding = _new_money_map()
    customer_rows = {}
    product_rows = {}
    country_rows = {}
    port_rows = {}
    missing_customer, missing_product, missing_country = 0, 0, 0
    unknown_units, all_units = 0, set()
    open_order_weight_mt = ZERO
    unshipped_open_order_weight_mt = ZERO
    open_weight_excluded_lines = 0
    open_weight_excluded_units = set()
    ytd_order_count = shipped_order_count = arrived_order_count = 0
    in_transit = upcoming = 0

    for order in owners:
        currency = _currency(order)
        total = _order_total(order)
        advance = _money(getattr(order, "advance_received_amount", None))
        balance = _money(getattr(order, "balance_received_amount", None))
        paid = advance + balance
        due = max(total - paid, ZERO)
        contracted[currency] += total
        received[currency] += paid
        outstanding[currency] += due

        customer_id = getattr(order, "customer_id", None)
        customer_label = str(getattr(order, "customer_name_snapshot", "") or "").strip()
        if not customer_label:
            customer_label = CUSTOMER_NOT_RECORDED
            missing_customer += 1
        customer_key = customer_id if customer_id is not None else f"missing:{getattr(order, 'id', None)}"
        customer = customer_rows.setdefault(customer_key, {
            "customer_id": customer_id, "label": customer_label, "order_count": 0,
            "revenue_by_currency": _new_money_map(), "outstanding_by_currency": _new_money_map(),
        })
        # Customer masters are mutable, while orders retain their historical
        # label.  A customer may therefore have multiple historical labels.
        # Pick a stable snapshot-only representative independent of query order.
        customer["label"] = min(customer["label"], customer_label, key=_customer_label_key)
        customer["order_count"] += 1
        customer["revenue_by_currency"][currency] += total
        customer["outstanding_by_currency"][currency] += due

        country = str(getattr(order, "destination_country_code", "") or "").strip().upper()
        if not country:
            country = COUNTRY_NOT_RECORDED
            missing_country += 1
        country_row = country_rows.setdefault(country, {"country_code": country, "order_count": 0,
                                                          "revenue_by_currency": _new_money_map()})
        country_row["order_count"] += 1
        country_row["revenue_by_currency"][currency] += total

        port = str(getattr(order, "destination_port", "") or "").strip() or PORT_NOT_RECORDED
        port_rows[port] = port_rows.get(port, 0) + 1

        booked_date = getattr(order, "pi_date", None)
        if _is_ytd(booked_date, as_of):
            ytd_order_count += 1
            booked_revenue[currency] += total
            month = months[_month_key(booked_date)]
            month["booked_order_count"] += 1
            month_revenue = _new_money_map()
            month_revenue.update(month["booked_revenue_by_currency"])
            month_revenue[currency] += total
            month["booked_revenue_by_currency"] = _as_dict(month_revenue)

        shipped_date = getattr(order, "actual_departure_date", None)
        if _is_ytd(shipped_date, as_of):
            shipped_order_count += 1
            shipped_revenue[currency] += total
            months[_month_key(shipped_date)]["shipped_order_count"] += 1

        arrived_date = getattr(order, "actual_arrival_date", None)
        if _is_ytd(arrived_date, as_of):
            arrived_order_count += 1
            arrived_revenue[currency] += total
            months[_month_key(arrived_date)]["arrived_order_count"] += 1

        if shipped_date and not arrived_date:
            in_transit += 1
        planned_context = getattr(order, "etd", None) or getattr(order, "planned_shipment_date", None)
        if not shipped_date and planned_context and planned_context >= as_of:
            upcoming += 1

        is_open = getattr(order, "status", None) != OrderStage.COMPLETED
        for item in getattr(order, "items", ()):
            product_label = str(getattr(item, "product_model_snapshot", "") or "").strip()
            if not product_label:
                product_label = PRODUCT_NOT_RECORDED
                missing_product += 1
            product = product_rows.setdefault(product_label, {
                "product_snapshot": product_label, "order_ids": set(), "customer_ids": set(),
                "revenue_by_currency": _new_money_map(), "quantity_by_unit": _new_money_map(),
            })
            product["order_ids"].add(getattr(order, "id", None))
            product["customer_ids"].add(customer_key)
            product["revenue_by_currency"][currency] += _money(getattr(item, "line_total", None))
            unit = _unit(item)
            product["quantity_by_unit"][unit] += _money(getattr(item, "quantity", None))
            all_units.add(unit)
            if unit == UNIT_NOT_RECORDED:
                unknown_units += 1

            if is_open:
                normalized_weight = _weight_mt(item)
                if normalized_weight is None:
                    open_weight_excluded_lines += 1
                    open_weight_excluded_units.add(unit)
                else:
                    open_order_weight_mt += normalized_weight
                    if not shipped_date:
                        unshipped_open_order_weight_mt += normalized_weight

    freight = _freight_context(owners, freight_settlements)
    collection_rate = {
        currency: (received[currency] / amount if amount else Decimal("0"))
        for currency, amount in contracted.items()
    }
    average_order_value = {
        currency: (amount / sum(1 for order in owners if _currency(order) == currency))
        for currency, amount in contracted.items()
    }

    for month in months.values():
        month["booked_revenue_by_currency"] = _as_dict(month["booked_revenue_by_currency"])

    customers = []
    for row in customer_rows.values():
        customers.append({
            "customer_id": row["customer_id"], "label": row["label"], "order_count": row["order_count"],
            "revenue_by_currency": _as_dict(row["revenue_by_currency"]),
            "outstanding_by_currency": _as_dict(row["outstanding_by_currency"]),
        })
    products = []
    for row in product_rows.values():
        products.append({
            "product_snapshot": row["product_snapshot"], "order_count": len(row["order_ids"]),
            "customer_count": len(row["customer_ids"]), "revenue_by_currency": _as_dict(row["revenue_by_currency"]),
            "quantity_by_unit": _as_dict(row["quantity_by_unit"]),
        })
    countries = []
    for row in country_rows.values():
        countries.append({"country_code": row["country_code"], "order_count": row["order_count"],
                          "revenue_by_currency": _as_dict(row["revenue_by_currency"])})

    owner_count = len(owners)
    quality = {
        **link_quality,
        "missing_destination_country_count": missing_country,
        "destination_country_coverage_rate": (
            Decimal(owner_count - missing_country) / owner_count if owner_count else Decimal("0")
        ),
        "missing_product_snapshot_item_count": missing_product,
        "missing_customer_snapshot_order_count": missing_customer,
        "unknown_quantity_unit_item_count": unknown_units,
        "quantity_unit_types": sorted(all_units),
        "mixed_quantity_units": len(all_units - {UNIT_NOT_RECORDED}) > 1,
        "open_order_weight_excluded_line_count": open_weight_excluded_lines,
        "open_order_weight_excluded_units": sorted(open_weight_excluded_units),
        "freight_missing_settlement_count": freight["orders_with_missing_settlement_count"],
        "freight_incomplete_settlement_count": freight["orders_with_incomplete_settlement_count"],
    }
    return {
        "as_of": as_of,
        "reporting_owner_ids": [getattr(order, "id", None) for order in owners],
        "kpis": {
            "ytd_reporting_owner_order_count": ytd_order_count,
            "ytd_booked_revenue_by_currency": _as_dict(booked_revenue),
            "ytd_shipped_order_count": shipped_order_count,
            "ytd_shipped_revenue_by_currency": _as_dict(shipped_revenue),
            "ytd_arrived_order_count": arrived_order_count,
            "ytd_arrived_revenue_by_currency": _as_dict(arrived_revenue),
            "orders_in_transit_count": in_transit,
            "upcoming_shipments_count": upcoming,
            "open_order_weight_mt": open_order_weight_mt,
            "unshipped_open_order_weight_mt": unshipped_open_order_weight_mt,
            "contracted_by_currency": _as_dict(contracted),
            "received_by_currency": _as_dict(received),
            "outstanding_receivables_by_currency": _as_dict(outstanding),
            "collection_rate_by_currency": _as_dict(collection_rate),
            "average_order_value_by_currency": _as_dict(average_order_value),
        },
        "customers": sorted(customers, key=lambda row: (str(row["label"]), row["customer_id"] or -1)),
        "products": sorted(products, key=lambda row: row["product_snapshot"]),
        "geography": {
            "countries": sorted(countries, key=lambda row: row["country_code"]),
            "destination_ports": dict(sorted(port_rows.items())),
        },
        "freight_context": {
            "confirmed_bill_amount_by_currency": _as_dict(freight["confirmed_bill_amount_by_currency"]),
            "freight_total_billed_by_currency": _as_dict(freight["freight_total_billed_by_currency"]),
            "freight_unpaid_by_currency": _as_dict(freight["freight_unpaid_by_currency"]),
            "orders_with_missing_settlement_count": freight["orders_with_missing_settlement_count"],
            "orders_with_incomplete_settlement_count": freight["orders_with_incomplete_settlement_count"],
        },
        "time_series": months,
        "quality": quality,
    }
