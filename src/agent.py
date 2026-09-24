"""Minimal tool-calling loop for the AI Business Copilot."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from openai import OpenAI

from src.run_history import save_run
from src.tools.registry import call_tool, get_tool_definitions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gpt-5-mini"
MAX_TOOL_ROUNDS = 7

INSTRUCTIONS = """
You are an evidence-based business analytics copilot.

Use the provided tools whenever the user asks about business performance or the
underlying dataset. Do not invent database values. Choose the smallest useful tool,
interpret its output clearly, and state the comparison period. Reply in the same
language as the user. If a tool reports an error or the data is unavailable, explain
that limitation instead of guessing.

Use search_business_knowledge when the question requires KPI definitions, comparison
methodology, SKU limitations, evidence boundaries, or recommendation rules. Treat
retrieved knowledge as interpretation guidance and analytics-tool output as the
source of numeric facts.

When identifying the category with the largest absolute daily GMV decline, call
get_category_contribution with direction decline and use its first row. Rank by
daily_gmv_change, not daily_change_pct. Diagnose only that selected category unless
the user explicitly asks to compare multiple categories.

When comparing monthly totals, check days_in_month. If the months have different
numbers of calendar days, distinguish total changes from daily-normalized changes
and do not infer demand, traffic, or operating momentum from totals alone.

The metric freight_share_of_total_paid means freight / (GMV + freight). State that
denominator explicitly whenever discussing it; never describe it as freight / GMV.

Keep a strict evidence boundary:
- Observed facts must come directly from tool output.
- Interpretations must be labeled as interpretations and use non-causal language.
- Possible causes and recommended checks must be labeled as hypotheses to verify.

Do not claim changes in traffic, conversion rate, inventory, product availability,
discounting, profit margin, ROI, or marketing-channel performance unless a tool
provides those measures. A change in average item price may come from pricing,
promotions, or product mix; do not assume which cause is true. Do not invent numeric
targets or deadlines. If the user asks for a target, state the assumptions and basis.

In SKU analysis, product_id is the only available product identifier; do not invent
product names. The status not_sold_in_compare_month means that the product had no
items in delivered-order data for that month. It does not prove that the product was
delisted, discontinued, or out of stock.

Use get_entity_time_trend when the user asks when a category or SKU change began.
Zero-filled periods show no delivered-order items in the selected period, not the
cause of that absence.

The dataset and tools do not provide currency metadata. Never label monetary values
as yuan, RMB, CNY, BRL, USD, or use a currency symbol unless a tool explicitly
provides that currency. Use plain numbers or say "金额单位（数据未注明币种）" in
Chinese / "dataset currency units (currency not specified)" in English. For example,
write "39,522.72（金额单位未注明）", not "39,522.72元".

Keep a strict capability boundary:
- Do not claim to know or retrieve specific internal table names, source-field names,
  system names, data owners, departments, or responsible people unless tool output
  explicitly provides them.
- You may recommend categories of additional data to collect and the type of business
  function that might own them, but label these as suggestions rather than known facts.
- Do not promise exports, analyses, fields, or follow-up actions that the currently
  registered tools cannot perform.
- Complete the requested answer and stop. Do not end with generic offers for
  unsupported work. Offer a follow-up only when a registered tool can perform it.
""".strip()


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    answer: str
    response_id: str


def create_client() -> OpenAI:
    """Create an OpenAI client using values from the project's .env file."""

    load_dotenv(PROJECT_ROOT / ".env")

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Copy .env.example to .env and add your key."
        )

    return OpenAI()


