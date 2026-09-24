import json
from pathlib import Path
import tempfile
import unittest

from src.run_history import (
    group_history_sessions,
    history_records_to_messages,
    load_run_history,
    save_run_safely,
)


class RunHistoryReadTests(unittest.TestCase):
    def test_loads_valid_records_and_skips_corrupt_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "history.jsonl"
            history_path.write_text(
                json.dumps({"question": "Valid question"})
                + "\nnot-json\n[]\n",
                encoding="utf-8",
            )

            records = load_run_history(history_path)

        self.assertEqual(records, [{"question": "Valid question"}])

    def test_groups_sessions_newest_first_and_preserves_legacy_runs(self):
        records = [
            {
                "timestamp_utc": "2026-01-01T01:00:00+00:00",
                "session_id": "session-a",
                "turn_number": 1,
                "question": "First question",
                "model": "test-model",
            },
            {
                "timestamp_utc": "2026-01-01T02:00:00+00:00",
                "session_id": "session-a",
                "turn_number": 2,
                "question": "Follow-up",
                "model": "test-model",
            },
            {
                "timestamp_utc": "2026-01-02T01:00:00+00:00",
                "session_id": None,
                "question": "Legacy question",
            },
        ]

        sessions = group_history_sessions(records)

        self.assertEqual(len(sessions), 2)
        self.assertTrue(sessions[0]["is_legacy"])
        self.assertEqual(sessions[1]["session_id"], "session-a")
        self.assertEqual(sessions[1]["turn_count"], 2)
        self.assertEqual(sessions[1]["preview"], "First question")

    def test_converts_success_and_error_records_to_chat_messages(self):
        trace = [{
            "name": "get_monthly_kpis",
            "output": {"ok": True, "data": [{"month": "2018-01"}]},
        }]
        messages = history_records_to_messages([
            {
                "status": "success",
                "turn_number": 1,
                "question": "What changed?",
                "answer": "GMV changed.",
                "tool_trace": trace,
            },
            {
                "status": "error",
                "turn_number": 2,
                "question": "Why?",
                "error": "Tool failed",
                "tool_trace": [],
            },
        ])

        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["content"], "GMV changed.")
        self.assertEqual(messages[1]["tool_trace"], trace)
        self.assertIn("Tool failed", messages[3]["content"])

    def test_safe_save_reports_filesystem_errors_without_raising(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory_path = Path(temp_dir) / "not-a-file"
            directory_path.mkdir()

            saved_path, error = save_run_safely(
                question="What changed?",
                answer="An answer that should still be shown.",
                tool_trace=[],
                model="test-model",
                history_path=directory_path,
            )

        self.assertIsNone(saved_path)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
