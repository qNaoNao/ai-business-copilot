import unittest

from src.ui_helpers import build_csv_downloads


class CsvDownloadTests(unittest.TestCase):
    def test_builds_csv_for_successful_tabular_tool_output(self):
        downloads = build_csv_downloads(
            [{
                "name": "get_entity_time_trend",
                "arguments": {"entity_type": "sku"},
                "output": {
                    "ok": True,
                    "data": [
                        {"period": "2018-01-01", "gmv": 903.0},
                        {"period": "2018-01-08", "gmv": 2709.0},
                    ],
                },
            }],
            turn_number=2,
        )

        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0]["row_count"], 2)
        self.assertEqual(
            downloads[0]["file_name"],
            "turn_2_1_get_entity_time_trend.csv",
        )
        decoded_csv = downloads[0]["data"].decode("utf-8-sig")
        self.assertIn("period,gmv", decoded_csv)
        self.assertIn("2018-01-08,2709.0", decoded_csv)

    def test_skips_errors_and_empty_results(self):
        downloads = build_csv_downloads(
            [
                {
                    "name": "failed_tool",
                    "output": {"ok": False, "error": "failed"},
                },
                {
                    "name": "empty_tool",
                    "output": {"ok": True, "data": []},
                },
            ],
            turn_number=1,
        )

        self.assertEqual(downloads, [])


if __name__ == "__main__":
    unittest.main()
