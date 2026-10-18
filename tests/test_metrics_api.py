"""HTTP contract, authentication, roles, limits and server-side evidence; no API key."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from src.api import create_app
from src.demo_data import create_demo_database

TOKEN = "test-analyst-token-" + "x" * 32
VIEWER = "test-viewer-token-" + "y" * 32
SPEC = {"metrics": ["gmv", "orders"], "dimensions": ["month"],
        "start_date": "2018-01-01", "end_date": "2018-02-28"}


class APITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.db = create_demo_database(self.path / "demo.db")
        env = patch.dict(os.environ, {"BUSINESS_DB_PATH": str(self.db), "COPILOT_API_TOKEN": "", "COPILOT_VIEWER_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(create_app(token=TOKEN, viewer_token=VIEWER, store_path=self.path / "store.db"))
        self.client.headers["Authorization"] = "Bearer " + TOKEN
        self.addCleanup(self.client.close)

    def test_no_token_configuration_fails_closed(self):
        for config in [{"token": ""}, {"token": "short"}, {"token": TOKEN, "viewer_token": TOKEN}]:
            with self.subTest(config=list(config)), self.assertRaises(ValueError):
                create_app(**config, store_path=self.path / "unused.db")

    def test_health_is_public_and_discloses_no_data(self):
        response = self.client.get("/healthz", headers={"Authorization": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["paid_model_endpoints"])
        self.assertNotIn(str(self.db), response.text)

    def test_unknown_or_missing_token_cannot_read_or_query(self):
        for path in ["/v1/catalog", "/v1/analyses", "/v1/audit"]:
            self.assertEqual(self.client.get(path, headers={"Authorization": ""}).status_code, 401)
        response = self.client.post("/v1/query", json=SPEC, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)

    def test_query_preview_and_live_result(self):
        preview = self.client.post("/v1/query/preview", json=SPEC)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("LIMIT ?", preview.json()["data"]["sql"])
        response = self.client.post("/v1/query", json=SPEC)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["rows"][0]["gmv"], 860)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_viewer_can_query_but_cannot_save_review_or_read_audit(self):
        headers = {"Authorization": "Bearer " + VIEWER}
        self.assertEqual(self.client.post("/v1/query", json=SPEC, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/v1/analyses", json={"title": "x", "query": SPEC}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/v1/analyses/" + "a" * 32 + "/feedback", json={"verdict": "verified"}, headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/v1/audit", headers=headers).status_code, 403)

    def test_save_load_replay_review_and_list(self):
        saved = self.client.post("/v1/analyses", json={"title": "月度", "query": SPEC}).json()["data"]
        identifier = saved["id"]
        path = "/v1/analyses/" + identifier
        self.assertEqual(self.client.get(path).json()["data"]["result"], saved["result"])
        self.assertEqual(self.client.get("/v1/analyses").json()["data"]["analyses"][0]["id"], identifier)
        self.assertFalse(self.client.post(path + "/replay", json={}).json()["data"]["rows_changed"])
        self.assertEqual(self.client.post(path + "/feedback", json={"verdict": "needs_review", "note": "口径核对"}).status_code, 200)
        self.assertEqual(len(self.client.get(path).json()["data"]["feedback"]), 1)

    def test_client_cannot_submit_fabricated_evidence(self):
        response = self.client.post("/v1/analyses", json={"title": "fake", "result": {"rows": [{"gmv": 999}]}})
        self.assertEqual(response.status_code, 400)

    def test_bad_json_oversized_body_invalid_dates_and_sql(self):
        cases = [self.client.post("/v1/query", content="bad", headers={"Content-Type": "application/json"}),
                 self.client.post("/v1/query", content="x" * 16385, headers={"Content-Type": "application/json"}),
                 self.client.post("/v1/query", json={**SPEC, "start_date": "2018-02-30"}),
                 self.client.post("/v1/query", json={**SPEC, "sql": "DROP TABLE orders"}),
                 self.client.post("/v1/query", content="{}", headers={"Content-Type": "text/plain"})]
        self.assertEqual([r.status_code for r in cases], [400] * 5)

    def test_rate_limit_is_explicit_and_per_role(self):
        with TestClient(create_app(token=TOKEN, viewer_token=VIEWER, store_path=self.path / "rate.db", requests_per_minute=1)) as client:
            self.assertEqual(client.get("/v1/catalog", headers={"Authorization": "Bearer " + TOKEN}).status_code, 200)
            self.assertEqual(client.get("/v1/catalog", headers={"Authorization": "Bearer " + TOKEN}).status_code, 429)
            self.assertEqual(client.get("/v1/catalog", headers={"Authorization": "Bearer " + VIEWER}).status_code, 200)

    def test_http_errors_do_not_leak_database_path(self):
        with patch.dict(os.environ, {"BUSINESS_DB_PATH": str(self.path / "missing-private.db")}):
            response = self.client.post("/v1/query", json=SPEC)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("missing-private", response.text)
        self.assertNotIn(TOKEN, response.text)

    def test_missing_analysis_is_404(self):
        self.assertEqual(self.client.get("/v1/analyses/" + "a" * 32).status_code, 404)


if __name__ == "__main__":
    unittest.main()
