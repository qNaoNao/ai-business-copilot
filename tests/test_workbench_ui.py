"""Browser-widget acceptance of preview, execution, persistence and replay."""
from functools import partial
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import streamlit as st

from src.demo_data import create_demo_database
from src.workflow import ROOT


class WorkbenchTests(unittest.TestCase):
    def test_preview_execute_save_restart_snapshot_replay_and_review(self):
        self.assertTrue(st.get_option("client.disableDataExport"))
        with tempfile.TemporaryDirectory() as temp:
            db = create_demo_database(Path(temp) / "demo.db")
            env = {"BUSINESS_DB_PATH": str(db), "COPILOT_ANALYSIS_STORE": str(Path(temp) / "analyses.db"), "COPILOT_RETRIEVAL": "bm25"}
            with patch.dict(os.environ, env), \
                 patch("src.workflow.list_workflow_sessions", return_value=[]), \
                 patch("src.run_history.load_run_history", return_value=[]), \
                 patch("src.workflow.create_client", side_effect=AssertionError("No paid API")):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                next(s for s in app.selectbox if s.label == "工作区").set_value("数据探索与分析资产").run()
                self.assertFalse(app.exception)
                next(b for b in app.button if b.label == "预览查询").click().run()
                self.assertFalse(app.exception)
                self.assertNotIn("wb_result", app.session_state)
                next(b for b in app.button if b.label == "执行受控查询").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["wb_result"]["row_count"], 4)
                next(b for b in app.button if b.label == "保存分析快照").click().run()
                self.assertFalse(app.exception)
                reloaded = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                next(s for s in reloaded.selectbox if s.label == "工作区").set_value("数据探索与分析资产").run()
                next(b for b in reloaded.button if b.label == "查看快照（不重新查询）").click().run()
                self.assertFalse(reloaded.exception)
                self.assertTrue(reloaded.session_state["wb_is_snapshot"])
                next(b for b in reloaded.button if b.label == "用当前数据重新执行").click().run()
                self.assertFalse(reloaded.exception)
                self.assertFalse(reloaded.session_state["wb_is_snapshot"])
                self.assertFalse(reloaded.session_state["wb_replay_comparison"]["rows_changed"])
                next(b for b in reloaded.button if b.label == "记录复核意见").click().run()
                self.assertFalse(reloaded.exception)

    def test_invalid_query_is_visible_and_does_not_leave_a_stale_result(self):
        with tempfile.TemporaryDirectory() as temp:
            db = create_demo_database(Path(temp) / "demo.db")
            with patch.dict(os.environ, {"BUSINESS_DB_PATH": str(db), "COPILOT_ANALYSIS_STORE": str(Path(temp) / "store.db")}), \
                 patch("src.workflow.list_workflow_sessions", return_value=[]), patch("src.run_history.load_run_history", return_value=[]):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                next(s for s in app.selectbox if s.label == "工作区").set_value("数据探索与分析资产").run()
                next(x for x in app.text_input if x.key == "wb_start").set_value("wrong")
                next(b for b in app.button if b.label == "执行受控查询").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.error)
                self.assertNotIn("wb_result", app.session_state)


if __name__ == "__main__":
    unittest.main()
