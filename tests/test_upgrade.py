"""New acceptance tests use synthetic SQL data and fake LLM responses: no API fees."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from src.demo_data import create_demo_database, SKU
from src.database import get_connection
from src.evidence import verify_answer
from src.retrieval import Retriever, load_chunks, corpus_version, split_text
from src.report_export import build_analysis_report
from src.tools.registry import call_tool
from src.workflow import run_workflow, list_workflow_sessions, DEMO_QUESTION, FOLLOWUP_QUESTION


def response(id, answer="", calls=(), input_tokens=10, output_tokens=5):
    return NS(id=id, output_text=answer, status="completed", output=[NS(type="function_call", name=n, arguments=json.dumps(a), call_id=c) for n, a, c in calls],
              usage=NS(input_tokens=input_tokens, output_tokens=output_tokens, input_tokens_details=NS(cached_tokens=2)))


class FakeClient:
    def __init__(self, responses):
        self.replies = iter(responses)
        self.requests = []
        self.responses = NS(create=self.create)
    def create(self, **request):
        self.requests.append(request)
        return next(self.replies)


class RetrievalUpgradeTests(unittest.TestCase):
    def test_citations_have_source_chunk_and_content_hash(self):
        rows = Retriever().search("SKU 库存", 3)
        self.assertTrue(rows)
        self.assertRegex(rows[0]["citation"], r"KB:[\w-]+:0@[a-f0-9]{12}")
        self.assertTrue(rows[0]["source"].startswith("knowledge_base/"))

    def test_unrelated_query_abstains(self):
        self.assertEqual(Retriever().search("完全无关的天气问题"), [])

    def test_paraphrase_retrieval_without_exact_aov(self):
        self.assertEqual(Retriever().search("每张订单平均花多少怎么计算")[0]["id"], "core_kpi_definitions")

    def test_missing_dense_index_falls_back_visibly(self):
        with tempfile.TemporaryDirectory() as temp:
            retriever = Retriever(dense=True, index_dir=Path(temp))
            self.assertTrue(retriever.dense_error)
            self.assertEqual(retriever.search("GMV")[0]["retrieval_backend"], "bm25-fallback")

    def test_stale_index_never_reuses_old_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "manifest.json").write_text(json.dumps({"corpus_version": "old", "model": "old"}))
            retriever = Retriever(dense=True, index_dir=path)
            self.assertIn("stale", retriever.dense_error)
            self.assertEqual(retriever.search("SKU")[0]["retrieval_backend"], "bm25-fallback")

    def test_chunking_and_version_invalidation(self):
        chunks = load_chunks()
        self.assertNotEqual(corpus_version(chunks), corpus_version(chunks[:-1]))
        self.assertEqual(len(split_text("a" * 1000)), 2)
        with self.assertRaises(ValueError): split_text("a", size=10, overlap=10)

    def test_invalid_search_limit(self):
        with self.assertRaises(ValueError): Retriever().search("GMV", True)


class WorkflowAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = create_demo_database(self.root / "business.db")
        self.checkpoints = self.root / "workflow.sqlite3"
        self.environment = patch.dict(os.environ, {"BUSINESS_DB_PATH": str(self.db), "COPILOT_RETRIEVAL": "bm25"})
        self.environment.start()
    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()
    def run_flow(self, question=DEMO_QUESTION, session="acceptance", **kwargs):
        return run_workflow(question, session_id=session, checkpoint_path=self.checkpoints, **kwargs)

    def test_original_private_database_is_never_overwritten(self):
        with self.assertRaises(FileExistsError): create_demo_database(self.db)

    def test_sql_connection_is_read_only(self):
        with get_connection() as conn:
            with self.assertRaises(sqlite3.OperationalError): conn.execute("DELETE FROM orders")

    def test_synthetic_kpis_have_known_totals_and_freight_denominator(self):
        rows = call_tool("get_monthly_kpis", {"start_month": "2018-01", "end_month": "2018-02"})
        self.assertEqual([r["gmv"] for r in rows], [860, 600])
        self.assertEqual([r["items"] for r in rows], [8, 4])
        self.assertEqual([r["days_in_month"] for r in rows], [31, 28])
        self.assertAlmostEqual(rows[0]["freight_share_of_total_paid"], 80 / 940)

    def test_free_two_turn_diagnosis_resolves_previous_sku(self):
        with patch("src.workflow.create_client", side_effect=AssertionError("No API in demo")):
            first = self.run_flow()
            second = self.run_flow(FOLLOWUP_QUESTION)
        self.assertEqual(first["entities"]["category"], "stationery")
        self.assertEqual(first["entities"]["sku"], SKU)
        self.assertEqual(first["tool_calls"], 3)
        self.assertEqual(second["trace"][0]["arguments"]["entity_id"], SKU)
        self.assertIn("2018-01-15 至 2018-01-21", second["answer"])
        self.assertEqual(len(second["messages"]), 4)
        self.assertEqual(second["usage"]["estimated_cost_usd"], 0)
        self.assertTrue(all(c["passed"] for c in first["checks"]))

    def test_restart_after_tools_does_not_repeat_completed_query(self):
        calls = []
        def spy(name, arguments):
            calls.append(name)
            return call_tool(name, arguments)
        initial = self.run_flow(interrupt_after=["tools"], tool_caller=spy)
        self.assertEqual(initial["status"], "interrupted")
        self.assertEqual(len(calls), 1)
        resumed = self.run_flow(None, tool_caller=spy)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(calls.count("get_category_contribution"), 1)
        self.assertEqual(len(calls), 3)
        self.assertTrue(list_workflow_sessions(self.checkpoints))
        cached = self.run_flow(None, tool_caller=lambda *_: self.fail("Already complete"))
        self.assertEqual(cached["answer"], resumed["answer"])

    def test_new_question_refused_until_pending_turn_is_recovered(self):
        self.run_flow(interrupt_after=["tools"])
        with self.assertRaisesRegex(ValueError, "未完成"): self.run_flow("新问题")

    def test_resume_refuses_database_snapshot_change(self):
        self.run_flow(interrupt_after=["tools"])
        with sqlite3.connect(self.db) as conn: conn.execute("INSERT INTO customers VALUES('new','new')")
        with self.assertRaisesRegex(ValueError, "数据库已变化"): self.run_flow(None)

    def test_session_path_traversal_and_backend_change_refused(self):
        with self.assertRaises(ValueError): self.run_flow(session="../escape")
        self.run_flow()
        with self.assertRaisesRegex(ValueError, "新会话"): self.run_flow(None, backend="openai")

    def test_tool_budget_returns_partial_evidence_instead_of_crashing(self):
        state = self.run_flow(max_tool_calls=1)
        self.assertEqual(state["status"], "partial")
        self.assertEqual(len(state["trace"]), 1)
        self.assertIn("预算", state["answer"])

    def test_bad_tool_arguments_fail_before_sql_execution(self):
        for name, args in [("shell", {}), ("get_monthly_kpis", {"start_month": "2018-13", "end_month": "2018-02"}),
                           ("get_monthly_kpis", {"start_month": "2018-01", "end_month": "2018-02", "sql": "DROP TABLE orders"}),
                           ("get_category_contribution", {"base_month": "2018-01", "compare_month": "2018-02", "limit": True})]:
            with self.assertRaises(ValueError): call_tool(name, args)

    def test_fake_openai_loop_tracks_tokens_limits_and_response_chain(self):
        citation = Retriever().search("GMV")[0]["citation"]
        client = FakeClient([response("r1", calls=[("get_monthly_kpis", {"start_month": "2018-01", "end_month": "2018-02"}, "c1")]),
                             response("r2", answer=f"GMV 为 860.00 → 600.00。 [{citation}]")])
        state = self.run_flow("整体经营表现 GMV", backend="openai", client=client)
        self.assertEqual(state["usage"]["input_tokens"], 20)
        self.assertEqual(state["usage"]["output_tokens"], 10)
        self.assertEqual(state["usage"]["cached_input_tokens"], 4)
        self.assertEqual(client.requests[1]["previous_response_id"], "r1")
        self.assertEqual(client.requests[0]["max_output_tokens"], 3500)
        self.assertIsNone(state["usage"]["estimated_cost_usd"])
        self.assertEqual(state["status"], "completed")

    def test_fake_openai_resume_after_tool_without_repeat(self):
        client = FakeClient([response("r1", calls=[("get_monthly_kpis", {"start_month": "2018-01", "end_month": "2018-02"}, "c1")]), response("r2", answer="保留工具证据。")])
        first = self.run_flow("整体经营表现", backend="openai", client=client, interrupt_after=["tools"])
        resumed = self.run_flow(None, backend="openai", client=client)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(resumed["tool_calls"], 1)
        self.assertEqual(first["status"], "interrupted")

    def test_model_budget_is_enforced(self):
        client = FakeClient([response("r1", calls=[("get_monthly_kpis", {"start_month": "2018-01", "end_month": "2018-02"}, "c1")])])
        state = self.run_flow("整体经营表现", backend="openai", client=client, max_model_calls=1)
        self.assertEqual(state["status"], "partial")
        self.assertEqual(len(client.requests), 1)

    def test_export_contains_new_audit_metadata(self):
        state = self.run_flow()
        report = build_analysis_report(state["messages"], session_id="acceptance", model="demo")
        self.assertIn("运行审计", report.markdown)
        self.assertIn("citation_integrity", report.html)
        self.assertIn("estimated_cost_usd", report.markdown)


class EvidenceTests(unittest.TestCase):
    def test_unknown_citation_and_currency_are_flagged(self):
        checks = verify_answer("GMV 100元。[KB:invented:0@aaaaaaaaaaaa]", [], [])
        self.assertFalse(checks[0]["passed"])
        self.assertFalse(checks[1]["passed"])

    def test_causal_assertion_and_negation_distinguished(self):
        bad = verify_answer("销量为零证明缺货。", [], [])
        good = verify_answer("销量为零不能证明缺货，需要验证库存。", [], [])
        self.assertFalse(bad[2]["passed"])
        self.assertTrue(good[2]["passed"])

    def test_unsupported_number_is_advisory_not_a_truth_guarantee(self):
        checks = verify_answer("GMV 999.99", [], [{"name": "get_monthly_kpis", "output": {"ok": True, "data": [{"gmv": 100}]}}])
        self.assertFalse(checks[-1]["passed"])
        self.assertEqual(checks[-1]["severity"], "advisory")


if __name__ == "__main__": unittest.main()
