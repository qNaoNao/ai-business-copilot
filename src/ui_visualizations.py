"""Prepare chart and KPI-card payloads from successful tool traces."""

import math
from typing import Any


def _number(value: Any) -> float | None:
    """Return a finite float, or None for missing/non-numeric values."""

    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None

    return converted if math.isfinite(converted) else None


def _percent_delta(base: Any, compare: Any) -> float | None:
    """Calculate percentage change when the base value is usable."""

    base_number = _number(base)
    compare_number = _number(compare)

    if base_number in (None, 0) or compare_number is None:
        return None

    return (compare_number - base_number) / base_number * 100


def _card(
    label: str,
    value: Any,
    *,
    base_value: Any,
    base_label: str,
    number_format: str,
) -> dict[str, Any]:
    """Create one presentation-neutral KPI card description."""

    numeric_value = _number(value)
    delta = _percent_delta(base_value, value)

    return {
        "label": label,
        "value": (
            number_format.format(numeric_value)
            if numeric_value is not None
            else "—"
        ),
        "delta": (
            f"{delta:+.2f}% vs {base_label}"
            if delta is not None
            else None
        ),
    }


def _monthly_kpis(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable_rows = sorted(
        (row for row in rows if row.get("month")),
        key=lambda row: str(row["month"]),
    )
    if not usable_rows:
        return None

    base = usable_rows[0]
    compare = usable_rows[-1]
    base_label = str(base["month"])
    compare_label = str(compare["month"])

    return {
        "kind": "monthly_kpis",
        "title": f"月度经营概览：{base_label} → {compare_label}",
        "cards": [
            _card(
                "月度 GMV",
                compare.get("gmv"),
                base_value=base.get("gmv"),
                base_label=base_label,
                number_format="{:,.2f}",
            ),
            _card(
                "订单数",
                compare.get("orders"),
                base_value=base.get("orders"),
                base_label=base_label,
                number_format="{:,.0f}",
            ),
            _card(
                "客户数",
                compare.get("customers"),
                base_value=base.get("customers"),
                base_label=base_label,
                number_format="{:,.0f}",
            ),
            _card(
                "AOV",
                compare.get("aov"),
                base_value=base.get("aov"),
                base_label=base_label,
                number_format="{:,.2f}",
            ),
        ],
        "rows": usable_rows,
    }


def _category_comparison(
    rows: list[dict[str, Any]],
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    rows_by_metric = {
        row.get("metric"): row
        for row in rows
        if row.get("metric")
    }
    selected = [
        ("日均 GMV", "daily_gmv", "{:,.2f}"),
        ("日均订单", "daily_orders", "{:,.2f}"),
        ("AOV", "aov", "{:,.2f}"),
        ("平均商品价格", "avg_item_price", "{:,.2f}"),
    ]
    if not any(metric in rows_by_metric for _, metric, _ in selected):
        return None

    base_label = str(arguments.get("base_month", "基期"))
    compare_label = str(arguments.get("compare_month", "比较期"))
    category = str(arguments.get("category_name", "所选品类"))
    cards = []

    for label, metric, number_format in selected:
        row = rows_by_metric.get(metric)
        if row:
            cards.append(_card(
                label,
                row.get("Compare"),
                base_value=row.get("Base"),
                base_label=base_label,
                number_format=number_format,
            ))

    return {
        "kind": "category_comparison",
        "title": f"{category}：{base_label} → {compare_label}",
        "cards": cards,
    }


def _contribution(
    rows: list[dict[str, Any]],
    *,
    entity: str,
) -> dict[str, Any] | None:
    identifier = "category" if entity == "category" else "product_id"
    prepared_rows = []

    for row in rows:
        change = _number(row.get("daily_gmv_change"))
        entity_id = row.get(identifier)
        if change is None or not entity_id:
            continue

        label = str(entity_id)
        if entity == "sku" and len(label) > 12:
            label = f"{label[:8]}…"

        prepared_rows.append({
            "label": label,
            "full_label": str(entity_id),
            "daily_gmv_change": change,
            "base_daily_gmv": _number(row.get("base_daily_gmv")),
            "compare_daily_gmv": _number(row.get("compare_daily_gmv")),
            "daily_change_pct": _number(row.get("daily_change_pct")),
            "status": row.get("comparison_status"),
        })

    if not prepared_rows:
        return None

    return {
        "kind": "contribution",
        "title": (
            "品类日均 GMV 变化"
            if entity == "category"
            else "SKU 日均 GMV 变化"
        ),
        "entity": entity,
        "rows": prepared_rows,
    }


def _time_trend(
    rows: list[dict[str, Any]],
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    prepared_rows = []

    for row in rows:
        if not row.get("period_start"):
            continue

        prepared_rows.append({
            "period_start": row["period_start"],
            "period_end": row.get("period_end"),
            "gmv": _number(row.get("gmv")) or 0.0,
            "orders": _number(row.get("orders")) or 0.0,
            "items": _number(row.get("items")) or 0.0,
            "is_partial_period": bool(row.get("is_partial_period")),
        })

    if not prepared_rows:
        return None

    entity_type = arguments.get("entity_type", "entity")
    entity_id = str(arguments.get("entity_id", ""))
    granularity = arguments.get("granularity", "week")
    entity_label = "SKU" if entity_type == "sku" else "品类"
    grain_label = "周" if granularity == "week" else "日"

    return {
        "kind": "time_trend",
        "title": f"{entity_label} {entity_id} 的{grain_label}趋势",
        "rows": prepared_rows,
    }


def build_tool_visualization(
    tool_call: dict[str, Any],
) -> dict[str, Any] | None:
    """Translate one supported successful tool call into a UI payload."""

    output = tool_call.get("output", {})
    rows = output.get("data") if output.get("ok") else None
    if not isinstance(rows, list) or not rows:
        return None
    if not all(isinstance(row, dict) for row in rows):
        return None

    tool_name = tool_call.get("name")
    arguments = tool_call.get("arguments", {})

    if tool_name == "get_monthly_kpis":
        return _monthly_kpis(rows)
    if tool_name == "compare_category":
        return _category_comparison(rows, arguments)
    if tool_name == "get_category_contribution":
        return _contribution(rows, entity="category")
    if tool_name == "get_category_sku_contribution":
        return _contribution(rows, entity="sku")
    if tool_name == "get_entity_time_trend":
        return _time_trend(rows, arguments)

    return None


def build_tool_visualizations(
    tool_trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return supported visualizations in tool-call order."""

    visualizations = []

    for tool_call in tool_trace:
        visualization = build_tool_visualization(tool_call)
        if visualization:
            visualizations.append(visualization)

    return visualizations
