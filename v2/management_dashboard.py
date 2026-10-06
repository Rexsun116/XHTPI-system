"""Read-only assembly for the V2 Business Overview page."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .fx_reference import FXReferenceService
from .iso3166 import country_name_for_code
from .models import FreightSettlement, PI, db
from .reporting import build_management_report, select_reporting_owners


fx_reference_service = FXReferenceService()
_MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CHART_LEFT, _CHART_RIGHT, _CHART_TOP, _CHART_BASELINE = 54, 678, 28, 163
PRIMARY_CURRENCY = "USD"
SECONDARY_CURRENCY = "CNY"


def reporting_as_of(year: int, *, today: date | None = None) -> date:
    today = today or date.today()
    return today if year == today.year else date(year, 12, 31)


def _value(values, currency):
    return values.get(currency, Decimal("0.00"))


def format_compact_amount(value: Decimal) -> str:
    """Format a dashboard amount without changing its underlying Decimal value."""
    value = Decimal(value)
    absolute = abs(value)
    if absolute < Decimal("1000"):
        return f"{value:,.2f}"
    if absolute < Decimal("1000000"):
        scaled, suffix, quantum = value / Decimal("1000"), "K", Decimal("0.1")
    else:
        scaled, suffix, quantum = value / Decimal("1000000"), "M", Decimal("0.01")
    rendered = scaled.quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{rendered:,.{abs(quantum.as_tuple().exponent)}f}".rstrip("0").rstrip(".") + suffix


def format_count(value) -> str:
    """Render discrete reporting entities as grouped integers only."""
    return f"{int(Decimal(value)):,}"


def _ordered_currencies(currencies):
    """Keep management currencies stable while retaining separated other currencies."""
    values = {str(currency).strip().upper() for currency in currencies if str(currency).strip()}
    ordered = [currency for currency in (PRIMARY_CURRENCY, SECONDARY_CURRENCY) if currency in values]
    return ordered + sorted(values - set(ordered))


def _report_currencies(report):
    currencies = set()
    for values in (
        report["kpis"]["ytd_booked_revenue_by_currency"],
        report["kpis"]["contracted_by_currency"],
        report["kpis"]["received_by_currency"],
        report["kpis"]["outstanding_receivables_by_currency"],
    ):
        currencies.update(values)
    return _ordered_currencies(currencies)


def _currency_rows(report, currency):
    customers = []
    for row in report["customers"]:
        revenue = _value(row["revenue_by_currency"], currency)
        outstanding = _value(row["outstanding_by_currency"], currency)
        if revenue or outstanding:
            customers.append({
                **row, "revenue": revenue, "outstanding": outstanding,
                "order_count": _value(row.get("order_count_by_currency", {}), currency),
            })
    customers.sort(key=lambda row: (-row["revenue"], row["label"]))

    products = []
    for row in report["products"]:
        revenue = _value(row["revenue_by_currency"], currency)
        if revenue:
            products.append({
                **row, "revenue": revenue,
                "order_count": _value(row.get("order_count_by_currency", {}), currency),
                "customer_count": _value(row.get("customer_count_by_currency", {}), currency),
                "quantity_by_unit": row.get("quantity_by_unit_by_currency", {}).get(currency, {}),
            })
    products.sort(key=lambda row: (-row["revenue"], row["product_snapshot"]))

    countries = []
    for row in report["geography"]["countries"]:
        revenue = _value(row["revenue_by_currency"], currency)
        if revenue:
            countries.append({
                **row, "revenue": revenue,
                "order_count": _value(row.get("order_count_by_currency", {}), currency),
            })
    countries.sort(key=lambda row: (-row["revenue"], row["country_code"]))
    return customers, products, countries


def _x(index, total):
    return _CHART_LEFT if total <= 1 else round(_CHART_LEFT + ((_CHART_RIGHT - _CHART_LEFT) * index / (total - 1)))


def _svg_paths(points):
    path = " ".join(
        f"{'M' if index == 0 else 'L'}{point['x']} {point['y']}"
        for index, point in enumerate(points)
    )
    if not points:
        return "", ""
    area = f"{path} L{points[-1]['x']} {_CHART_BASELINE} L{points[0]['x']} {_CHART_BASELINE}Z"
    return path, area


def _chart_data(report, currencies):
    monthly_rows = list(report["time_series"].items())
    result = {}
    for chart_index, currency in enumerate(currencies):
        values = [row["booked_revenue_by_currency"].get(currency, Decimal("0")) for _, row in monthly_rows]
        maximum = max(values, default=Decimal("0"))
        points = []
        for index, ((month, row), value) in enumerate(zip(monthly_rows, values)):
            y = _CHART_BASELINE - round((value / maximum * (_CHART_BASELINE - _CHART_TOP)) if maximum else 0)
            points.append({
                "month": month[-2:], "month_label": _MONTH_LABELS[int(month[-2:]) - 1],
                "value": value, "orders": row["booked_order_count"], "x": _x(index, len(monthly_rows)),
                "y": y,
            })
        path, area_path = _svg_paths(points)
        peak = max(points, key=lambda point: point["value"], default=None)
        result[currency] = {
            "currency": currency, "points": points, "total": sum(values, Decimal("0")),
            "maximum": maximum, "path": path, "area_path": area_path,
            "peak": peak, "fill_id": f"business-revenue-fill-{chart_index}",
        }
    return result


def _monthly_order_context(report):
    monthly_rows = list(report["time_series"].items())
    counts = [row["booked_order_count"] for _, row in monthly_rows]
    maximum = max(counts, default=0)
    points = [{
        "month_label": _MONTH_LABELS[int(month[-2:]) - 1], "count": count,
        "height": round((count / maximum * 100) if maximum else 0),
    } for (month, _), count in zip(monthly_rows, counts)]
    peak_index = counts.index(maximum) if maximum else None
    return {
        "points": points,
        "average": (Decimal(sum(counts)) / len(counts)) if counts else Decimal("0"),
        "peak_count": maximum,
        "peak_month": points[peak_index]["month_label"] if peak_index is not None else "—",
        "current_month_count": counts[-1] if counts else 0,
    }


def _executive_context(report, order_context, currencies):
    outstanding = report["kpis"]["outstanding_receivables_by_currency"]
    return {
        "current_month_orders": order_context["current_month_count"],
        "outstanding_primary": (PRIMARY_CURRENCY, _value(outstanding, PRIMARY_CURRENCY)),
        "outstanding_secondary": [
            (currency, _value(outstanding, currency))
            for currency in currencies if currency != PRIMARY_CURRENCY
        ],
    }


def _percent(value, total):
    """Return display and bar percentages without changing source measures."""
    percentage = (Decimal(value) / Decimal(total) * 100) if total else Decimal("0")
    return percentage, min(max(percentage, Decimal("0")), Decimal("100"))


def _customer_context(report, currencies):
    result = {}
    for currency in currencies:
        total = _value(report["kpis"]["contracted_by_currency"], currency)
        rows, _, _ = _currency_rows(report, currency)
        entries = []
        for row in rows:
            share, bar_percent = _percent(row["revenue"], total)
            entries.append({**row, "share": share, "share_bar_percent": bar_percent})
        concentration = sum((row["share"] for row in entries[:3]), Decimal("0"))
        result[currency] = {
            "currency": currency,
            "rows": entries,
            "concentration": concentration,
            "outstanding": [row for row in entries if row["outstanding"] > 0],
        }
    return result


def _receivable_context(report, customer_context, currency):
    contracted = _value(report["kpis"]["contracted_by_currency"], currency)
    received = _value(report["kpis"]["received_by_currency"], currency)
    outstanding = _value(report["kpis"]["outstanding_receivables_by_currency"], currency)
    rate = _value(report["kpis"]["collection_rate_by_currency"], currency)
    percentage, bar_percent = _percent(rate, Decimal("1"))
    return {
        "currency": currency, "contracted": contracted, "received": received,
        "outstanding": outstanding, "collection_rate": rate,
        "collection_percent": percentage, "collection_bar_percent": bar_percent,
        "largest_outstanding": customer_context[currency]["outstanding"][:3],
    }


def _product_context(report, currency):
    _, products, _ = _currency_rows(report, currency)
    return {"currency": currency, "rows": products[:5]} if currency else {"currency": None, "rows": []}


def _market_context(report, currency):
    _, _, countries = _currency_rows(report, currency)
    maximum_orders = max((row["order_count"] for row in countries), default=0)
    rows = []
    for row in countries[:5]:
        _, bar_percent = _percent(row["order_count"], maximum_orders)
        code = row["country_code"]
        rows.append({
            **row,
            "country_label": (country_name_for_code(code) or code) if len(code) == 2 else code,
            "order_bar_percent": bar_percent,
        })
    return {"currency": currency, "rows": rows}


def _coverage_context(report, orders):
    quality = report["quality"]
    owner_ids = set(report["reporting_owner_ids"])
    owner_count = len(owner_ids)
    item_count = sum(len(order.items) for order in orders if order.id in owner_ids)
    missing_product = quality["missing_product_snapshot_item_count"]
    missing_freight = quality["freight_missing_settlement_count"]
    incomplete_freight = quality["freight_incomplete_settlement_count"]

    def coverage(missing, total):
        percentage, bar_percent = _percent(max(total - missing, 0), total)
        return {"percent": percentage, "bar_percent": bar_percent}

    return {
        "destination": {
            "percent": quality["destination_country_coverage_rate"] * 100,
            "bar_percent": quality["destination_country_coverage_rate"] * 100,
        },
        "customer": coverage(quality["missing_customer_snapshot_order_count"], owner_count),
        "product": coverage(missing_product, item_count),
        "freight": coverage(missing_freight + incomplete_freight, owner_count),
        "malformed_linked": quality["malformed_linked_metadata_excluded_count"],
        "unsupported_weight_lines": quality["open_order_weight_excluded_line_count"],
        "unknown_units": quality["unknown_quantity_unit_item_count"],
    }


def _shipment_context(report, order_context, as_of):
    current_month = list(report["time_series"].values())[-1] if report["time_series"] else {}
    return {
        "in_transit": report["kpis"]["orders_in_transit_count"],
        "upcoming": report["kpis"]["upcoming_shipments_count"],
        "arrived": current_month.get("arrived_order_count", 0),
        "arrived_period": _MONTH_LABELS[as_of.month - 1],
    }


def build_business_dashboard(year: int, *, today: date | None = None):
    """Load authoritative records and prepare presentation-only structures."""
    current_date = today or date.today()
    as_of = reporting_as_of(year, today=current_date)
    orders = list(db.session.scalars(db.select(PI)))
    settlements = list(db.session.scalars(db.select(FreightSettlement)))
    eligible_owners, _ = select_reporting_owners(orders)
    current_year = current_date.year
    available_years = {
        order.pi_date.year for order in eligible_owners
        if getattr(order, "pi_date", None) is not None
    }
    # Keep a selected empty historical year visible instead of silently
    # replacing the requested reporting period.
    available_years.update((current_year, year))
    report = build_management_report(orders, settlements, as_of=as_of, reporting_year=year)
    currencies = _report_currencies(report)
    # USD remains the primary management view. CNY is rendered alongside it
    # when present so each analytical card can switch locally without a GET.
    analytical_currencies = [PRIMARY_CURRENCY] + ([SECONDARY_CURRENCY] if SECONDARY_CURRENCY in currencies else [])
    hero_currencies = [PRIMARY_CURRENCY] + ([SECONDARY_CURRENCY] if SECONDARY_CURRENCY in currencies else [])
    chart_data = _chart_data(report, currencies)
    order_context = _monthly_order_context(report)
    customer_context = _customer_context(report, analytical_currencies)
    return {
        "report": report,
        "year": year,
        "today": current_date,
        "available_years": sorted(available_years, reverse=True),
        "is_ytd": as_of != date(year, 12, 31),
        "currencies": currencies,
        "hero_currencies": hero_currencies,
        "other_currencies": [currency for currency in currencies if currency not in hero_currencies],
        "analytical_currencies": analytical_currencies,
        "format_compact_amount": format_compact_amount,
        "format_count": format_count,
        "chart_data": chart_data,
        "order_context": order_context,
        "executive": _executive_context(report, order_context, currencies),
        "customer_context": customer_context,
        "receivable_context": {
            currency: _receivable_context(report, customer_context, currency)
            for currency in analytical_currencies
        },
        "product_context": {
            currency: _product_context(report, currency)
            for currency in analytical_currencies
        },
        "market_context": {
            currency: _market_context(report, currency)
            for currency in analytical_currencies
        },
        "shipment_context": _shipment_context(report, order_context, as_of),
        "coverage_context": _coverage_context(report, orders),
        "fx": fx_reference_service.current_usd_cny(),
    }
