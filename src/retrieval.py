"""Versioned business RAG: BM25 + local multilingual dense vectors + RRF.

No network/model download occurs during import or lexical search. Building the
dense index is an explicit CLI operation; stale/unavailable indexes fail back to
BM25 visibly. Retrieved text is evidence, never an instruction channel.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT / "knowledge_base"
INDEX_DIR = ROOT / "runs" / "knowledge_index"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
ALIASES = {
    "客单价": "aov 单均交易额", "average order value": "aov 客单价",
    "平均一单": "aov 客单价", "每张订单": "aov 订单",
    "shipping": "freight 运费", "运送费用": "freight 运费",
    "less days": "days_in_month 日均 月份比较", "calendar": "日均 月份比较",
    "天数不一样": "日均 月份比较", "二月更短": "日均 月份比较",
    "缺货": "sku 库存 证据", "stockout": "sku 库存 缺货",
    "delisting": "sku 下架", "零销量": "sku 没有销售",
    "currency": "币种", "人民币": "币种", "单位": "币种",
    "causality": "因果 证据", "correlation": "解释 证据边界",
    "建议": "管理建议", "recommendation": "管理建议",
}
STOP_TOKENS = set("关的 问题 什么 怎么 如何 可以 能否 是否 需要 一个 这些 我们 当前 不能 没有 只有 这个 数据 分析 a an the in of to and or is are do does can should we i with what how no not be these from for it".split())


@dataclass(frozen=True)
class Chunk:
    id: str
    chunk_id: str
    title: str
    content: str
    keywords: tuple[str, ...]
    source: str
    version: str
    citation: str


def tokenize(text: str) -> list[str]:
    text = text.casefold()
    tokens = re.findall(r"[a-z0-9_]+", text)
    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(span[i:i + 2] for i in range(max(1, len(span) - 1)))
    return [t for t in tokens if t not in STOP_TOKENS]


def expand_query(query: str) -> str:
    lower = query.casefold()
    return query + " " + " ".join(v for k, v in ALIASES.items() if k in lower)


def split_text(text: str, size: int = 800, overlap: int = 80) -> list[str]:
    if size <= overlap or overlap < 0:
        raise ValueError("chunk size must exceed overlap")
    if len(text) <= size:
        return [text.strip()] if text.strip() else []
    return [text[start:start + size].strip() for start in range(0, len(text), size - overlap)
            if text[start:start + size].strip()]


def load_chunks(directory: Path = KNOWLEDGE_DIR) -> list[Chunk]:
    entries = json.loads((directory / "business_rules.json").read_text(encoding="utf-8"))
    docs = [(e["id"], e["title"], e["content"], e["keywords"], "knowledge_base/business_rules.json")
            for e in entries]
    for path in sorted((directory / "documents").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ")
        docs.append((path.stem, title, text, [title], "knowledge_base/documents/" + path.name))
    if len({d[0] for d in docs}) != len(docs):
        raise ValueError("Duplicate knowledge document IDs")
    chunks = []
    for doc_id, title, body, keywords, source in docs:
        version = hashlib.sha256(body.encode()).hexdigest()[:12]
        for i, text in enumerate(split_text(body)):
            chunk_id = f"{doc_id}:{i}"
            chunks.append(Chunk(doc_id, chunk_id, title, text, tuple(keywords), source,
                                version, f"KB:{chunk_id}@{version}"))
    return chunks


def corpus_version(chunks: list[Chunk]) -> str:
    return hashlib.sha256(json.dumps([asdict(c) for c in chunks], ensure_ascii=False,
                                    sort_keys=True).encode()).hexdigest()


class Retriever:
    def __init__(self, chunks: list[Chunk] | None = None, *, dense: bool = False,
                 index_dir: Path = INDEX_DIR):
        self.chunks = load_chunks() if chunks is None else chunks
        self.version = corpus_version(self.chunks)
        self.index_dir = index_dir
        self.dense_enabled = dense
        self._embedder = None
        self._vectors = None
        self.dense_error = ""
        self._terms = [Counter(tokenize(c.title + " " + c.content + " " + " ".join(c.keywords)))
                       for c in self.chunks]
        self._lengths = [sum(t.values()) for t in self._terms]
        self._avg_len = sum(self._lengths) / max(1, len(self._lengths))
        self._df = Counter(t for terms in self._terms for t in terms)
        if dense:
            try:
                import numpy as np
                meta = json.loads((index_dir / "manifest.json").read_text())
                if meta["corpus_version"] != self.version or meta["model"] != MODEL:
                    raise ValueError("dense index is stale; rebuild it")
                self._vectors = np.load(index_dir / "vectors.npy", allow_pickle=False)
                if self._vectors.shape[0] != len(self.chunks):
                    raise ValueError("dense index row count mismatch")
            except (OSError, KeyError, ValueError) as exc:
                self.dense_error = str(exc)

    def lexical_scores(self, query: str) -> list[float]:
        terms = set(tokenize(expand_query(query)))
        scores = []
        for chunk, counts, length in zip(self.chunks, self._terms, self._lengths):
            overlap = terms & counts.keys()
            keyword_match = any(k.casefold() in query.casefold() for k in chunk.keywords)
            if len(overlap) < 2 and not keyword_match:
                scores.append(0.0)
                continue
            score = 0.0
            for term in overlap:
                tf = counts[term]
                idf = math.log(1 + (len(self.chunks) - self._df[term] + 0.5) / (self._df[term] + 0.5))
                score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / max(self._avg_len, 1)))
            # Exact domain-keyword matches preserve important metric identifiers.
            score += sum(3.0 for k in chunk.keywords if k.casefold() in query.casefold())
            scores.append(score)
        return scores

    def _dense_scores(self, query: str) -> list[float] | None:
        if self._vectors is None:
            return None
        try:
            import numpy as np
            from fastembed import TextEmbedding
            if self._embedder is None:
                self._embedder = TextEmbedding(model_name=MODEL, cache_dir=str(ROOT / ".cache" / "embeddings"),
                                              local_files_only=True, threads=2)
            vector = next(iter(self._embedder.query_embed(query)))
            norms = np.linalg.norm(self._vectors, axis=1) * np.linalg.norm(vector)
            return (self._vectors @ vector / np.maximum(norms, 1e-12)).tolist()
        except Exception as exc:
            self.dense_error = type(exc).__name__ + ": local embedding unavailable"
            return None

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
            raise ValueError("limit must be an integer between 1 and 5")
        lexical = self.lexical_scores(query)
        dense = self._dense_scores(query) if self.dense_enabled else None
        ranks = sorted(range(len(lexical)), key=lambda i: (-lexical[i], self.chunks[i].chunk_id))
        fused = {i: 1 / (60 + rank) for rank, i in enumerate(ranks, 1) if lexical[i] > 0}
        if dense is not None:
            for rank, i in enumerate(sorted(range(len(dense)), key=lambda i: -dense[i]), 1):
                # Threshold is a retrieval gate, NOT a calibrated probability.
                if dense[i] >= 0.45 or lexical[i] > 0:
                    fused[i] = fused.get(i, 0) + 1 / (60 + rank)
        rows = []
        seen = set()
        backend = "hybrid-bm25-minilm-rrf" if dense is not None else "bm25"
        if self.dense_enabled and dense is None:
            backend = "bm25-fallback"
        for i in sorted(fused, key=lambda i: (-fused[i], -lexical[i], self.chunks[i].chunk_id)):
            c = self.chunks[i]
            if c.chunk_id in seen:
                continue
            seen.add(c.chunk_id)
            row = asdict(c)
            row.pop("keywords")
            row.update(matched_keywords=", ".join(k for k in c.keywords if k.casefold() in query.casefold()),
                       score=round(fused[i], 6), retrieval_backend=backend,
                       corpus_version=self.version, semantic_score=None if dense is None else round(dense[i], 6))
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows


@lru_cache(maxsize=4)
def _cached_retriever(version: str, dense: bool, manifest_version: str) -> Retriever:
    return Retriever(dense=dense)


def get_retriever() -> Retriever:
    chunks = load_chunks()
    manifest = INDEX_DIR / "manifest.json"
    stamp = str(manifest.stat().st_mtime_ns) if manifest.exists() else "missing"
    return _cached_retriever(corpus_version(chunks), os.getenv("COPILOT_RETRIEVAL", "hybrid") == "hybrid", stamp)


def build_dense_index() -> dict[str, Any]:
    import numpy as np
    from fastembed import TextEmbedding
    chunks = load_chunks()
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(model_name=MODEL, cache_dir=str(ROOT / ".cache" / "embeddings"), threads=2)
    vectors = np.asarray(list(model.passage_embed(c.title + "\n" + c.content for c in chunks)), dtype="float32")
    manifest = {"model": MODEL, "corpus_version": corpus_version(chunks), "chunks": len(chunks),
                "dimension": int(vectors.shape[1])}
    np.save(INDEX_DIR / "vectors.npy", vectors, allow_pickle=False)
    (INDEX_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _cached_retriever.cache_clear()
    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build a local multilingual embedding index (no paid API).")
    parser.add_argument("--build", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(build_dense_index(), indent=2))
