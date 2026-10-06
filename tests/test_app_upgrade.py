"""Headless UI acceptance: isolated synthetic DB/checkpoints/history, no API."""
from functools import partial
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from src.demo_data import create_demo_database
from src.run_history import save_run_safely
from src.workflow import ROOT, run_workflow, list_workflow_sessions


class AppUpgradeTests(unittest.TestCase):
    def test_free_drilldown_followup_and_reload_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = create_demo_database(root / "business.db")
            checkpoints, history = root / "workflow.sqlite3", root / "history.jsonl"
            with patch.dict(os.environ, {"BUSINESS_DB_PATH": str(db), "COPILOT_RETRIEVAL": "bm25"}), \
                 patch("src.workflow.run_workflow", partial(run_workflow, checkpoint_path=checkpoints)), \
                 patch("src.workflow.list_workflow_sessions", partial(list_workflow_sessions, checkpoint_path=checkpoints)), \
                 patch("src.run_history.save_run_safely", partial(save_run_safely, history_path=history)), \
                 patch("src.run_history.load_run_history", return_value=[]), \
                 patch("src.workflow.create_client", side_effect=AssertionError("Paid API forbidden")):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                self.assertFalse(app.exception)
                next(b for b in app.button if b.label == "品类→SKU下钻").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.session_state["messages"]), 2)
                next(b for b in app.button if b.label == "沿用该SKU看周趋势").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.session_state["messages"]), 4)
                session = app.session_state["session_id"]
                # New browser session loads persisted conversation, not only current session_state.
                reloaded = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                next(b for b in reloaded.button if b.label == "恢复并继续该会话").click().run()
                self.assertFalse(reloaded.exception)
                self.assertEqual(reloaded.session_state["session_id"], session)
                self.assertEqual(len(reloaded.session_state["messages"]), 4)

    def test_paid_mode_requires_explicit_ui_opt_in(self):
        with patch("src.workflow.create_client", side_effect=AssertionError("Paid API forbidden")):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            app.radio[0].set_value("OpenAI（付费模型）").run()
            self.assertFalse(app.exception)
            self.assertTrue(app.chat_input[0].disabled)


if __name__ == "__main__": unittest.main()
