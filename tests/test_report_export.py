from datetime import datetime, timezone
import unittest

from src.report_export import build_analysis_report


class ReportExportTests(unittest.TestCase):
    def test_builds_markdown_with_conversation_and_tool_evidence(self):
        report = build_analysis_report(
            [
                {
                    "role": "user",
                    "content": "比较两个月。",
                },
                {
                    "role": "assistant",
                    "content": "**GMV** 有所下降。",
                    "turn_number": 1,
                    "tool_trace": [{
                        "name": "get_monthly_kpis",
                        "arguments": {
                            "start_month": "2018-01",
                            "end_month": "2018-02",
                        },
                        "output": {
                            "ok": True,
                            "data": [{
                                "month": "2018-01",
                                "gmv": 100.0,
                            }],
                        },
                    }],
                },
            ],
            session_id="session/123",
            model="test-model",
            generated_at=datetime(
                2026,
                9,
                24,
                tzinfo=timezone.utc,
            ),
        )

        self.assertIn("# AI Business Copilot 分析报告", report.markdown)
        self.assertIn("比较两个月。", report.markdown)
        self.assertIn("`get_monthly_kpis`", report.markdown)
        self.assertIn("| month | gmv |", report.markdown)
        self.assertIn("| 2018-01 | 100.0 |", report.markdown)
        self.assertEqual(
            report.markdown_file_name,
            "business_copilot_report_session_123.md",
        )

    def test_builds_safe_standalone_html_with_tables(self):
        report = build_analysis_report(
            [
                {
                    "role": "user",
                    "content": "<script>alert('x')</script>",
                },
                {
                    "role": "assistant",
                    "content": "结论。",
                    "tool_trace": [{
                        "name": "tool",
                        "arguments": {},
                        "output": {
                            "ok": True,
                            "data": [{"metric": "gmv", "value": 100}],
                        },
                    }],
                },
            ],
            session_id="session-1",
            model="test-model",
        )

        self.assertTrue(report.html.startswith("<!doctype html>"))
        self.assertIn("<table>", report.html)
        self.assertIn("&lt;script&gt;", report.html)
        self.assertNotIn("<script>alert", report.html)
        self.assertEqual(
            report.html_file_name,
            "business_copilot_report_session-1.html",
        )

    def test_includes_failed_tool_as_an_evidence_note(self):
        report = build_analysis_report(
            [{
                "role": "assistant",
                "content": "未完成。",
                "tool_trace": [{
                    "name": "broken_tool",
                    "arguments": {},
                    "output": {"ok": False, "error": "bad input"},
                }],
            }],
            session_id="session-2",
            model="test-model",
        )

        self.assertIn("工具执行失败：bad input", report.markdown)


if __name__ == "__main__":
    unittest.main()
