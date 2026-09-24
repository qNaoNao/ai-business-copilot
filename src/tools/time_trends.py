"""Daily and weekly trend analysis for categories and SKU-level products."""

from datetime import datetime, timedelta

import pandas as pd

from src.database import get_connection


MAX_DATE_RANGE_DAYS = 366


def _parse_date(value, field_name):
    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format."
        )

    try:
        parsed = datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()
    except ValueError as error:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format."
        ) from error

    if parsed.isoformat() != value:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format."
        )

    return parsed


def _build_period_grid(
    start,
    end,
    granularity,
):
    if granularity == "day":
        period_keys = pd.date_range(
            start,
            end,
            freq="D",
        )
    else:
        first_monday = (
            start
            - timedelta(days=start.weekday())
        )
        last_monday = (
            end
            - timedelta(days=end.weekday())
        )
        period_keys = pd.date_range(
            first_monday,
            last_monday,
            freq="7D",
        )

    rows = []

    for period_key in period_keys:
        key_date = period_key.date()

        if granularity == "day":
            observed_start = key_date
            observed_end = key_date
            period_days = 1
            is_partial_period = False
        else:
            calendar_end = (
                key_date
                + timedelta(days=6)
            )
            observed_start = max(
                key_date,
                start,
            )
            observed_end = min(
                calendar_end,
                end,
            )
            period_days = (
                observed_end
                - observed_start
            ).days + 1
            is_partial_period = period_days < 7

        rows.append({
            "_period_key": key_date.isoformat(),
            "period_start": observed_start.isoformat(),
            "period_end": observed_end.isoformat(),
            "period_days": period_days,
            "is_partial_period": is_partial_period,
        })

    return pd.DataFrame(rows)


def get_entity_time_trend(
    entity_type,
    entity_id,
    start_date,
    end_date,
    granularity,
):
    """Return a zero-filled daily or weekly trend for a category or SKU."""

    if entity_type not in {"category", "sku"}:
        raise ValueError(
            "entity_type must be either 'category' or 'sku'."
        )

    if (
        not isinstance(entity_id, str)
        or not entity_id.strip()
    ):
        raise ValueError(
            "entity_id must be a non-empty string."
        )

    if granularity not in {"day", "week"}:
        raise ValueError(
            "granularity must be either 'day' or 'week'."
        )

    start = _parse_date(
        start_date,
        "start_date",
    )
    end = _parse_date(
        end_date,
        "end_date",
    )

    if end < start:
        raise ValueError(
            "end_date must be on or after start_date."
        )

    date_range_days = (
        end
        - start
    ).days + 1

    if date_range_days > MAX_DATE_RANGE_DAYS:
        raise ValueError(
            "Date range cannot exceed "
            f"{MAX_DATE_RANGE_DAYS} days."
        )

    if granularity == "day":
        period_expression = (
            "DATE(o.order_purchase_timestamp)"
        )
    else:
        period_expression = """
        DATE(
            o.order_purchase_timestamp,
            '-' || (
                (
                    CAST(
                        STRFTIME(
                            '%w',
                            o.order_purchase_timestamp
                        ) AS INTEGER
                    )
                    + 6
                )
                % 7
            ) || ' days'
        )
        """

    if entity_type == "category":
        entity_expression = """
        COALESCE(
            p.product_category_name_english,
            p.product_category_name,
            'unknown'
        )
        """
    else:
        entity_expression = "p.product_id"

    query = f"""
    SELECT
        {period_expression} AS period_key,

        SUM(oi.price) AS gmv,

        COUNT(
            DISTINCT o.order_id
        ) AS orders,

        COUNT(
            DISTINCT c.customer_unique_id
        ) AS customers,

        COUNT(*) AS items,

        AVG(oi.price) AS avg_item_price

    FROM orders AS o

    JOIN customers AS c
        ON o.customer_id = c.customer_id

    JOIN order_items AS oi
        ON o.order_id = oi.order_id

    JOIN products AS p
        ON oi.product_id = p.product_id

    WHERE o.order_status = 'delivered'

      AND DATE(
            o.order_purchase_timestamp
          ) BETWEEN ? AND ?

      AND {entity_expression} = ?

    GROUP BY period_key

    ORDER BY period_key;
    """

    existence_query = f"""
    SELECT 1
    FROM products AS p
    WHERE {entity_expression} = ?
    LIMIT 1;
    """

    with get_connection() as conn:
        exists = conn.execute(
            existence_query,
            [entity_id],
        ).fetchone()

        if not exists:
            raise ValueError(
                f"Unknown {entity_type}: {entity_id}"
            )

        result = pd.read_sql_query(
            query,
            conn,
            params=[
                start_date,
                end_date,
                entity_id,
            ],
        )

    period_grid = _build_period_grid(
        start,
        end,
        granularity,
    )

    result = result.rename(
        columns={
            "period_key": "_period_key",
        }
    )

    result = period_grid.merge(
        result,
        how="left",
        on="_period_key",
    )

    result["gmv"] = (
        result["gmv"]
        .fillna(0.0)
    )

    for column in [
        "orders",
        "customers",
        "items",
    ]:
        result[column] = (
            result[column]
            .fillna(0)
            .astype(int)
        )

    result["has_sales"] = (
        result["items"] > 0
    )

    orders_denominator = (
        result["orders"]
        .where(result["orders"] != 0)
    )

    items_denominator = (
        result["items"]
        .where(result["items"] != 0)
    )

    result["aov"] = (
        result["gmv"]
        / orders_denominator
    )

    result["avg_items_per_order"] = (
        result["items"]
        / orders_denominator
    )

    result["avg_item_price"] = (
        result["gmv"]
        / items_denominator
    )

    result["gmv_per_calendar_day"] = (
        result["gmv"]
        / result["period_days"]
    )

    result["orders_per_calendar_day"] = (
        result["orders"]
        / result["period_days"]
    )

    result["customers_per_calendar_day"] = (
        result["customers"]
        / result["period_days"]
    )

    result["items_per_calendar_day"] = (
        result["items"]
        / result["period_days"]
    )

    result.insert(
        0,
        "entity_type",
        entity_type,
    )
    result.insert(
        1,
        "entity_id",
        entity_id,
    )
    result.insert(
        2,
        "granularity",
        granularity,
    )

    return result.drop(
        columns=["_period_key"]
    )
