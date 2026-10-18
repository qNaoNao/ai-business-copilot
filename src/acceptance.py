"""Run API-free tests and emit a machine-readable, skip-aware CI acceptance record."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import unittest


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}

    def startTest(self, test):
        super().startTest(test)
        self.records[test.id()] = {"test": test.id(), "status": "running", "started": time.perf_counter()}

    def _status(self, test, status):
        self.records.setdefault(test.id(), {"test": test.id()})["status"] = status

    def addSuccess(self, test):
        super().addSuccess(test)
        self._status(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._status(test, "failed")

    def addError(self, test, err):
        super().addError(test, err)
        self._status(test, "error")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._status(test, "skipped")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._status(test, "expected_failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._status(test, "unexpected_success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._status(test, "failed")

    def stopTest(self, test):
        record = self.records[test.id()]
        record["elapsed_ms"] = round((time.perf_counter() - record.pop("started")) * 1000, 3)
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("runs/acceptance.json"))
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    records = list(result.records.values())
    report = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "API-free unit, real synthetic SQL, mock model, HTTP and Streamlit-widget checks; not live LLM quality or Docker runtime",
              "successful": result.wasSuccessful(), "tests_run": result.testsRun,
              "passed": sum(r["status"] == "passed" for r in records),
              "skipped": sum(r["status"] == "skipped" for r in records),
              "failed": sum(r["status"] in {"failed", "error", "unexpected_success"} for r in records),
              "tests": records}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
