import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.agent import (
    AgentTurnResult,
    INSTRUCTIONS,
    main,
    read_question,
    run_agent,
    run_agent_turn,
)
from src.run_history import save_run


class FakeResponses:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        return next(self._responses)


class FakeClient:
    def __init__(self, responses):
        self.responses = FakeResponses(responses)


class AgentLoopTests(unittest.TestCase):
    def test_instructions_require_fair_month_comparisons(self):
        self.assertIn("daily-normalized", INSTRUCTIONS)
        self.assertIn("freight / (GMV + freight)", INSTRUCTIONS)
        self.assertIn(
            "Use search_business_knowledge",
            INSTRUCTIONS,
        )
        self.assertIn(
            "source of numeric facts",
            INSTRUCTIONS,
        )
        self.assertIn("Rank by", INSTRUCTIONS)
        self.assertIn(
            "daily_gmv_change, not daily_change_pct",
            INSTRUCTIONS,
        )

    def test_instructions_enforce_evidence_boundaries(self):
        self.assertIn("strict evidence boundary", INSTRUCTIONS)
        self.assertIn("hypotheses to verify", INSTRUCTIONS)
        self.assertIn("Do not claim changes in traffic", INSTRUCTIONS)
        self.assertIn("do not assume which cause is true", INSTRUCTIONS)
        self.assertIn("Do not invent numeric", INSTRUCTIONS)
        self.assertIn(
            "product_id is the only available product identifier",
            INSTRUCTIONS,
        )
        self.assertIn("product names", INSTRUCTIONS)
        self.assertIn("does not prove", INSTRUCTIONS)
        self.assertIn(
            "Use get_entity_time_trend",
            INSTRUCTIONS,
        )
        self.assertIn(
            "Do not promise exports",
            INSTRUCTIONS,
        )
        self.assertIn("do not provide currency metadata", INSTRUCTIONS)
        self.assertIn("specific internal table names", INSTRUCTIONS)
        self.assertIn("data owners", INSTRUCTIONS)
        self.assertIn("generic offers", INSTRUCTIONS)

    def test_executes_tool_and_returns_final_answer(self):
        tool_call = SimpleNamespace(
            type="function_call",
            name="get_monthly_kpis",
            arguments=json.dumps({
                "start_month": "2018-01",
                "end_month": "2018-02",
            }),
            call_id="call_123",
        )
        first_response = SimpleNamespace(
            id="resp_tool",
            output=[tool_call],
            output_text="",
        )
        final_response = SimpleNamespace(
            id="resp_final",
            output=[SimpleNamespace(type="message")],
            output_text="GMV decreased between the two months.",
        )
        client = FakeClient([first_response, final_response])
        tool_trace = []

        with patch(
            "src.agent.call_tool",
            return_value=[{"month": "2018-01", "gmv": 100.0}],
        ) as mocked_call_tool:
            answer = run_agent(
                "How did the business perform?",
                client=client,
                model="test-model",
                tool_trace=tool_trace,
            )

        self.assertEqual(answer, "GMV decreased between the two months.")
        mocked_call_tool.assert_called_once_with(
            "get_monthly_kpis",
            {
                "start_month": "2018-01",
                "end_month": "2018-02",
            },
        )
        self.assertEqual(len(client.responses.requests), 2)
        self.assertNotIn(
            "previous_response_id",
            client.responses.requests[0],
        )
        self.assertEqual(
            client.responses.requests[1]["previous_response_id"],
            "resp_tool",
        )
        self.assertTrue(
            client.responses.requests[0]["store"]
        )
        self.assertEqual(
            client.responses.requests[1]["instructions"],
            INSTRUCTIONS,
        )
        self.assertEqual(tool_trace[0]["name"], "get_monthly_kpis")
        self.assertEqual(
            tool_trace[0]["arguments"],
            {
                "start_month": "2018-01",
                "end_month": "2018-02",
            },
        )
        self.assertTrue(tool_trace[0]["output"]["ok"])

        second_input = client.responses.requests[1]["input"]
        tool_outputs = [
            item
            for item in second_input
            if isinstance(item, dict)
            and item.get("type") == "function_call_output"
        ]
        self.assertEqual(tool_outputs[0]["call_id"], "call_123")
        self.assertIn('"ok": true', tool_outputs[0]["output"])

    def test_returns_direct_answer_when_no_tool_is_needed(self):
        response = SimpleNamespace(
            id="resp_direct",
            output=[SimpleNamespace(type="message")],
            output_text="Hello! How can I help?",
        )
        client = FakeClient([response])

        answer = run_agent(
            "Hello",
            client=client,
            model="test-model",
        )

        self.assertEqual(answer, "Hello! How can I help?")
        self.assertEqual(len(client.responses.requests), 1)

    def test_supports_combined_category_sku_and_knowledge_workflow(self):
        tool_requests = [
            (
                "get_category_contribution",
                {
                    "base_month": "2018-01",
                    "compare_month": "2018-02",
                    "direction": "decline",
                    "limit": 10,
                },
            ),
            (
                "compare_category",
                {
                    "category_name": "stationery",
                    "base_month": "2018-01",
                    "compare_month": "2018-02",
                },
            ),
            (
                "get_category_sku_contribution",
                {
                    "category_name": "stationery",
                    "base_month": "2018-01",
                    "compare_month": "2018-02",
                    "direction": "decline",
                    "limit": 5,
                },
            ),
            (
                "search_business_knowledge",
                {
                    "query": "SKU 缺货 证据 管理建议",
                    "limit": 3,
                },
            ),
        ]

        responses = [
            SimpleNamespace(
                id=f"resp_{index}",
                output=[SimpleNamespace(
                    type="function_call",
                    name=name,
                    arguments=json.dumps(arguments),
                    call_id=f"call_{index}",
                )],
                output_text="",
            )
            for index, (name, arguments) in enumerate(
                tool_requests,
                start=1,
            )
        ]
        responses.append(SimpleNamespace(
            id="resp_final",
            output=[SimpleNamespace(type="message")],
            output_text="Evidence-based SKU diagnosis.",
        ))

        client = FakeClient(responses)
        fake_results = [
            [{"category": "stationery"}],
            [{"metric": "daily_gmv"}],
            [{"product_id": "sku_1"}],
            [{"id": "sku_data_scope"}],
        ]

        with patch(
            "src.agent.call_tool",
            side_effect=fake_results,
        ) as mocked_call_tool:
            answer = run_agent(
                "Find and diagnose the largest category decline.",
                client=client,
                model="test-model",
            )

        self.assertEqual(
            answer,
            "Evidence-based SKU diagnosis.",
        )
        self.assertEqual(
            [
                call.args[0]
                for call in mocked_call_tool.call_args_list
            ],
            [
                name
                for name, _ in tool_requests
            ],
        )
        self.assertEqual(
            len(client.responses.requests),
            5,
        )

    def test_continues_a_follow_up_from_previous_response_id(self):
        first_response = SimpleNamespace(
            id="turn_1_response",
            output=[SimpleNamespace(type="message")],
            output_text="Stationery declined the most.",
        )
        second_response = SimpleNamespace(
            id="turn_2_response",
            output=[SimpleNamespace(type="message")],
            output_text="Its top SKU was sku_1.",
        )
        client = FakeClient([
            first_response,
            second_response,
        ])

        first_turn = run_agent_turn(
            "Which category declined the most?",
            client=client,
            model="test-model",
        )
        second_turn = run_agent_turn(
            "Which SKU drove that decline?",
            client=client,
            model="test-model",
            previous_response_id=(
                first_turn.response_id
            ),
        )

        self.assertEqual(
            first_turn.response_id,
            "turn_1_response",
        )
        self.assertEqual(
            second_turn.answer,
            "Its top SKU was sku_1.",
        )
        self.assertEqual(
            client.responses.requests[1][
                "previous_response_id"
            ],
            "turn_1_response",
        )
        self.assertEqual(
            client.responses.requests[1][
                "instructions"
            ],
            INSTRUCTIONS,
        )

    @patch(
        "builtins.input",
        side_effect=[
            "请完成经营分析。",
            "",
            "请继续下钻SKU。",
            "END",
        ],
    )
    def test_reads_multiline_question_until_end(self, mocked_input):
        question = read_question()

        self.assertEqual(
            question,
            "请完成经营分析。\n\n请继续下钻SKU。",
        )
        self.assertEqual(mocked_input.call_count, 4)

    @patch(
        "builtins.input",
        side_effect=["EXIT"],
    )
    def test_reads_exit_without_requiring_end(self, mocked_input):
        self.assertEqual(
            read_question(),
            "EXIT",
        )
        self.assertEqual(mocked_input.call_count, 1)

    def test_main_runs_multiple_turns_in_one_session(self):
        saved_path = Path("/tmp/agent_runs.jsonl")

        with (
            patch(
                "src.agent.create_client",
                return_value=object(),
            ),
            patch(
                "src.agent.read_question",
                side_effect=[
                    "First question",
                    "Follow-up question",
                    "EXIT",
                ],
            ),
            patch(
                "src.agent.run_agent_turn",
                side_effect=[
                    AgentTurnResult(
                        answer="First answer",
                        response_id="response_1",
                    ),
                    AgentTurnResult(
                        answer="Follow-up answer",
                        response_id="response_2",
                    ),
                ],
            ) as mocked_run_agent_turn,
            patch(
                "src.agent.save_run",
                return_value=saved_path,
            ) as mocked_save_run,
        ):
            main()

        self.assertEqual(
            mocked_run_agent_turn.call_count,
            2,
        )
        first_request = (
            mocked_run_agent_turn
            .call_args_list[0]
            .kwargs
        )
        second_request = (
            mocked_run_agent_turn
            .call_args_list[1]
            .kwargs
        )
        self.assertIsNone(
            first_request["previous_response_id"]
        )
        self.assertEqual(
            second_request["previous_response_id"],
            "response_1",
        )

        first_saved = (
            mocked_save_run
            .call_args_list[0]
            .kwargs
        )
        second_saved = (
            mocked_save_run
            .call_args_list[1]
            .kwargs
        )
        self.assertEqual(
            first_saved["session_id"],
            second_saved["session_id"],
        )
        self.assertEqual(
            first_saved["turn_number"],
            1,
        )
        self.assertEqual(
            second_saved["turn_number"],
            2,
        )
        self.assertEqual(
            second_saved["previous_response_id"],
            "response_1",
        )
        self.assertEqual(
            second_saved["response_id"],
            "response_2",
        )

    def test_saves_a_local_jsonl_run_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "agent_runs.jsonl"

            saved_path = save_run(
                question="What changed?",
                answer="Orders increased.",
                tool_trace=[{
                    "name": "get_monthly_kpis",
                    "arguments": {
                        "start_month": "2018-01",
                        "end_month": "2018-02",
                    },
                    "output": {
                        "ok": True,
                        "data": [{"month": "2018-01"}],
                    },
                }],
                model="test-model",
                session_id="session_123",
                turn_number=2,
                previous_response_id="response_1",
                response_id="response_2",
                history_path=history_path,
            )

            record = json.loads(
                history_path.read_text(encoding="utf-8").strip()
            )

        self.assertEqual(saved_path, history_path)
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["question"], "What changed?")
        self.assertEqual(record["answer"], "Orders increased.")
        self.assertEqual(
            record["session_id"],
            "session_123",
        )
        self.assertEqual(record["turn_number"], 2)
        self.assertEqual(
            record["previous_response_id"],
            "response_1",
        )
        self.assertEqual(
            record["response_id"],
            "response_2",
        )
        self.assertEqual(
            record["tool_trace"][0]["name"],
            "get_monthly_kpis",
        )
        self.assertNotIn("api_key", record)


if __name__ == "__main__":
    unittest.main()
