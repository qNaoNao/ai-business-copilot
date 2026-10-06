"""API-free retrieval comparison on a small checked-in labelled case set."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from src.retrieval import ROOT, Retriever, MODEL
from src.tools.knowledge_base import search_keyword_baseline


def evaluate_retrieval(include_dense=False):
    cases = json.loads((ROOT / "evals" / "retrieval_cases.json").read_text())
    systems = {"keyword_baseline": lambda q: search_keyword_baseline(q, 3).to_dict("records"),
               "bm25": Retriever().search}
    if include_dense:
        dense = Retriever(dense=True)
        # Warm-up excluded from latency; fail explicitly, never label a fallback as dense.
        dense.search("SKU", 3)
        if dense.dense_error or dense._embedder is None:
            raise RuntimeError("Dense evaluation unavailable: " + dense.dense_error)
        systems["hybrid"] = dense.search
    rows = []
    for name, search in systems.items():
        for case in cases:
            start = time.perf_counter()
            result = search(case["query"])
            ids = [r["id"] for r in result[:3]]
            expected = set(case["expected"])
            ranks = [i for i, value in enumerate(ids, 1) if value in expected]
            rows.append({"system": name, "id": case["id"], "split": case["split"], "expected": case["expected"], "retrieved": ids,
                         "hit_at_3": bool(ranks) if expected else None, "rr": 1 / min(ranks) if ranks else 0,
                         "correct_abstention": not ids if not expected else None,
                         "latency_ms": round((time.perf_counter() - start) * 1000, 3)})
    summaries = []
    for name in systems:
        for split in ["development", "holdout"]:
            subset = [r for r in rows if r["system"] == name and r["split"] == split]
            positive = [r for r in subset if r["expected"]]
            negatives = [r for r in subset if not r["expected"]]
            latencies = sorted(r["latency_ms"] for r in subset)
            summaries.append({"system": name, "split": split, "positive_cases": len(positive), "negative_cases": len(negatives),
                              "hit_at_3": sum(r["hit_at_3"] for r in positive) / len(positive),
                              "mrr_at_3": sum(r["rr"] for r in positive) / len(positive),
                              "abstention_accuracy": sum(r["correct_abstention"] for r in negatives) / len(negatives),
                              "median_latency_ms": latencies[len(latencies) // 2]})
    return {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "dense_model": MODEL if include_dense else None,
            "notes": "24 human-authored cases, 6 short business rules; not a production accuracy claim. Gate/aliases selected on development only. Hit@3 counts any labelled relevant document, not all relevant documents. Negative cases measured separately.",
            "summaries": summaries, "cases": rows}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dense", action="store_true")
    args = parser.parse_args()
    report = evaluate_retrieval(args.dense)
    directory = ROOT / "docs" / "evaluation"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "retrieval_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summaries"], ensure_ascii=False, indent=2))
