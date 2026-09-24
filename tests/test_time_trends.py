import math
import unittest

from src.database import DB_PATH
from src.tools.time_trends import (
    get_entity_time_trend,
)


SKU = "5411e9269501a870cabf632f05655131"
CATEGORY = "stationery"
START_DATE = "2018-01-01"
END_DATE = "2018-02-28"


@unittest.skipUnless(
    DB_PATH.exists(),
    f"Local analytics database not found: {DB_PATH}",
)
class TimeTrendTests(unittest.TestCase):
    def test_returns_complete_daily_category_trend(self):
        result = get_entity_time_trend(
            "category",
            CATEGORY,
            START_DATE,
            END_DATE,
            "day",
        )

        self.assertEqual(len(result), 59)
        self.assertEqual(
            result.iloc[0]["period_start"],
            START_DATE,
        )
        self.assertEqual(
            result.iloc[-1]["period_end"],
            END_DATE,
        )
        self.assertEqual(
            result["period_days"].unique().tolist(),
            [1],
        )
        self.assertFalse(
            result["is_partial_period"].any()
        )
        self.assertFalse(
            result["gmv"].isna().any()
        )
        self.assertTrue(math.isclose(
            result["gmv"].sum(),
            49613.25,
            rel_tol=1e-9,
        ))

    def test_returns_weekly_sku_trend_with_partial_period(self):
        result = get_entity_time_trend(
            "sku",
            SKU,
            START_DATE,
            END_DATE,
            "week",
        )

        self.assertEqual(len(result), 9)
        self.assertEqual(
            result["gmv"].head(3).tolist(),
            [903.0, 2709.0, 903.0],
        )
        self.assertTrue(
            (result["gmv"].iloc[3:] == 0).all()
        )
        self.assertEqual(
            result.iloc[-1]["period_days"],
            3,
        )
        self.assertTrue(
            result.iloc[-1]["is_partial_period"]
        )
        self.assertAlmostEqual(
            result["gmv"].sum(),
            4515.0,
        )
        self.assertEqual(
            result["orders"].sum(),
            34,
        )
        self.assertEqual(
            result["items"].sum(),
            35,
        )

    def test_validates_time_trend_arguments(self):
        with self.assertRaisesRegex(
            ValueError,
            "entity_type",
        ):
            get_entity_time_trend(
                "seller",
                SKU,
                START_DATE,
                END_DATE,
                "week",
            )

        with self.assertRaisesRegex(
            ValueError,
            "granularity",
        ):
            get_entity_time_trend(
                "sku",
                SKU,
                START_DATE,
                END_DATE,
                "month",
            )

        with self.assertRaisesRegex(
            ValueError,
            "end_date",
        ):
            get_entity_time_trend(
                "sku",
                SKU,
                END_DATE,
                START_DATE,
                "day",
            )

        with self.assertRaisesRegex(
            ValueError,
            "Unknown sku",
        ):
            get_entity_time_trend(
                "sku",
                "not-a-real-product",
                START_DATE,
                END_DATE,
                "day",
            )


if __name__ == "__main__":
    unittest.main()
