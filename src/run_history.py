"""Local JSON Lines history for interactive business-copilot runs."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_HISTORY_PATH = PROJECT_ROOT / "runs" / "agent_runs.jsonl"


def load_run_history(
    history_path: Path = RUN_HISTORY_PATH,
) -> list[dict[str, Any]]:
    """Load valid JSON-object records while tolerating a missing/corrupt line."""

    if not history_path.exists():
        return []

    records = []

    with history_path.open(encoding="utf-8") as history_file:
        for line in history_file:
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if isinstance(record, dict):
                records.append(record)

    return records


def group_history_sessions(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group turns into newest-first sessions, including legacy single turns."""

    sessions_by_id: dict[str, list[dict[str, Any]]] = {}

    for record_number, record in enumerate(records, start=1):
        session_id = record.get("session_id")
        if not session_id:
            session_id = f"legacy-{record_number}"

        sessions_by_id.setdefault(str(session_id), []).append(record)

    sessions = []

    for session_id, session_records in sessions_by_id.items():
        latest_timestamp = max(
            (
                str(record.get("timestamp_utc", ""))
                for record in session_records
            ),
            default="",
        )
        preview = next(
            (
                str(record.get("question", "")).strip()
                for record in session_records
                if str(record.get("question", "")).strip()
            ),
            "未命名记录",
        )
        models = sorted({
            str(record["model"])
            for record in session_records
            if record.get("model")
        })

        sessions.append({
            "session_id": session_id,
            "records": session_records,
            "latest_timestamp": latest_timestamp,
            "preview": preview,
            "turn_count": len(session_records),
            "models": models,
            "is_legacy": session_id.startswith("legacy-"),
        })

    return sorted(
        sessions,
        key=lambda session: session["latest_timestamp"],
        reverse=True,
    )


def history_records_to_messages(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert stored run records into read-only chat messages."""

    messages = []

    for position, record in enumerate(records, start=1):
        question = str(record.get("question", "")).strip()
        if question:
            messages.append({
                "role": "user",
                "content": question,
            })

        status = record.get("status")
        if status == "error":
            content = (
                "这轮历史运行没有完成："
                f"{record.get('error') or '未保存具体错误。'}"
            )
        else:
            content = str(
                record.get("answer")
                or "（该轮没有保存回答文本。）"
            )

        tool_trace = record.get("tool_trace", [])
        if not isinstance(tool_trace, list):
            tool_trace = []

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_trace": tool_trace,
            "turn_number": (
                record.get("turn_number")
                or position
            ),
        })

    return messages


def save_run(
    *,
    question: str,
    answer: str | None,
    tool_trace: list[dict[str, Any]],
    model: str,
    error: str | None = None,
    session_id: str | None = None,
    turn_number: int | None = None,
    previous_response_id: str | None = None,
    response_id: str | None = None,
    history_path: Path = RUN_HISTORY_PATH,
) -> Path:
    """Append one local run record without storing credentials."""

    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "error" if error else "success",
        "session_id": session_id,
        "turn_number": turn_number,
        "model": model,
        "previous_response_id": previous_response_id,
        "response_id": response_id,
        "question": question,
        "tool_trace": tool_trace,
        "answer": answer,
        "error": error,
    }

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as history_file:
        history_file.write(
            json.dumps(record, ensure_ascii=False)
            + "\n"
        )

    return history_path


def save_run_safely(
    **run_details: Any,
) -> tuple[Path | None, str | None]:
    """Save a run without allowing a local filesystem error to hide an answer."""

    try:
        return save_run(**run_details), None
    except OSError as error:
        return None, str(error)
