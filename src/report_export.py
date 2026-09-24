"""Create self-contained Markdown and HTML reports from one chat session."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
from typing import Any

import mistune


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Download-ready versions of one conversation report."""

    markdown: str
    html: str
    markdown_file_name: str
    html_file_name: str


def _format_table_value(value: Any) -> str:
    """Format one value safely inside a Markdown table cell."""

    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)

    return (
        str(value)
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
    )


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    """Convert JSON-compatible records to a Markdown table."""

    if not rows:
        return "_查询没有返回数据行。_"

    columns = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| "
        + " | ".join(
            _format_table_value(row.get(column))
            for column in columns
        )
        + " |"
        for row in rows
    ]

    return "\n".join([header, separator, *body])


def _tool_evidence_markdown(
    tool_trace: list[dict[str, Any]],
) -> list[str]:
    """Render tool arguments and results as report evidence sections."""

    if not tool_trace:
        return []

    sections = ["### 数据依据"]

    for call_number, tool_call in enumerate(tool_trace, start=1):
        tool_name = str(tool_call.get("name", "unknown_tool"))
        arguments = tool_call.get("arguments", {})
        output = tool_call.get("output", {})

        sections.extend([
            f"#### {call_number}. `{tool_name}`",
            "查询条件：",
            "```json",
            json.dumps(arguments, ensure_ascii=False, indent=2),
            "```",
        ])

        if not output.get("ok"):
            sections.append(
                f"工具执行失败：{output.get('error', '未保存具体错误。')}"
            )
            continue

        data = output.get("data")
        if (
            isinstance(data, list)
            and all(isinstance(row, dict) for row in data)
        ):
            sections.append(_markdown_table(data))
        else:
            sections.extend([
                "```json",
                json.dumps(data, ensure_ascii=False, indent=2),
                "```",
            ])

    return sections


def _build_markdown(
    messages: list[dict[str, Any]],
    *,
    session_id: str,
    model: str,
    generated_at: datetime,
) -> str:
    """Build the editable source report."""

    user_turns = sum(
        message.get("role") == "user"
        for message in messages
    )
    sections = [
        "# AI Business Copilot 分析报告",
        f"- 生成时间：{generated_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 会话编号：`{session_id}`",
        f"- 使用模型：`{model}`",
        f"- 对话轮数：{user_turns}",
        "",
        "---",
    ]
    turn_number = 0

    for message in messages:
        role = message.get("role")
        content = str(message.get("content", "")).strip()

        if role == "user":
            turn_number += 1
            sections.extend([
                "",
                f"## 第 {turn_number} 轮",
                "### 分析问题",
                content or "（未保存问题文本。）",
            ])
            continue

        if role != "assistant":
            continue

        if turn_number == 0:
            turn_number = 1
            sections.extend(["", "## 第 1 轮"])

        sections.extend([
            "### 分析结论",
            content or "（未保存回答文本。）",
        ])
        tool_trace = message.get("tool_trace", [])
        if isinstance(tool_trace, list):
            sections.extend(_tool_evidence_markdown(tool_trace))

    sections.extend([
        "",
        "---",
        "_本报告由 AI Business Copilot 根据已保存的会话和本地数据工具结果生成。_",
        "",
    ])

    return "\n\n".join(sections)


def _build_html(markdown_text: str) -> str:
    """Convert report Markdown into a safe, printable HTML document."""

    markdown_renderer = mistune.create_markdown(
        escape=True,
        plugins=["table"],
    )
    report_body = markdown_renderer(markdown_text)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Business Copilot 分析报告</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 40px 24px 64px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.65;
    }}
    h1, h2, h3, h4 {{ line-height: 1.3; margin-top: 1.6em; }}
    h1 {{ margin-top: 0; }}
    pre {{ padding: 14px; overflow-x: auto; border: 1px solid #8886; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    table {{ border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }}
    th, td {{ border-bottom: 1px solid #8886; padding: 8px 10px; text-align: left; }}
    th {{ font-weight: 600; }}
    hr {{ border: 0; border-top: 1px solid #8886; margin: 32px 0; }}
    @media print {{
      :root {{ color-scheme: light; }}
      body {{ max-width: none; padding: 0; color: #111; background: #fff; }}
      pre, table {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
{report_body}
</body>
</html>
"""


def build_analysis_report(
    messages: list[dict[str, Any]],
    *,
    session_id: str,
    model: str,
    generated_at: datetime | None = None,
) -> AnalysisReport:
    """Build Markdown and standalone HTML downloads for one conversation."""

    generated_at = generated_at or datetime.now(timezone.utc)
    safe_session_id = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        session_id,
    ).strip("_") or "session"
    file_stem = f"business_copilot_report_{safe_session_id[:16]}"
    markdown_text = _build_markdown(
        messages,
        session_id=session_id,
        model=model,
        generated_at=generated_at,
    )

    return AnalysisReport(
        markdown=markdown_text,
        html=_build_html(markdown_text),
        markdown_file_name=f"{file_stem}.md",
        html_file_name=f"{file_stem}.html",
    )
