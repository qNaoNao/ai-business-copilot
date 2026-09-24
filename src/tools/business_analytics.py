import pandas as pd

from src.database import get_connection


def get_monthly_kpis(start_month, end_month):
    """Return monthly totals and calendar-day-normalized business KPIs."""

    query = """
    SELECT
        SUBSTR(
            o.order_purchase_timestamp,
            1,
            7
        ) AS month,

        SUM(f.product_value) AS gmv,

        COUNT(DISTINCT o.order_id) AS orders,

        COUNT(DISTINCT c.customer_unique_id) AS customers,

        SUM(f.item_count) AS items,

        SUM(f.freight_value) AS freight

    FROM orders AS o

    JOIN customers AS c
        ON o.customer_id = c.customer_id

    JOIN order_financials AS f
        ON o.order_id = f.order_id

    WHERE o.order_status = 'delivered'

      AND SUBSTR(
            o.order_purchase_timestamp,
            1,
            7
          ) BETWEEN ? AND ?

    GROUP BY month

    ORDER BY month;
    """

    with get_connection() as conn:
        result = pd.read_sql_query(
            query,
            conn,
            params=[
                start_month,
                end_month,
            ],
        )

    if result.empty:
        return result

    month_dates = pd.to_datetime(
        result["month"],
        format="%Y-%m",
    )

    result["days_in_month"] = (
        month_dates.dt.days_in_month
    )

    result["daily_gmv"] = (
        result["gmv"]
        / result["days_in_month"]
    )

    result["daily_orders"] = (
        result["orders"]
        / result["days_in_month"]
    )

    result["daily_customers"] = (
        result["customers"]
        / result["days_in_month"]
    )

    result["aov"] = (
        result["gmv"]
        / result["orders"]
    )

    result["avg_items_per_order"] = (
        result["items"]
        / result["orders"]
    )

    result["avg_item_price"] = (
        result["gmv"]
        / result["items"]
    )

    result["freight_share_of_total_paid"] = (
        result["freight"]
        / (
            result["gmv"]
            + result["freight"]
        )
    )

    return result


def get_category_contribution(
    base_month,
    compare_month,
    direction="decline",
    limit=10,
):
    """Compare category-level total and daily GMV between two months."""

    if direction not in {"decline", "growth"}:
        raise ValueError(
            "direction must be either 'decline' or 'growth'."
        )

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 25
    ):
        raise ValueError(
            "limit must be an integer between 1 and 25."
        )

    query = """
    SELECT
        COALESCE(
            p.product_category_name_english,
            p.product_category_name,
            'unknown'
        ) AS category,

        SUM(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN oi.price
                ELSE 0
            END
        ) AS base_gmv,

        SUM(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN oi.price
                ELSE 0
            END
        ) AS compare_gmv

    FROM orders AS o

    JOIN order_items AS oi
        ON o.order_id = oi.order_id

    JOIN products AS p
        ON oi.product_id = p.product_id

    WHERE o.order_status = 'delivered'

      AND SUBSTR(
            o.order_purchase_timestamp,
            1,
            7
          ) IN (?, ?)

    GROUP BY category;
    """

    params = [
        base_month,
        compare_month,
        base_month,
        compare_month,
    ]

    with get_connection() as conn:
        result = pd.read_sql_query(
            query,
            conn,
            params=params,
        )

    result["gmv_change"] = (
        result["compare_gmv"]
        - result["base_gmv"]
    )

    denominator = (
        result["base_gmv"]
        .where(result["base_gmv"] != 0)
    )

    result["change_pct"] = (
        result["gmv_change"]
        / denominator
        * 100
    )

    result["base_days_in_month"] = pd.Period(
        base_month,
        freq="M",
    ).days_in_month

    result["compare_days_in_month"] = pd.Period(
        compare_month,
        freq="M",
    ).days_in_month

    result["base_daily_gmv"] = (
        result["base_gmv"]
        / result["base_days_in_month"]
    )

    result["compare_daily_gmv"] = (
        result["compare_gmv"]
        / result["compare_days_in_month"]
    )

    result["daily_gmv_change"] = (
        result["compare_daily_gmv"]
        - result["base_daily_gmv"]
    )

    daily_denominator = (
        result["base_daily_gmv"]
        .where(result["base_daily_gmv"] != 0)
    )

    result["daily_change_pct"] = (
        result["daily_gmv_change"]
        / daily_denominator
        * 100
    )

    ascending = direction == "decline"

    return (
        result.sort_values(
            ["daily_gmv_change", "category"],
            ascending=[ascending, True],
        )
        .head(limit)
        .reset_index(drop=True)
    )


