"""Small, provider-neutral registry for analytics tools.

The business functions stay independent from any agent framework. An agent layer can
use ``get_tool_definitions`` to advertise them and ``call_tool`` to execute a selected
tool.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import inspect
import re
from typing import Any

import pandas as pd
from jsonschema import Draft202012Validator

from src.semantic import DIMENSIONS, METRICS, query_business_metrics

from src.tools.business_analytics import (
    compare_category,
    get_category_contribution,
    get_category_sku_contribution,
    get_monthly_kpis,
)
from src.tools.knowledge_base import (
    search_business_knowledge,
)
from src.tools.time_trends import (
    get_entity_time_trend,
)


MONTH_SCHEMA = {
    "type": "string",
    "description": "Calendar month in YYYY-MM format.",
    "pattern": r"^\d{4}-(0[1-9]|1[0-2])$",
}

DATE_SCHEMA = {
    "type": "string",
    "description": "Calendar date in YYYY-MM-DD format.",
    "pattern": (
        r"^\d{4}-(0[1-9]|1[0-2])-"
        r"(0[1-9]|[12]\d|3[01])$"
    ),
}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    function: Callable[..., pd.DataFrame]
    parameters: dict[str, Any]

    def as_definition(self) -> dict[str, Any]:
        """Return an OpenAI Responses API function-tool definition."""

        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }


TOOLS = (
    ToolSpec(
        name="query_business_metrics",
        description=(
            "Explore approved business metrics by day, Monday-based week, month, "
            "category or product_id (at most two dimensions). Choose metric names, "
            "never SQL. Returns ONE evidence packet containing rows, definitions, "
            "parameterized SQL, versions, truncation and scope notes. Only delivered "
            "orders; distinct orders/customers are non-additive across categories. "
            "No zero filling or automatic daily normalization. Prefer existing "
            "comparison/contribution tools for fair month comparisons and ranked declines."
        ),
        function=query_business_metrics,
        parameters={
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string", "enum": list(METRICS)}, "minItems": 1, "maxItems": len(METRICS), "uniqueItems": True},
                "dimensions": {"type": "array", "items": {"type": "string", "enum": list(DIMENSIONS)}, "maxItems": 2, "uniqueItems": True},
                "start_date": DATE_SCHEMA,
                "end_date": DATE_SCHEMA,
                "category": {"type": ["string", "null"], "maxLength": 128},
                "product_id": {"type": ["string", "null"], "maxLength": 128},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "order_by": {"type": ["string", "null"]},
                "descending": {"type": "boolean"},
            },
            "required": ["metrics", "dimensions", "start_date", "end_date", "category", "product_id", "limit", "order_by", "descending"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="search_business_knowledge",
        description=(
            "Retrieve internal KPI definitions, fair-comparison methodology, "
            "SKU data limitations, evidence boundaries, and recommendation rules. "
            "Use this when a question requires definitions or interpretation rules; "
            "analytics tools remain the source of numeric facts."
        ),
        function=search_business_knowledge,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Short search text containing the business concepts "
                        "that need definitions or interpretation rules."
                    ),
                    "minLength": 1,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Maximum number of knowledge entries to return.",
                },
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_monthly_kpis",
        description=(
            "Return monthly GMV, orders, customers, items, freight, calendar "
            "days, daily-normalized GMV/orders/customers, and derived KPIs for "
            "an inclusive month range. freight_share_of_total_paid is freight "
            "divided by (GMV + freight), not freight divided by GMV."
        ),
        function=get_monthly_kpis,
        parameters={
            "type": "object",
            "properties": {
                "start_month": MONTH_SCHEMA,
                "end_month": MONTH_SCHEMA,
            },
            "required": ["start_month", "end_month"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_category_contribution",
        description=(
            "Return the top declining or growing product categories, ranked by "
            "calendar-day-normalized absolute GMV change across two months. "
            "For the largest absolute decline, use direction decline and select "
            "the first row by daily_gmv_change; do not rank by percentage change."
        ),
        function=get_category_contribution,
        parameters={
            "type": "object",
            "properties": {
                "base_month": MONTH_SCHEMA,
                "compare_month": MONTH_SCHEMA,
                "direction": {
                    "type": "string",
                    "enum": ["decline", "growth"],
                    "description": "Return the largest declines or growth drivers.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Maximum number of categories to return.",
                },
            },
            "required": [
                "base_month",
                "compare_month",
                "direction",
                "limit",
            ],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="compare_category",
        description=(
            "Compare total and daily-normalized GMV, order, customer, item, and "
            "price drivers for one product category across two months."
        ),
        function=compare_category,
        parameters={
            "type": "object",
            "properties": {
                "category_name": {
                    "type": "string",
                    "description": "Exact product category name from the database.",
                    "minLength": 1,
                },
                "base_month": MONTH_SCHEMA,
                "compare_month": MONTH_SCHEMA,
            },
            "required": ["category_name", "base_month", "compare_month"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_category_sku_contribution",
        description=(
            "Return the top declining or growing product IDs within one category, "
            "ranked by calendar-day-normalized GMV change across two months. "
            "product_id is the dataset's SKU-level identifier; product names are "
            "not available. Results include GMV, orders, items, average item "
            "price, and whether the SKU is continuing, new, or not sold in the "
            "comparison month's delivered-order data. Not sold does not prove "
            "that a product was delisted or out of stock."
        ),
        function=get_category_sku_contribution,
        parameters={
            "type": "object",
            "properties": {
                "category_name": {
                    "type": "string",
                    "description": "Exact product category name from the database.",
                    "minLength": 1,
                },
                "base_month": MONTH_SCHEMA,
                "compare_month": MONTH_SCHEMA,
                "direction": {
                    "type": "string",
                    "enum": ["decline", "growth"],
                    "description": "Return the largest declines or growth drivers.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Maximum number of product IDs to return.",
                },
            },
            "required": [
                "category_name",
                "base_month",
                "compare_month",
                "direction",
                "limit",
            ],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_entity_time_trend",
        description=(
            "Return a zero-filled daily or Monday-based weekly time trend for "
            "one exact category or product_id SKU. Results include GMV, orders, "
            "customers, items, average price, AOV, per-calendar-day metrics, and "
            "partial-period flags. Use this to identify when a change began; a "
            "zero-sales period does not prove stockout or delisting."
        ),
        function=get_entity_time_trend,
        parameters={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["category", "sku"],
                    "description": "Analyze a product category or SKU-level product.",
                },
                "entity_id": {
                    "type": "string",
                    "description": (
                        "Exact category name or product_id, according to entity_type."
                    ),
                    "minLength": 1,
                },
                "start_date": DATE_SCHEMA,
                "end_date": DATE_SCHEMA,
                "granularity": {
                    "type": "string",
                    "enum": ["day", "week"],
                    "description": "Aggregate the selected period by day or week.",
                },
            },
            "required": [
                "entity_type",
                "entity_id",
                "start_date",
                "end_date",
                "granularity",
            ],
            "additionalProperties": False,
        },
    ),
)

TOOL_REGISTRY = {tool.name: tool for tool in TOOLS}


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return tool metadata for a future model request."""

    return [tool.as_definition() for tool in TOOLS]


