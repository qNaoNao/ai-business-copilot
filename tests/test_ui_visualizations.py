import unittest

from src.ui_visualizations import (
    build_tool_visualization,
    build_tool_visualizations,
)


class UiVisualizationTests(unittest.TestCase):
    def test_builds_monthly_kpi_cards_with_deltas(self):
        visualization = build_tool_visualization({
            "name": "get_monthly_kpis",
            "arguments": {
                "start_month": "2018-01",
                "end_month": "2018-02",
            },
            "output": {
                "ok": True,
                "data": [
                    {
                        "month": "2018-01",
                        "gmv": 100.0,
                        "orders": 10,
                        "customers": 8,
                        "aov": 10.0,
                        "daily_gmv": 3.23,
                        "days_in_month": 31,
                    },
                    {
                        "month": "2018-02",
                        "gmv": 90.0,
                        "orders": 9,
                        "customers": 9,
                        "aov": 10.0,
                        "daily_gmv": 3.21,
                        "days_in_month": 28,
                    },
                ],
            },
        })

        self.assertIsNotNone(visualization)
        self.assertEqual(visualization["kind"], "monthly_kpis")
        self.assertEqual(visualization["cards"][0]["value"], "90.00")
        self.assertEqual(
            visualization["cards"][0]["delta"],
            "-10.00% vs 2018-01",
        )
        self.assertEqual(
            visualization["cards"][2]["delta"],
            "+12.50% vs 2018-01",
        )

    def test_builds_category_and_sku_contribution_charts(self):
        category = build_tool_visualization({
            "name": "get_category_contribution",
            "output": {
                "ok": True,
                "data": [{
                    "category": "stationery",
                    "base_daily_gmv": 1274.93,
                    "compare_daily_gmv": 360.38,
                    "daily_gmv_change": -914.55,
                    "daily_change_pct": -71.73,
                }],
            },
        })
        sku = build_tool_visualization({
            "name": "get_category_sku_contribution",
            "output": {
                "ok": True,
                "data": [{
                    "product_id": "5411e9269501a870cabf632f05655131",
                    "base_daily_gmv": 145.65,
                    "compare_daily_gmv": 0,
                    "daily_gmv_change": -145.65,
                    "daily_change_pct": -100,
                    "comparison_status": "not_sold_in_compare_month",
                }],
            },
        })

        self.assertEqual(category["entity"], "category")
        self.assertEqual(category["rows"][0]["label"], "stationery")
        self.assertEqual(sku["entity"], "sku")
        self.assertEqual(sku["rows"][0]["label"], "5411e926…")
        self.assertEqual(
            sku["rows"][0]["status"],
            "not_sold_in_compare_month",
        )

    def test_builds_time_trend_and_skips_unsupported_tools(self):
        trend_call = {
            "name": "get_entity_time_trend",
            "arguments": {
                "entity_type": "sku",
                "entity_id": "sku-1",
                "granularity": "week",
            },
            "output": {
                "ok": True,
                "data": [{
                    "period_start": "2018-01-01",
                    "period_end": "2018-01-07",
                    "gmv": 903.0,
                    "orders": 7,
                    "items": 7,
                    "is_partial_period": False,
                }],
            },
        }
        visualizations = build_tool_visualizations([
            {
                "name": "search_business_knowledge",
                "output": {"ok": True, "data": [{"id": "rule"}]},
            },
            trend_call,
        ])

        self.assertEqual(len(visualizations), 1)
        self.assertEqual(visualizations[0]["kind"], "time_trend")
        self.assertEqual(
            visualizations[0]["rows"][0]["gmv"],
            903.0,
        )

    def test_builds_category_comparison_cards(self):
        visualization = build_tool_visualization({
            "name": "compare_category",
            "arguments": {
                "category_name": "stationery",
                "base_month": "2018-01",
                "compare_month": "2018-02",
            },
            "output": {
                "ok": True,
                "data": [
                    {
                        "metric": "daily_gmv",
                        "Base": 100.0,
                        "Compare": 75.0,
                    },
                    {
                        "metric": "aov",
                        "Base": 10.0,
                        "Compare": 8.0,
                    },
                ],
            },
        })

        self.assertEqual(visualization["kind"], "category_comparison")
        self.assertEqual(len(visualization["cards"]), 2)
        self.assertEqual(
            visualization["cards"][0]["delta"],
            "-25.00% vs 2018-01",
        )


if __name__ == "__main__":
    unittest.main()