def run_agent_turn(
    question: str,
    *,
    client: Any | None = None,
    model: str | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    verbose: bool = False,
    tool_trace: list[dict[str, Any]] | None = None,
    previous_response_id: str | None = None,
) -> AgentTurnResult:
    """Answer one turn and return the final response ID for continuation."""

    if not question.strip():
        raise ValueError("Question cannot be empty.")

    load_dotenv(PROJECT_ROOT / ".env")
    client = client or create_client()
    model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    input_items: list[Any] = [
        {
            "role": "user",
            "content": question,
        }
    ]
    chained_response_id = previous_response_id

    for _ in range(max_tool_rounds):
        request = {
            "model": model,
            "instructions": INSTRUCTIONS,
            "tools": get_tool_definitions(),
            "input": input_items,
            "parallel_tool_calls": False,
            "store": True,
        }

        if chained_response_id:
            request["previous_response_id"] = (
                chained_response_id
            )

        response = client.responses.create(
            **request
        )

        response_id = getattr(
            response,
            "id",
            None,
        )

        if not response_id:
            raise RuntimeError(
                "The model response did not include a response ID."
            )

        tool_calls = [
            item
            for item in response.output
            if item.type == "function_call"
        ]

        if not tool_calls:
            if response.output_text:
                return AgentTurnResult(
                    answer=response.output_text,
                    response_id=response_id,
                )
            raise RuntimeError("The model returned neither text nor a tool call.")

        tool_output_items = []

        for tool_call in tool_calls:
            arguments: Any = tool_call.arguments
            try:
                arguments = json.loads(tool_call.arguments)
                if verbose:
                    print(
                        f"[tool] {tool_call.name}"
                        f"({json.dumps(arguments, ensure_ascii=False)})"
                    )
                result = call_tool(tool_call.name, arguments)
                tool_output = {
                    "ok": True,
                    "data": result,
                }
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                tool_output = {
                    "ok": False,
                    "error": str(error),
                }

            if tool_trace is not None:
                tool_trace.append({
                    "name": tool_call.name,
                    "arguments": arguments,
                    "output": tool_output,
                })

            tool_output_items.append({
                "type": "function_call_output",
                "call_id": tool_call.call_id,
                "output": json.dumps(
                    tool_output,
                    ensure_ascii=False,
                ),
            })

        chained_response_id = response_id
        input_items = tool_output_items

    raise RuntimeError(
        f"The agent exceeded the limit of {max_tool_rounds} tool rounds."
    )


def run_agent(
    question: str,
    *,
    client: Any | None = None,
    model: str | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    verbose: bool = False,
    tool_trace: list[dict[str, Any]] | None = None,
) -> str:
    """Backward-compatible single-turn helper returning only answer text."""

    return run_agent_turn(
        question,
        client=client,
        model=model,
        max_tool_rounds=max_tool_rounds,
        verbose=verbose,
        tool_trace=tool_trace,
    ).answer


def read_question(turn_number=1) -> str:
    """Read a one-line or multi-line terminal question until END."""

    print(
        f"\nQuestion {turn_number}. "
        "Paste your question, then type END "
        "on a new line to submit. "
        "Type EXIT alone to finish:"
    )
    lines = []

    while True:
        try:
            line = input()
        except EOFError:
            break

        if (
            not lines
            and line.strip().upper() == "EXIT"
        ):
            return "EXIT"

        if line.strip() == "END":
            break

        lines.append(line)

    return "\n".join(lines).strip()


def main() -> None:
    """Run a continuous terminal conversation until the user enters EXIT."""

    load_dotenv(PROJECT_ROOT / ".env")
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    session_id = uuid4().hex
    previous_response_id = None
    turn_number = 1

    try:
        client = create_client()
    except Exception as error:
        raise SystemExit(
            f"\nCould not start the business copilot: {error}"
        ) from error

    print(
        "Business Copilot session started. "
        f"Session ID: {session_id}"
    )

    while True:
        question = read_question(turn_number)

        if question.upper() == "EXIT":
            print("\nBusiness Copilot session ended.")
            return

        if not question:
            print(
                "\nQuestion cannot be empty. "
                "Please try again."
            )
            continue

        tool_trace: list[dict[str, Any]] = []
        turn_previous_response_id = (
            previous_response_id
        )

        try:
            result = run_agent_turn(
                question,
                client=client,
                model=model,
                verbose=True,
                tool_trace=tool_trace,
                previous_response_id=(
                    turn_previous_response_id
                ),
            )
        except Exception as error:
            history_path = save_run(
                question=question,
                answer=None,
                tool_trace=tool_trace,
                model=model,
                error=str(error),
                session_id=session_id,
                turn_number=turn_number,
                previous_response_id=(
                    turn_previous_response_id
                ),
            )
            print(
                "\nCould not complete this turn: "
                f"{error}"
            )
            print(
                f"Failed turn saved to: {history_path}"
            )
            continue

        history_path = save_run(
            question=question,
            answer=result.answer,
            tool_trace=tool_trace,
            model=model,
            session_id=session_id,
            turn_number=turn_number,
            previous_response_id=(
                turn_previous_response_id
            ),
            response_id=result.response_id,
        )
        previous_response_id = result.response_id

        print(
            f"\nBusiness Copilot:\n{result.answer}"
        )
        print(
            f"\nTurn saved to: {history_path}"
        )
        turn_number += 1


if __name__ == "__main__":
    main()
