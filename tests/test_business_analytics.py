import math
import unittest

from src.database import DB_PATH
from src.tools.business_analytics import (
    compare_category,
    get_category_contribution,
    get_category_sku_contribution,
    get_monthly_kpis,
)
from src.tools.registry import call_tool, get_tool_definitions


BASE_MONTH = "2018-01"
COMPARE_MONTH = "2018-02"
CATEGORY = "bed_bath_table"
SKU_CATEGORY = "stationery"


@unittest.skipUnless(
    DB_PATH.exists(),
    f"Local analytics database not found: {DB_PATH}",
)
class BusinessAnalyticsSmokeTests(unittest.TestCase):
    def test_get_monthly_kpis(self):
        result = get_monthly_kpis(BASE_MONTH, COMPARE_MONTH)

        self.assertEqual(result["month"].tolist(), [BASE_MONTH, COMPARE_MONTH])
        self.assertTrue(
            {
                "gmv",
                "orders",
                "customers",
                "items",
                "freight",
                "days_in_month",
                "daily_gmv",
                "daily_orders",
                "daily_customers",
                "aov",
                "avg_items_per_order",
                "avg_item_price",
                "freight_share_of_total_paid",
            }.issubset(result.columns)
        )
        self.assertTrue((result["orders"] > 0).all())
        self.assertTrue((result["items"] > 0).all())
        self.assertEqual(result["days_in_month"].tolist(), [31, 28])

        for row in result.itertuples(index=False):
            self.assertAlmostEqual(
                row.daily_gmv,
                row.gmv / row.days_in_month,
            )
            self.assertAlmostEqual(
                row.daily_orders,
                row.orders / row.days_in_month,
            )
            self.assertAlmostEqual(
                row.daily_customers,
                row.customers / row.days_in_month,
            )
            self.assertAlmostEqual(row.aov, row.gmv / row.orders)
            self.assertAlmostEqual(
                row.avg_items_per_order,
                row.items / row.orders,
            )
            self.assertAlmostEqual(row.avg_item_price, row.gmv / row.items)
            self.assertAlmostEqual(
                row.freight_share_of_total_paid,
                row.freight / (row.gmv + row.freight),
            )

    def test_get_category_contribution(self):
        result = get_category_contribution(BASE_MONTH, COMPARE_MONTH)

        self.assertFalse(result.empty)
        self.assertTrue(
            {
                "category",
                "base_gmv",
                "compare_gmv",
                "gmv_change",
                "change_pct",
                "base_days_in_month",
                "compare_days_in_month",
                "base_daily_gmv",
                "compare_daily_gmv",
                "daily_gmv_change",
                "daily_change_pct",
            }.issubset(result.columns)
        )
        self.assertTrue(
            result["daily_gmv_change"].is_monotonic_increasing
        )
        self.assertTrue((result["base_days_in_month"] == 31).all())
        self.assertTrue((result["compare_days_in_month"] == 28).all())
        self.assertEqual(
            result.iloc[0]["category"],
            "stationery",
        )

        category = result.loc[result["category"] == CATEGORY].iloc[0]
        self.assertAlmostEqual(
            category["gmv_change"],
            category["compare_gmv"] - category["base_gmv"],
        )
        self.assertAlmostEqual(
            category["change_pct"],
            category["gmv_change"] / category["base_gmv"] * 100,
        )
        self.assertAlmostEqual(
            category["base_daily_gmv"],
            category["base_gmv"] / 31,
        )
        self.assertAlmostEqual(
            category["compare_daily_gmv"],
            category["compare_gmv"] / 28,
        )
        self.assertAlmostEqual(
            category["daily_change_pct"],
            (
                category["compare_daily_gmv"]
                - category["base_daily_gmv"]
            )
            / category["base_daily_gmv"]
            * 100,
        )

    def test_get_category_contribution_validates_options(self):
        with self.assertRaisesRegex(
            ValueError,
            "direction",
        ):
            get_category_contribution(
                BASE_MONTH,
                COMPARE_MONTH,
                direction="largest",
                limit=10,
            )

        with self.assertRaisesRegex(
            ValueError,
            "limit",
        ):
            get_category_contribution(
                BASE_MONTH,
                COMPARE_MONTH,
                direction="decline",
                limit=0,
            )

    def test_compare_category(self):
        result = compare_category(CATEGORY, BASE_MONTH, COMPARE_MONTH)

        self.assertEqual(result.columns.tolist(), ["Base", "Compare", "Change %"])
        self.assertTrue(
            {
                "gmv",
                "orders",
                "customers",
                "items",
                "avg_item_price",
                "days_in_month",
                "daily_gmv",
                "daily_orders",
                "daily_customers",
                "daily_items",
                "aov",
                "avg_items_per_order",
            }.issubset(result.index)
        )
        self.assertTrue(result.notna().all().all())
        self.assertEqual(result.loc["days_in_month", "Base"], 31)
        self.assertEqual(result.loc["days_in_month", "Compare"], 28)
        self.assertAlmostEqual(
            result.loc["daily_gmv", "Base"],
            result.loc["gmv", "Base"] / 31,
        )
        self.assertAlmostEqual(
            result.loc["daily_gmv", "Compare"],
            result.loc["gmv", "Compare"] / 28,
        )

        for metric in result.index:
            expected_change = (
                (result.loc[metric, "Compare"] - result.loc[metric, "Base"])
                / result.loc[metric, "Base"]
                * 100
            )
            self.assertTrue(math.isclose(
                result.loc[metric, "Change %"],
                expected_change,
                rel_tol=1e-9,
            ))

    def test_get_category_sku_contribution(self):
        result = get_category_sku_contribution(
            SKU_CATEGORY,
            BASE_MONTH,
            COMPARE_MONTH,
            direction="decline",
            limit=10,
        )

        self.assertEqual(len(result), 10)
        self.assertTrue(
            {
                "product_id",
                "base_gmv",
                "compare_gmv",
                "base_orders",
                "compare_orders",
                "base_items",
                "compare_items",
                "base_avg_item_price",
                "compare_avg_item_price",
                "base_daily_gmv",
                "compare_daily_gmv",
                "gmv_change",
                "daily_gmv_change",
                "daily_change_pct",
                "orders_change",
                "items_change",
                "avg_item_price_change_pct",
                "comparison_status",
            }.issubset(result.columns)
        )
        self.assertTrue(
            result["daily_gmv_change"].is_monotonic_increasing
        )

        top_decline = result.iloc[0]
        self.assertEqual(
            top_decline["product_id"],
            "5411e9269501a870cabf632f05655131",
        )
        self.assertAlmostEqual(top_decline["base_gmv"], 4515.0)
        self.assertAlmostEqual(top_decline["compare_gmv"], 0.0)
        self.assertEqual(top_decline["base_items"], 35)
        self.assertEqual(top_decline["compare_items"], 0)
        self.assertEqual(
            top_decline["comparison_status"],
            "not_sold_in_compare_month",
        )
        self.assertAlmostEqual(
            top_decline["daily_gmv_change"],
            -4515.0 / 31,
        )

    def test_get_category_sku_contribution_validates_options(self):
        with self.assertRaisesRegex(
            ValueError,
            "direction",
        ):
            get_category_sku_contribution(
                SKU_CATEGORY,
                BASE_MONTH,
                COMPARE_MONTH,
                direction="largest",
                limit=10,
            )

        with self.assertRaisesRegex(
            ValueError,
            "limit",
        ):
            get_category_sku_contribution(
                SKU_CATEGORY,
                BASE_MONTH,
                COMPARE_MONTH,
                direction="decline",
                limit=0,
            )

    def test_tool_registry_is_ready_for_an_agent(self):
        definitions = get_tool_definitions()
        names = {definition["name"] for definition in definitions}

        self.assertEqual(
            names,
            {
                "get_monthly_kpis",
                "get_category_contribution",
                "compare_category",
                "get_category_sku_contribution",
                "get_entity_time_trend",
                "search_business_knowledge",
            },
        )

        result = call_tool(
            "compare_category",
            {
                "category_name": CATEGORY,
                "base_month": BASE_MONTH,
                "compare_month": COMPARE_MONTH,
            },
        )
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["metric"], "gmv")


if __name__ == "__main__":
    unittest.main()
