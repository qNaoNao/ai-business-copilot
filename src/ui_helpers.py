"""Small presentation helpers shared by the local chat UI and its tests."""

import re
from typing import Any

import pandas as pd


def build_csv_downloads(
    tool_trace: list[dict[str, Any]],
    *,
    turn_number: int,
) -> list[dict[str, Any]]:
    """Build downloadable CSV files from successful tabular tool outputs."""

    downloads = []

    for call_number, tool_call in enumerate(tool_trace, start=1):
        output = tool_call.get("output", {})
        rows = output.get("data") if output.get("ok") else None

        if not isinstance(rows, list) or not rows:
            continue

        if not all(isinstance(row, dict) for row in rows):
            continue

        tool_name = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            str(tool_call.get("name", "tool_result")),
        ).strip("_") or "tool_result"
        file_name = (
            f"turn_{turn_number}_{call_number}_{tool_name}.csv"
        )
        csv_bytes = (
            pd.DataFrame(rows)
            .to_csv(index=False)
            .encode("utf-8-sig")
        )

        downloads.append({
            "tool_name": tool_name,
            "file_name": file_name,
            "data": csv_bytes,
            "row_count": len(rows),
            "call_number": call_number,
        })

    return downloads
