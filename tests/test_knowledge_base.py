import unittest

from src.tools.knowledge_base import (
    load_business_knowledge,
    search_business_knowledge,
)


class BusinessKnowledgeTests(unittest.TestCase):
    def test_loads_required_business_rules(self):
        entries = load_business_knowledge()
        entry_ids = {
            entry["id"]
            for entry in entries
        }

        self.assertEqual(len(entries), 6)
        self.assertTrue(
            {
                "core_kpi_definitions",
                "fair_month_comparison",
                "sku_data_scope",
                "evidence_boundaries",
                "management_recommendation_rules",
                "currency_and_capability_boundaries",
            }.issubset(entry_ids)
        )

    def test_retrieves_sku_scope_and_evidence_rules(self):
        result = search_business_knowledge(
            "SKU product_id 缺货和库存能否被证明？",
            limit=3,
        )

        self.assertFalse(result.empty)
        self.assertEqual(
            result.iloc[0]["id"],
            "sku_data_scope",
        )
        self.assertIn(
            "不能证明",
            result.iloc[0]["content"],
        )

    def test_retrieves_kpi_definitions(self):
        result = search_business_knowledge(
            "请解释GMV、AOV和运费占比的指标口径",
            limit=2,
        )

        self.assertEqual(
            result.iloc[0]["id"],
            "core_kpi_definitions",
        )
        self.assertIn(
            "freight_share_of_total_paid",
            result.iloc[0]["content"],
        )

    def test_retrieves_currency_and_capability_boundaries(self):
        result = search_business_knowledge(
            "币种 人民币 表名 字段名 负责人 能力边界",
            limit=5,
        )

        self.assertFalse(result.empty)
        self.assertEqual(
            result.iloc[0]["id"],
            "currency_and_capability_boundaries",
        )
        self.assertIn(
            "金额单位（数据未注明币种）",
            result.iloc[0]["content"],
        )
        self.assertIn(
            "不能声称这些信息已知或能够直接调取",
            result.iloc[0]["content"],
        )

    def test_returns_empty_result_for_unmatched_query(self):
        result = search_business_knowledge(
            "完全无关的天气问题",
            limit=3,
        )

        self.assertTrue(result.empty)
        self.assertEqual(
            result.columns.tolist(),
            [
                "id",
                "title",
                "content",
                "matched_keywords",
            ],
        )

    def test_validates_search_arguments(self):
        with self.assertRaisesRegex(
            ValueError,
            "query",
        ):
            search_business_knowledge(
                "",
                limit=3,
            )

        with self.assertRaisesRegex(
            ValueError,
            "limit",
        ):
            search_business_knowledge(
                "GMV",
                limit=0,
            )


if __name__ == "__main__":
    unittest.main()
