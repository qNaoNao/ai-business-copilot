"""Golden SQL, boundary/security and existing-metric parity checks; no model API."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.demo_data import SKU, create_demo_database
from src.export_safety import safe_csv
from src.semantic import METRICS, authorize, catalog, compile_query, execute_query
from src.tools.business_analytics import get_monthly_kpis
from src.tools.registry import call_tool, get_tool_definitions


BASE = {"metrics": ["gmv", "orders", "customers", "items", "aov"], "dimensions": ["month"],
        "start_date": "2018-01-01", "end_date": "2018-02-28"}


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = create_demo_database(Path(self.temp.name) / "demo.db")
        env = patch.dict(os.environ, {"BUSINESS_DB_PATH": str(self.db)})
        env.start()
        self.addCleanup(env.stop)

    def test_golden_month_totals_exclude_cancelled(self):
        rows = execute_query(BASE)["rows"]
        self.assertEqual([r["gmv"] for r in rows], [860, 600])
        self.assertEqual([r["items"] for r in rows], [8, 4])
        self.assertEqual([r["orders"] for r in rows], [5, 3])
        self.assertEqual([r["customers"] for r in rows], [3, 2])
        self.assertEqual([r["aov"] for r in rows], [172, 200])

    def test_every_metric_matches_existing_month_tool(self):
        rows = execute_query({**BASE, "metrics": list(METRICS)})["rows"]
        old = get_monthly_kpis("2018-01", "2018-02").to_dict("records")
        for actual, expected in zip(rows, old):
            for name in METRICS:
                with self.subTest(metric=name, month=actual["month"]):
                    self.assertAlmostEqual(actual[name], expected[name], places=9)

    def test_golden_two_dimensional_and_filter(self):
        rows = execute_query({**BASE, "dimensions": ["month", "category"]})["rows"]
        self.assertEqual([r["gmv"] for r in rows], [200, 660, 500, 100])
        filtered = execute_query({**BASE, "category": "stationery", "product_id": SKU})["rows"]
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["gmv"], 600)

    def test_range_includes_end_day_excludes_next_day(self):
        rows = execute_query({**BASE, "start_date": "2018-02-28", "end_date": "2018-02-28"})["rows"]
        self.assertEqual(rows[0]["gmv"], 50)

    def test_week_starts_monday_and_does_not_claim_zero_filling(self):
        packet = execute_query({**BASE, "dimensions": ["week"], "product_id": SKU})
        self.assertEqual([r["week"] for r in packet["rows"]], ["2018-01-01", "2018-01-08", "2018-01-15"])
        self.assertIn("不自动补零", " ".join(packet["scope_notes"]))

    def test_empty_group_and_empty_total_have_distinct_meaning(self):
        self.assertEqual(execute_query({**BASE, "category": "missing"})["rows"], [])
        row = execute_query({**BASE, "dimensions": [], "category": "missing"})["rows"][0]
        self.assertEqual(row["gmv"], 0)
        self.assertEqual(row["orders"], 0)
        self.assertIsNone(row["aov"])

    def test_cross_category_order_and_customer_counts_not_additive(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO order_items VALUES('order-0',3,?,40,1)", ("00000000000000000000000000000003",))
        overall = execute_query({**BASE, "dimensions": []})["rows"][0]
        groups = execute_query({**BASE, "dimensions": ["category"]})["rows"]
        self.assertEqual(overall["orders"], 8)
        self.assertGreater(sum(r["orders"] for r in groups), overall["orders"])
        self.assertEqual(sum(r["gmv"] for r in groups), overall["gmv"])

    def test_duplicate_item_grain_is_rejected_before_reporting_inflated_gmv(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO order_items SELECT * FROM order_items WHERE order_id='order-0' AND order_item_id=1")
        with self.assertRaisesRegex(ValueError, "grain contract"):
            execute_query(BASE)

    def test_structured_metric_tool_runs_inside_persistent_model_loop_with_fake_client(self):
        from types import SimpleNamespace as NS
        from src.workflow import run_workflow
        args = {**BASE, "category": None, "product_id": None, "limit": 100, "order_by": None, "descending": False}
        responses = iter([
            NS(id="metric-r1", output_text="", status="completed", usage=None,
               output=[NS(type="function_call", name="query_business_metrics", arguments=json.dumps(args), call_id="m1")]),
            NS(id="metric-r2", output_text="查询结果见证据包，月度GMV为860和600。", output=[], status="completed", usage=None),
        ])
        requests = []
        def create(**request):
            requests.append(request)
            return next(responses)
        with patch.dict(os.environ, {"COPILOT_RETRIEVAL": "bm25"}):
            state = run_workflow("按月查询整体指标", session_id="metrics-integration", backend="openai",
                                 checkpoint_path=Path(self.temp.name) / "workflow.sqlite3", client=NS(responses=NS(create=create)))
        self.assertTrue(state["trace"][0]["output"]["ok"])
        self.assertEqual(state["trace"][0]["output"]["data"][0]["rows"][0]["gmv"], 860)
        self.assertEqual(requests[1]["previous_response_id"], "metric-r1")

    def test_truncation_sort_and_repeatable_identity(self):
        spec = {**BASE, "dimensions": ["category"], "limit": 1, "order_by": "gmv", "descending": True}
        first, second = execute_query(spec), execute_query(spec)
        self.assertTrue(first["truncated"])
        self.assertEqual(first["row_count"], 1)
        self.assertEqual(first["rows"][0]["category"], "stationery")
        self.assertEqual(first["query_id"], second["query_id"])
        self.assertEqual(first["source_version"], second["source_version"])

    def test_result_contains_auditable_lineage_without_local_path(self):
        result = execute_query(BASE)
        self.assertTrue(result["query_plan"])
        self.assertTrue(result["catalog_version"])
        self.assertNotIn(str(self.db), json.dumps(result))
        self.assertEqual(catalog()["limits"]["rows"], 500)

    def test_values_are_bound_not_interpolated(self):
        attack = "stationery' OR 1=1; DROP TABLE orders;--"
        plan = compile_query({**BASE, "category": attack})
        self.assertNotIn(attack, plan.sql)
        self.assertIn(attack, plan.parameters)
        self.assertEqual(execute_query({**BASE, "category": attack})["rows"], [])
        self.assertEqual(execute_query(BASE)["rows"][0]["gmv"], 860)

    def test_invalid_intent_matrix_is_rejected(self):
        changes = [
            {"sql": "DROP TABLE orders"}, {"metrics": ["SUM(price)"]},
            {"dimensions": ["customer_unique_id"]}, {"dimensions": ["month", "week"]},
            {"dimensions": ["month", "category", "product_id"]}, {"metrics": []},
            {"metrics": ["gmv", "gmv"]}, {"limit": True}, {"limit": 501},
            {"start_date": "2018-02-30"}, {"start_date": "2018-03-01"},
            {"start_date": "2014-01-01"}, {"end_date": "9999-12-31"},
            {"start_date": "20180101"}, {"category": ""}, {"product_id": "x" * 129},
            {"descending": 1}, {"order_by": "gmv; SELECT 1"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                compile_query({**BASE, **change})

    def test_authorizer_denies_write_attach_metadata_and_extension(self):
        statements = ["DELETE FROM orders", "DROP TABLE products", "ATTACH DATABASE ':memory:' AS other",
                      "SELECT name FROM sqlite_master", "SELECT load_extension('evil')", "PRAGMA table_info(orders)",
                      "SELECT randomblob(1000)", "CREATE TEMP TABLE secret(x)"]
        for sql in statements:
            with self.subTest(sql=sql), closing(sqlite3.connect(self.db)) as conn:
                conn.set_authorizer(authorize)
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(sql)

    def test_vm_budget_interrupts_and_connection_does_not_poison_next_run(self):
        with self.assertRaises(TimeoutError):
            execute_query(BASE, max_vm_steps=1)
        self.assertEqual(execute_query(BASE)["rows"][0]["gmv"], 860)

    def test_tool_schema_and_packet_adapter(self):
        args = {**BASE, "category": None, "product_id": None, "limit": 100, "order_by": None, "descending": False}
        packet = call_tool("query_business_metrics", args)[0]
        self.assertEqual(packet["rows"][0]["gmv"], 860)
        definitions = get_tool_definitions()
        spec = next(d for d in definitions if d["name"] == "query_business_metrics")
        self.assertEqual(set(spec["parameters"]["required"]), set(spec["parameters"]["properties"]))
        with self.assertRaises(ValueError):
            call_tool("query_business_metrics", {**args, "metrics": "DROP TABLE orders"})

    def test_csv_formula_guard_preserves_numeric_values(self):
        frame = pd.DataFrame([{"label": " =HYPERLINK(\"evil\")", "value": -12.5}, {"label": "@cmd", "value": 3}])
        text = safe_csv(frame).decode("utf-8-sig")
        self.assertIn("' =HYPERLINK", text)
        self.assertIn("'@cmd", text)
        self.assertIn("-12.5", text)
        self.assertEqual(frame.iloc[1]["label"], "@cmd")


if __name__ == "__main__":
    unittest.main()