def get_category_sku_contribution(
    category_name,
    base_month,
    compare_month,
    direction="decline",
    limit=10,
):
    """Return the SKU-level drivers of a category's monthly GMV change."""

    if direction not in {"decline", "growth"}:
        raise ValueError(
            "direction must be either 'decline' or 'growth'."
        )

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 25
    ):
        raise ValueError(
            "limit must be an integer between 1 and 25."
        )

    query = """
    SELECT
        p.product_id,

        SUM(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN oi.price
                ELSE 0
            END
        ) AS base_gmv,

        SUM(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN oi.price
                ELSE 0
            END
        ) AS compare_gmv,

        COUNT(
            DISTINCT CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN o.order_id
            END
        ) AS base_orders,

        COUNT(
            DISTINCT CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN o.order_id
            END
        ) AS compare_orders,

        SUM(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN 1
                ELSE 0
            END
        ) AS base_items,

        SUM(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN 1
                ELSE 0
            END
        ) AS compare_items,

        AVG(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN oi.price
            END
        ) AS base_avg_item_price,

        AVG(
            CASE
                WHEN SUBSTR(
                    o.order_purchase_timestamp,
                    1,
                    7
                ) = ?
                THEN oi.price
            END
        ) AS compare_avg_item_price

    FROM orders AS o

    JOIN order_items AS oi
        ON o.order_id = oi.order_id

    JOIN products AS p
        ON oi.product_id = p.product_id

    WHERE o.order_status = 'delivered'

      AND COALESCE(
            p.product_category_name_english,
            p.product_category_name,
            'unknown'
          ) = ?

      AND SUBSTR(
            o.order_purchase_timestamp,
            1,
            7
          ) IN (?, ?)

    GROUP BY p.product_id;
    """

    params = [
        base_month,
        compare_month,
        base_month,
        compare_month,
        base_month,
        compare_month,
        base_month,
        compare_month,
        category_name,
        base_month,
        compare_month,
    ]

    with get_connection() as conn:
        result = pd.read_sql_query(
            query,
            conn,
            params=params,
        )

    if result.empty:
        raise ValueError(
            f"No SKU data found for category: "
            f"{category_name}"
        )

    base_days = pd.Period(
        base_month,
        freq="M",
    ).days_in_month

    compare_days = pd.Period(
        compare_month,
        freq="M",
    ).days_in_month

    result["base_daily_gmv"] = (
        result["base_gmv"]
        / base_days
    )

    result["compare_daily_gmv"] = (
        result["compare_gmv"]
        / compare_days
    )

    result["gmv_change"] = (
        result["compare_gmv"]
        - result["base_gmv"]
    )

    result["daily_gmv_change"] = (
        result["compare_daily_gmv"]
        - result["base_daily_gmv"]
    )

    base_daily_denominator = (
        result["base_daily_gmv"]
        .where(result["base_daily_gmv"] != 0)
    )

    result["daily_change_pct"] = (
        result["daily_gmv_change"]
        / base_daily_denominator
        * 100
    )

    result["orders_change"] = (
        result["compare_orders"]
        - result["base_orders"]
    )

    result["items_change"] = (
        result["compare_items"]
        - result["base_items"]
    )

    base_price_denominator = (
        result["base_avg_item_price"]
        .where(result["base_avg_item_price"] != 0)
    )

    result["avg_item_price_change_pct"] = (
        (
            result["compare_avg_item_price"]
            - result["base_avg_item_price"]
        )
        / base_price_denominator
        * 100
    )

    result["comparison_status"] = "continuing"
    result.loc[
        (
            (result["base_items"] > 0)
            & (result["compare_items"] == 0)
        ),
        "comparison_status",
    ] = "not_sold_in_compare_month"
    result.loc[
        (
            (result["base_items"] == 0)
            & (result["compare_items"] > 0)
        ),
        "comparison_status",
    ] = "new_in_compare_month"

    ascending = direction == "decline"

    return (
        result.sort_values(
            ["daily_gmv_change", "product_id"],
            ascending=[ascending, True],
        )
        .head(limit)
        .reset_index(drop=True)
    )


def compare_category(
    category_name,
    base_month,
    compare_month,
):
    """Diagnose a category with both total and daily-normalized metrics."""

    query = """
    SELECT
        SUBSTR(
            o.order_purchase_timestamp,
            1,
            7
        ) AS month,

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

      AND COALESCE(
            p.product_category_name_english,
            p.product_category_name,
            'unknown'
          ) = ?

      AND SUBSTR(
            o.order_purchase_timestamp,
            1,
            7
          ) IN (?, ?)

    GROUP BY month

    ORDER BY month;
    """

    params = [
        category_name,
        base_month,
        compare_month,
    ]

    with get_connection() as conn:
        result = pd.read_sql_query(
            query,
            conn,
            params=params,
        )

    if result.empty:
        raise ValueError(
            f"No data found for category: "
            f"{category_name}"
        )

    month_dates = pd.to_datetime(
        result["month"],
        format="%Y-%m",
    )

    result["days_in_month"] = (
        month_dates.dt.days_in_month
    )

    result["daily_gmv"] = (
        result["gmv"]
        / result["days_in_month"]
    )

    result["daily_orders"] = (
        result["orders"]
        / result["days_in_month"]
    )

    result["daily_customers"] = (
        result["customers"]
        / result["days_in_month"]
    )

    result["daily_items"] = (
        result["items"]
        / result["days_in_month"]
    )

    result["aov"] = (
        result["gmv"]
        / result["orders"]
    )

    result["avg_items_per_order"] = (
        result["items"]
        / result["orders"]
    )

    result = result.set_index("month")

    if (
        base_month not in result.index
        or compare_month not in result.index
    ):
        raise ValueError(
            "The selected category has no data "
            "in one of the selected months."
        )

    base = result.loc[base_month]

    compare = result.loc[compare_month]

    denominator = base.where(base != 0)

    comparison = pd.DataFrame({
        "Base": base,
        "Compare": compare,
        "Change %": (
            (compare - base)
            / denominator
            * 100
        ),
    })

    return comparison
