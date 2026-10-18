"""Snapshots survive restarts, replay honestly, and retain non-destructive reviews."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.analysis_store import AnalysisStore
from src.demo_data import create_demo_database
from src.semantic import execute_query

SPEC = {"metrics": ["gmv", "orders"], "dimensions": ["month"],
        "start_date": "2018-01-01", "end_date": "2018-02-28"}


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = create_demo_database(Path(temp.name) / "demo.db")
        self.path = Path(temp.name) / "analyses.sqlite3"
        self.store = AnalysisStore(self.path)
        env = patch.dict(os.environ, {"BUSINESS_DB_PATH": str(self.db)})
        env.start()
        self.addCleanup(env.stop)

    def test_snapshot_survives_restart_and_load_never_executes_sql(self):
        result = self.store.run(SPEC)
        identifier = self.store.save("经营分析", result)
        with patch("src.analysis_store.execute_query", side_effect=AssertionError("No query on snapshot load")):
            loaded = AnalysisStore(self.path).get(identifier)
        self.assertEqual(loaded["result"], result)
        self.assertEqual(self.store.list()[0]["id"], identifier)

    def test_replay_detects_data_change_without_overwriting_snapshot(self):
        identifier = self.store.save("baseline", self.store.run(SPEC))
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE order_items SET price=price+10 WHERE order_id='order-0'")
        replay = self.store.replay(identifier)
        self.assertTrue(replay["source_changed"])
        self.assertTrue(replay["rows_changed"])
        self.assertFalse(replay["catalog_changed"])
        self.assertEqual(self.store.get(identifier)["result"]["rows"][0]["gmv"], 860)
        self.assertEqual(replay["result"]["rows"][0]["gmv"], 880)

    def test_snapshot_integrity_failure_is_not_silently_accepted(self):
        identifier = self.store.save("snapshot", self.store.run(SPEC))
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE analyses SET digest='bad' WHERE id=?", (identifier,))
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.store.get(identifier)

    def test_reviews_are_append_only_not_accuracy_claims(self):
        identifier = self.store.save("review", self.store.run(SPEC))
        self.store.review(identifier, "needs_review", "检查去重口径")
        self.store.review(identifier, "verified", "与SQL结果核对")
        reviews = self.store.get(identifier)["feedback"]
        self.assertEqual([r["verdict"] for r in reviews], ["needs_review", "verified"])
        self.assertEqual(reviews[1]["note"], "与SQL结果核对")

    def test_invalid_saved_results_and_reviews_are_rejected(self):
        for title in ["", "x" * 101, 1]:
            with self.assertRaises(ValueError):
                self.store.save(title, {})
        with self.assertRaises(ValueError):
            self.store.save("untrusted", {})
        with self.assertRaises(KeyError):
            self.store.get("../secret")
        with self.assertRaises(ValueError):
            self.store.review("a" * 32, "100% accurate")
        with self.assertRaises(KeyError):
            self.store.review("a" * 32, "verified")

    def test_failure_and_success_audits_contain_no_raw_inputs(self):
        self.store.run(SPEC)
        with self.assertRaises(ValueError):
            self.store.run({**SPEC, "sql": "private input"})
        stats = self.store.stats()
        self.assertEqual({r["outcome"] for r in stats["summary"]}, {"ok", "error"})
        self.assertNotIn("private input", str(stats))
        self.assertNotIn(str(self.db), str(stats))

    def test_concurrent_saves_keep_all_snapshots(self):
        result = execute_query(SPEC)
        with ThreadPoolExecutor(max_workers=4) as pool:
            identifiers = list(pool.map(lambda i: self.store.save(f"analysis-{i}", result), range(12)))
        self.assertEqual(len(set(identifiers)), 12)
        self.assertEqual(len(self.store.list()), 12)


if __name__ == "__main__":
    unittest.main()
