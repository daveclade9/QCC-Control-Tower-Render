import unittest
from datetime import date, timedelta

from qcc_reflex_pilot.ai_demand import ai_two_week_demand_forecast


class AiDemandForecastTests(unittest.TestCase):
    def test_recent_growth_varies_and_dampens_across_periods(self):
        start = date(2026, 5, 4)
        weekly = []
        for index in range(12):
            weekly.append({
                "Strain": "Diamond Bar",
                "SKU Type": "3.5g Flower",
                "Week Starting": (start + timedelta(days=7 * index)).isoformat(),
                "Units Shipped": 40 if index >= 8 else 10,
                "Availability Signal": "Shipping",
            })
        periods = [
            {
                "crop": f"F{index + 1}.10",
                "clone_cut_date": (date(2026, 8, 28) + timedelta(days=14 * index)).isoformat(),
                "is_historical": False,
            }
            for index in range(5)
        ]
        windows = {
            "30 Days": [{"Strain": "Diamond Bar", "SKU Type": "3.5g Flower", "Avg Weekly Units": 20}],
            "60 Days": [{"Strain": "Diamond Bar", "SKU Type": "3.5g Flower", "Avg Weekly Units": 15}],
            "All Time": [{"Strain": "Diamond Bar", "SKU Type": "3.5g Flower", "Avg Weekly Units": 10}],
        }

        result = ai_two_week_demand_forecast(
            periods=periods,
            adjusted_windows=windows,
            weekly_rows=weekly,
            fallback_rows=[],
        )["diamond bar"]

        baseline = (0.45 * 20 + 0.35 * 15 + 0.20 * 10) * 3.5 / 453.59237
        self.assertAlmostEqual(result[0], 2 * baseline * 1.25)
        self.assertGreater(result[0], result[1])
        self.assertGreater(result[1], result[-1])
        self.assertGreater(result[-1], 2 * baseline)

    def test_likely_oos_week_does_not_depress_recent_trend(self):
        start = date(2026, 6, 1)
        weekly = [
            {
                "Strain": "J1",
                "SKU Type": "3.5g Flower",
                "Week Starting": (start + timedelta(days=7 * index)).isoformat(),
                "Units Shipped": 0 if index == 7 else 20,
                "Availability Signal": (
                    "Likely OOS proxy" if index == 7 else "Shipping"
                ),
            }
            for index in range(12)
        ]
        period = [{"clone_cut_date": "2026-09-01", "is_historical": False}]
        windows = {
            label: [{"Strain": "J1", "SKU Type": "3.5g Flower", "Avg Weekly Units": 20}]
            for label in ("30 Days", "60 Days", "All Time")
        }
        result = ai_two_week_demand_forecast(
            periods=period,
            adjusted_windows=windows,
            weekly_rows=weekly,
            fallback_rows=[],
        )["j1"][0]
        expected = 2 * 20 * 3.5 / 453.59237
        self.assertAlmostEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