def call_tool(name: str, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Execute a registered tool and return JSON-compatible records."""

    try:
        tool = TOOL_REGISTRY[name]
    except KeyError as error:
        available = ", ".join(sorted(TOOL_REGISTRY))
        raise ValueError(
            f"Unknown tool {name!r}. Available tools: {available}"
        ) from error

    if not isinstance(arguments, Mapping):
        raise ValueError("Tool arguments must be an object")
    properties = tool.parameters["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError("Unexpected tool arguments: " + ", ".join(sorted(unknown)))
    for parameter in inspect.signature(tool.function).parameters.values():
        if parameter.default is inspect.Parameter.empty and parameter.name not in arguments:
            raise ValueError("Missing tool argument: " + parameter.name)
    for key, value in arguments.items():
        schema = properties[key]
        if name == "query_business_metrics":
            errors = list(Draft202012Validator(schema).iter_errors(value))
            if errors:
                raise ValueError(f"{key} does not match the approved metric-query schema")
            continue
        valid_type = (isinstance(value, str) if schema["type"] == "string"
                      else isinstance(value, int) and not isinstance(value, bool))
        if not valid_type:
            raise ValueError(f"{key} must be {schema['type']}")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{key} is not an allowed option")
        if isinstance(value, str):
            if not value.strip() or len(value) > 4000:
                raise ValueError(f"{key} must be non-empty and at most 4000 characters")
            if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
                raise ValueError(f"{key} has an invalid format")
        elif value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ValueError(f"{key} is outside the allowed range")
    for first, last in [("start_month", "end_month"), ("base_month", "compare_month"), ("start_date", "end_date")]:
        if first in arguments and last in arguments and arguments[first] > arguments[last]:
            raise ValueError(f"{last} must be on or after {first}")
    result = tool.function(**dict(arguments))

    if isinstance(result.index, pd.RangeIndex):
        serializable = result
    else:
        serializable = result.rename_axis("metric").reset_index()

    return json.loads(serializable.to_json(orient="records"))
