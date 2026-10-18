"""Local analysis snapshots, replay specifications, review feedback and audit.

Single workspace. Not tenant isolation, a model-quality score, or a tamper-proof
audit ledger. Never persists API tokens, customer rows, or arbitrary user SQL.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from src.semantic import execute_query, stable_hash, validate_query

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "runs" / "analyses.sqlite3"


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class AnalysisStore:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("COPILOT_ANALYSIS_STORE") or DEFAULT_STORE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS analyses(
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                payload TEXT NOT NULL, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback(
                id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES analyses(id),
                verdict TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
                id TEXT PRIMARY KEY, action TEXT NOT NULL, outcome TEXT NOT NULL,
                query_id TEXT, row_count INTEGER, latency_ms REAL, created_at TEXT NOT NULL);
            """)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def event(self, action, outcome, *, query_id=None, row_count=None, latency_ms=None):
        with closing(self._connect()) as conn, conn:
            self._insert_event(conn, action, outcome, query_id, row_count, latency_ms)

    def _insert_event(self, conn, action, outcome, query_id=None, row_count=None, latency_ms=None):
        conn.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?)",
                     (uuid4().hex, action, outcome, query_id, row_count, latency_ms, self._now()))

    def run(self, spec):
        started = time.perf_counter()
        try:
            result = execute_query(spec)
        except Exception:
            self.event("query", "error", latency_ms=(time.perf_counter() - started) * 1000)
            raise
        self.event("query", "ok", query_id=result["query_id"], row_count=result["row_count"], latency_ms=result["latency_ms"])
        return result

    def save(self, title, result):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
            raise ValueError("Title must contain 1–100 characters")
        if not isinstance(result, dict) or not {"spec", "rows", "source_version", "catalog_version", "query_id"} <= result.keys():
            raise ValueError("A successful governed-query result is required")
        validate_query(result["spec"])
        payload = _json(result)
        if len(payload.encode()) > 1_000_000:
            raise ValueError("Snapshot exceeds 1 MB")
        identifier = uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO analyses VALUES(?,?,?,?,?)",
                         (identifier, title.strip(), self._now(), payload, stable_hash(result)))
            self._insert_event(conn, "save", "ok", result["query_id"])
        return identifier

    def list(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("List limit must be 1–100")
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id,title,created_at FROM analyses ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]

    def get(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32:
            raise KeyError("Analysis not found")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM analyses WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError("Analysis not found")
            record = dict(row)
            result = json.loads(record.pop("payload"))
            if stable_hash(result) != record["digest"]:
                raise ValueError("Snapshot integrity check failed")
            record["result"] = result
            record["feedback"] = [dict(r) for r in conn.execute(
                "SELECT verdict,note,created_at FROM feedback WHERE analysis_id=? ORDER BY created_at", (identifier,))]
        return record

    def replay(self, identifier):
        original = self.get(identifier)["result"]
        # Deliberately executes again; never relabels a cached snapshot as a live result.
        result = self.run(original["spec"])
        return {"result": result,
                "source_changed": original["source_version"] != result["source_version"],
                "catalog_changed": original["catalog_version"] != result["catalog_version"],
                "rows_changed": original["rows"] != result["rows"]}

    def review(self, identifier, verdict, note=""):
        if verdict not in {"verified", "needs_review"} or not isinstance(note, str) or len(note) > 1000:
            raise ValueError("Use verified/needs_review and a note of at most 1000 characters")
        self.get(identifier)
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO feedback VALUES(?,?,?,?,?)",
                         (uuid4().hex, identifier, verdict, note, self._now()))
            self._insert_event(conn, "review", "ok")

    def stats(self):
        with closing(self._connect()) as conn:
            summary = [dict(row) for row in conn.execute(
                "SELECT action,outcome,COUNT(*) AS count,ROUND(AVG(latency_ms),3) AS avg_latency_ms "
                "FROM audit GROUP BY action,outcome ORDER BY action,outcome")]
            recent = [dict(row) for row in conn.execute(
                "SELECT action,outcome,query_id,row_count,latency_ms,created_at FROM audit ORDER BY created_at DESC LIMIT 30")]
        return {"summary": summary, "recent": recent, "scope": "local workspace; event latency, not an SLA or model accuracy"}
