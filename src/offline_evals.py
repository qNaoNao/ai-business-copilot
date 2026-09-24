"""Deterministic, API-free acceptance checks for local analytics tools."""

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from src.tools.registry import call_tool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = PROJECT_ROOT / "evals" / "cases.json"


@dataclass(frozen=True, slots=True)
class CheckResult:
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    question: str
    passed: bool
    checks: tuple[CheckResult, ...]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    results: tuple[CaseResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def passed_cases(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def total_cases(self) -> int:
        return len(self.results)

    @property
    def passed_checks(self) -> int:
        return sum(
            check.passed
            for result in self.results
            for check in result.checks
        )

    @property
    def total_checks(self) -> int:
        return sum(len(result.checks) for result in self.results)


def load_eval_cases(
    cases_path: Path = DEFAULT_CASES_PATH,
) -> list[dict[str, Any]]:
    """Load the offline evaluation case list."""

    with cases_path.open(encoding="utf-8") as cases_file:
        cases = json.load(cases_file)

    if not isinstance(cases, list):
        raise ValueError("Evaluation cases must be a JSON list.")
    if not all(isinstance(case, dict) for case in cases):
        raise ValueError("Each evaluation case must be a JSON object.")

    return cases


def _value_matches(actual: Any, expected: Any) -> bool:
    """Support exact, approximate, and substring-list expectations."""

    if not isinstance(expected, dict):
        return actual == expected

    if "approx" in expected:
        try:
            return math.isclose(
                float(actual),
                float(expected["approx"]),
                rel_tol=float(expected.get("rel_tol", 0.0)),
                abs_tol=float(expected.get("abs_tol", 1e-9)),
            )
        except (TypeError, ValueError):
            return False

    if "contains" in expected:
        required_parts = expected["contains"]
        if isinstance(required_parts, str):
            required_parts = [required_parts]
        if not isinstance(required_parts, list):
            return False

        actual_text = str(actual)
        return all(
            str(part) in actual_text
            for part in required_parts
        )

    return actual == expected


def _row_matches(
    row: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Compare selected fields and return the first useful mismatch."""

    for field, expected_value in expected.items():
        actual_value = row.get(field)
        if not _value_matches(actual_value, expected_value):
            return (
                False,
                f"字段 {field!r}: 实际值 {actual_value!r}, "
                f"期望 {expected_value!r}",
            )

    return True, None


def _matching_rows(
    rows: list[dict[str, Any]],
    where: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if _row_matches(row, where)[0]
    ]


def evaluate_check(
    rows: list[dict[str, Any]],
    check: Mapping[str, Any],
) -> CheckResult:
    """Evaluate one declarative check against tool result rows."""

    check_type = check.get("type")

    if check_type == "row_count":
        expected_count = check.get("expected")
        passed = len(rows) == expected_count
        return CheckResult(
            passed=passed,
            message=(
                f"返回 {len(rows)} 行"
                if passed
                else f"实际返回 {len(rows)} 行，期望 {expected_count} 行"
            ),
        )

    if check_type == "first_row":
        candidates = rows[:1]
        description = "第一行"
    elif check_type == "last_row":
        candidates = rows[-1:]
        description = "最后一行"
    elif check_type in {"row", "last_matching_row"}:
        candidates = _matching_rows(rows, check.get("where", {}))
        if check_type == "last_matching_row":
            candidates = candidates[-1:]
        description = f"匹配条件 {check.get('where', {})} 的行"
    else:
        return CheckResult(False, f"未知检查类型：{check_type!r}")

    if not candidates:
        return CheckResult(False, f"没有找到{description}")

    passed, mismatch = _row_matches(
        candidates[0],
        check.get("expected", {}),
    )
    return CheckResult(
        passed=passed,
        message=(
            f"{description}符合预期"
            if passed
            else f"{description}不符合预期：{mismatch}"
        ),
    )


def evaluate_case(
    case: Mapping[str, Any],
    *,
    tool_caller: Callable[
        [str, Mapping[str, Any]],
        list[dict[str, Any]],
    ] = call_tool,
) -> CaseResult:
    """Run one tool case and all of its deterministic checks."""

    case_id = str(case.get("id", "unnamed_case"))
    question = str(case.get("question", ""))

    try:
        rows = tool_caller(
            str(case["tool"]),
            case.get("arguments", {}),
        )
        checks = tuple(
            evaluate_check(rows, check)
            for check in case.get("checks", [])
        )
    except Exception as error:
        return CaseResult(
            case_id=case_id,
            question=question,
            passed=False,
            checks=(),
            error=str(error),
        )

    return CaseResult(
        case_id=case_id,
        question=question,
        passed=bool(checks) and all(
            check.passed for check in checks
        ),
        checks=checks,
    )


def run_evaluations(
    cases: list[dict[str, Any]],
    *,
    tool_caller: Callable[
        [str, Mapping[str, Any]],
        list[dict[str, Any]],
    ] = call_tool,
) -> EvaluationSummary:
    """Run all cases without contacting an LLM provider."""

    return EvaluationSummary(tuple(
        evaluate_case(case, tool_caller=tool_caller)
        for case in cases
    ))


def format_summary(summary: EvaluationSummary) -> str:
    """Create a concise terminal report."""

    lines = [
        (
            "离线验收结果："
            f"{summary.passed_cases}/{summary.total_cases} 个题目通过，"
            f"{summary.passed_checks}/{summary.total_checks} 项检查通过。"
        )
    ]

    for result in summary.results:
        status = "通过" if result.passed else "失败"
        lines.append(
            f"[{status}] {result.case_id} — {result.question}"
        )
        if result.error:
            lines.append(f"  工具错误：{result.error}")
        for check in result.checks:
            marker = "✓" if check.passed else "✗"
            lines.append(f"  {marker} {check.message}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run API-free business-copilot acceptance checks.",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to an evaluation case JSON file.",
    )
    arguments = parser.parse_args()

    summary = run_evaluations(load_eval_cases(arguments.cases))
    print(format_summary(summary))

    if not summary.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
