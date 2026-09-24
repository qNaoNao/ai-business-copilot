"""Lightweight local retrieval for business definitions and analysis rules."""

import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_PATH = (
    PROJECT_ROOT
    / "knowledge_base"
    / "business_rules.json"
)

RESULT_COLUMNS = [
    "id",
    "title",
    "content",
    "matched_keywords",
]


def load_business_knowledge(
    knowledge_path=KNOWLEDGE_PATH,
):
    """Load and validate the local business knowledge entries."""

    with Path(knowledge_path).open(
        "r",
        encoding="utf-8",
    ) as knowledge_file:
        entries = json.load(knowledge_file)

    if not isinstance(entries, list):
        raise ValueError(
            "Business knowledge must be a list of entries."
        )

    required_fields = {
        "id",
        "title",
        "keywords",
        "content",
    }

    for entry in entries:
        missing = required_fields - set(entry)
        if missing:
            raise ValueError(
                "Knowledge entry is missing fields: "
                + ", ".join(sorted(missing))
            )

    return entries


def search_business_knowledge(
    query,
    limit=3,
):
    """Return the locally stored business rules most relevant to a query."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError(
            "query must be a non-empty string."
        )

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 5
    ):
        raise ValueError(
            "limit must be an integer between 1 and 5."
        )

    normalized_query = query.casefold()
    matches = []

    for entry in load_business_knowledge():
        matched_keywords = [
            keyword
            for keyword in entry["keywords"]
            if keyword.casefold() in normalized_query
        ]

        if not matched_keywords:
            continue

        matches.append({
            "id": entry["id"],
            "title": entry["title"],
            "content": entry["content"],
            "matched_keywords": ", ".join(
                matched_keywords
            ),
            "_score": len(matched_keywords),
        })

    matches.sort(
        key=lambda item: (
            -item["_score"],
            item["id"],
        )
    )

    for match in matches:
        match.pop("_score")

    return pd.DataFrame(
        matches[:limit],
        columns=RESULT_COLUMNS,
    )
