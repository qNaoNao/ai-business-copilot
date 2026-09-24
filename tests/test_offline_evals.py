import unittest

from src.offline_evals import (
    evaluate_case,
    format_summary,
    load_eval_cases,
    run_evaluations,
)


class OfflineEvaluationTests(unittest.TestCase):
    def test_project_acceptance_cases_pass_against_local_data(self):
        cases = load_eval_cases()
        summary = run_evaluations(cases)

        self.assertEqual(len(cases), 7)
        self.assertTrue(summary.passed, format_summary(summary))
        self.assertEqual(summary.passed_cases, 7)
        self.assertGreaterEqual(summary.total_checks, 17)

    def test_failed_expectation_returns_a_useful_message(self):
        case = {
            "id": "intentional_failure",
            "question": "Does failure reporting work?",
            "tool": "fake_tool",
            "arguments": {},
            "checks": [{
                "type": "first_row",
                "expected": {"category": "expected"},
            }],
        }

        result = evaluate_case(
            case,
            tool_caller=lambda name, arguments: [
                {"category": "actual"}
            ],
        )

        self.assertFalse(result.passed)
        self.assertIn("实际值 'actual'", result.checks[0].message)

    def test_tool_error_fails_only_that_case(self):
        case = {
            "id": "tool_error",
            "question": "Can tool errors be reported?",
            "tool": "fake_tool",
            "arguments": {},
            "checks": [{"type": "row_count", "expected": 1}],
        }

        def failing_tool(name, arguments):
            raise ValueError("database unavailable")

        result = evaluate_case(case, tool_caller=failing_tool)

        self.assertFalse(result.passed)
        self.assertEqual(result.error, "database unavailable")


if __name__ == "__main__":
    unittest.main()
