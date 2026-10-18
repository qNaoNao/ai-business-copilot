"""Governed metric queries: validated intent -> parameterized SQL -> bounded result.

This is deliberately not a free-form SQL sandbox. Identifiers and expressions
come only from this versioned catalog; untrusted text is bound as data.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from src.database import DB_PATH, get_connection


METRICS = {
    "gmv": ("COALESCE(SUM(i.price), 0)", "金额单位未注明", "商品金额，不含运费"),
    "orders": ("COUNT(DISTINCT o.order_id)", "订单", "已交付订单去重数；跨品类不可相加"),
    "customers": ("COUNT(DISTINCT c.customer_unique_id)", "客户", "购买客户去重数；跨期间、品类不可相加，不是流量"),
    "items": ("COUNT(i.order_item_id)", "件", "已交付订单明细行数"),
    "freight": ("COALESCE(SUM(i.freight_value), 0)", "金额单位未注明", "已交付商品对应运费"),
    "aov": ("1.0 * SUM(i.price) / NULLIF(COUNT(DISTINCT o.order_id), 0)", "金额单位未注明/订单", "当前筛选商品 GMV / 含这些商品的去重订单数"),
    "avg_item_price": ("1.0 * SUM(i.price) / NULLIF(COUNT(i.order_item_id), 0)", "金额单位未注明/件", "GMV / 件数；不能直接证明降价"),
    "avg_items_per_order": ("1.0 * COUNT(i.order_item_id) / NULLIF(COUNT(DISTINCT o.order_id), 0)", "件/订单", "件数 / 去重订单数"),
    "freight_share_of_total_paid": ("1.0 * SUM(i.freight_value) / NULLIF(SUM(i.price) + SUM(i.freight_value), 0)", "比例（0–1）", "运费 / (GMV + 运费)，不是运费 / GMV"),
}
DIMENSIONS = {
    "day": "SUBSTR(o.order_purchase_timestamp, 1, 10)",
    "week": "DATE(o.order_purchase_timestamp, '-' || ((CAST(STRFTIME('%w', o.order_purchase_timestamp) AS INTEGER) + 6) % 7) || ' days')",
    "month": "SUBSTR(o.order_purchase_timestamp, 1, 7)",
    "category": "COALESCE(p.product_category_name_english, p.product_category_name, 'unknown')",
    "product_id": "i.product_id",
}
SCOPE_NOTES = [
    "仅已交付订单；按购买时间筛选，日期范围包含首尾两日。",
    "本工具按实际有记录的分组输出，不自动补零；无分组的空区间总额/数量为0，比率为null。",
    "周以周一为标签；首末周/月可能不完整，不能直接用总额推断日均变化。",
    "订单、客户跨品类或SKU不可相加；比率不能简单平均。",
    "币种未注明；不包含库存、流量、毛利或因果证据。",
]


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


CATALOG_VERSION = stable_hash({"metrics": METRICS, "dimensions": DIMENSIONS, "scope": SCOPE_NOTES,
                              "model": "delivered-order-item-v1; left-join unique product/customer keys"})[:16]


def catalog() -> dict:
    return {"version": CATALOG_VERSION,
            "metrics": [{"name": key, "unit": val[1], "definition": val[2]} for key, val in METRICS.items()],
            "dimensions": list(DIMENSIONS), "scope_notes": SCOPE_NOTES,
            "limits": {"dimensions": 2, "rows": 500, "date_span_days": 1096, "timeout_seconds": 5}}


def _names(value, allowed, name, maximum, minimum=0):
    if (not isinstance(value, list) or not minimum <= len(value) <= maximum
            or any(not isinstance(v, str) or v not in allowed for v in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{name}: use {minimum}–{maximum} unique catalog names")
    return list(value)


def validate_query(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("query must be an object")
    required = {"metrics", "dimensions", "start_date", "end_date"}
    allowed = required | {"category", "product_id", "limit", "order_by", "descending"}
    if set(value) - allowed or not required <= set(value):
        raise ValueError("query contains unknown fields or is missing required fields")
    result = dict(value)
    result["metrics"] = _names(value["metrics"], METRICS, "metrics", len(METRICS), 1)
    result["dimensions"] = _names(value["dimensions"], DIMENSIONS, "dimensions", 2)
    if len(set(result["dimensions"]) & {"day", "week", "month"}) > 1:
        raise ValueError("Select at most one time dimension")
    try:
        start, end = (date.fromisoformat(value[k]) for k in ("start_date", "end_date"))
        if any(value[k] != v.isoformat() for k, v in zip(("start_date", "end_date"), (start, end))):
            raise ValueError()
        if not 0 <= (end - start).days < 1096 or end == date.max:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("Use valid YYYY-MM-DD dates in order, spanning at most 1096 days") from None
    for key in ("category", "product_id"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 128):
            raise ValueError(f"{key} must be null or 1–128 characters")
        result[key] = item
    limit = value.get("limit", 100)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")
    result["limit"] = limit
    result["order_by"] = value.get("order_by") or (result["dimensions"] or result["metrics"])[0]
    if result["order_by"] not in result["dimensions"] + result["metrics"]:
        raise ValueError("order_by must be a selected dimension or metric")
    result["descending"] = value.get("descending", False)
    if type(result["descending"]) is not bool:
        raise ValueError("descending must be boolean")
    return result


@dataclass(frozen=True)
class QueryPlan:
    spec: dict
    sql: str
    parameters: tuple
    query_id: str


def compile_query(value: dict) -> QueryPlan:
    spec = validate_query(value)
    fields = [f'{DIMENSIONS[key]} AS "{key}"' for key in spec["dimensions"]]
    fields += [f'{METRICS[key][0]} AS "{key}"' for key in spec["metrics"]]
    predicates = ["o.order_status = 'delivered'", "o.order_purchase_timestamp >= ?", "o.order_purchase_timestamp < ?"]
    parameters = [spec["start_date"], (date.fromisoformat(spec["end_date"]) + timedelta(days=1)).isoformat()]
    for key, expression in [("category", DIMENSIONS["category"]), ("product_id", DIMENSIONS["product_id"])]:
        if spec[key] is not None:
            predicates.append(expression + " = ?")
            parameters.append(spec[key])
    sql = ("SELECT " + ", ".join(fields) + "\nFROM orders AS o\n"
           "JOIN order_items AS i ON i.order_id = o.order_id\n"
           "LEFT JOIN products AS p ON p.product_id = i.product_id\n"
           "LEFT JOIN customers AS c ON c.customer_id = o.customer_id\n"
           "WHERE " + " AND ".join(predicates))
    if spec["dimensions"]:
        sql += "\nGROUP BY " + ", ".join(f'"{key}"' for key in spec["dimensions"])
    order = [spec["order_by"]] + [k for k in spec["dimensions"] if k != spec["order_by"]]
    sql += "\nORDER BY " + ", ".join(f'"{k}" ' + ("DESC" if spec["descending"] else "ASC") for k in order)
    sql += "\nLIMIT ?"
    parameters.append(spec["limit"] + 1)  # one extra row detects truncation honestly
    return QueryPlan(spec, sql, tuple(parameters), stable_hash({"spec": spec, "catalog": CATALOG_VERSION}))


READ_COLUMNS = {
    "orders": {"order_id", "customer_id", "order_status", "order_purchase_timestamp"},
    "order_items": {"order_id", "order_item_id", "product_id", "price", "freight_value"},
    "products": {"product_id", "product_category_name", "product_category_name_english"},
    "customers": {"customer_id", "customer_unique_id"},
}


def authorize(action, table, column, database, source):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and database == "main" and column in READ_COLUMNS.get(table, set()):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and column in {"sum", "count", "coalesce", "nullif", "substr", "date", "strftime"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def source_version() -> str:
    path = Path(os.getenv("BUSINESS_DB_PATH") or DB_PATH).expanduser().resolve()
    stat = path.stat()
    # Metadata fingerprint, not a cryptographic hash of database contents.
    values = [str(path), stat.st_size, stat.st_mtime_ns]
    wal = Path(str(path) + "-wal")
    if wal.exists():
        info = wal.stat()
        values.extend([info.st_size, info.st_mtime_ns])
    return stable_hash(values)[:20]


def execute_query(value: dict, *, timeout_seconds=5.0, max_vm_steps=20_000_000) -> dict:
    plan = compile_query(value)
    if not 0 < timeout_seconds <= 5 or not 1 <= max_vm_steps <= 20_000_000:
        raise ValueError("Execution budgets may only be tightened")
    fingerprint = source_version()
    started, steps = time.perf_counter(), 0
    interval = min(1000, max_vm_steps)

    def progress():
        nonlocal steps
        steps += interval
        return int(steps >= max_vm_steps or time.perf_counter() - started >= timeout_seconds)

    with closing(get_connection()) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 1000")
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        conn.set_authorizer(authorize)
        conn.set_progress_handler(progress, interval)
        try:
            # Verify source grains before joining. Prevent silent metric inflation
            # if a replacement SQLite file has duplicate business keys.
            for table, keys in [("orders", ["order_id"]), ("products", ["product_id"]),
                                ("customers", ["customer_id"]), ("order_items", ["order_id", "order_item_id"])]:
                fields = ", ".join(keys)
                duplicate = conn.execute(
                    f"SELECT {fields} FROM {table} GROUP BY {fields} HAVING COUNT({keys[-1]}) > 1 LIMIT 1").fetchone()
                null = conn.execute(f"SELECT {fields} FROM {table} WHERE " +
                                    " OR ".join(f"{key} IS NULL" for key in keys) + " LIMIT 1").fetchone()
                if duplicate or null:
                    raise ValueError("Source grain contract failed: duplicate or null business key")
            explanation = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + plan.sql, plan.parameters)]
            cursor = conn.execute(plan.sql, plan.parameters)
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchmany(plan.spec["limit"] + 1)]
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise TimeoutError("Query execution budget exceeded; narrow the dates or dimensions") from None
            raise
    if time.perf_counter() - started >= timeout_seconds:
        raise TimeoutError("Query execution budget exceeded; narrow the dates or dimensions")
    if source_version() != fingerprint:
        raise RuntimeError("Source changed during the query; rerun against a stable snapshot")
    for row in rows:
        for key, val in row.items():
            if isinstance(val, float) and not math.isfinite(val):
                row[key] = None
    return {"query_id": plan.query_id, "spec": plan.spec, "sql": plan.sql,
            "parameters": list(plan.parameters), "catalog_version": CATALOG_VERSION,
            "source_version": fingerprint, "source_version_kind": "file-metadata fingerprint",
            "rows": rows[:plan.spec["limit"]], "truncated": len(rows) > plan.spec["limit"],
            "row_count": min(len(rows), plan.spec["limit"]), "query_plan": explanation,
            "source_contract": "unique, non-null join keys and (order_id, order_item_id) grain checked",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "definitions": {key: {"unit": METRICS[key][1], "definition": METRICS[key][2]} for key in plan.spec["metrics"]},
            "scope_notes": list(SCOPE_NOTES)}


def query_business_metrics(metrics, dimensions, start_date, end_date, category=None,
                           product_id=None, limit=100, order_by=None, descending=False):
    """Registry adapter: a single structured evidence packet, not raw SQL input."""
    import pandas as pd
    packet = execute_query(dict(metrics=metrics, dimensions=dimensions, start_date=start_date,
                                end_date=end_date, category=category, product_id=product_id,
                                limit=limit, order_by=order_by, descending=descending))
    return pd.DataFrame([packet])
