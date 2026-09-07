import unittest

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class ExecutiveChartTests(unittest.TestCase):
    def test_inventory_stage_chart_uses_weight_and_package_counts(self):
        rows = [
            {
                "Production Stage": "Packaged Goods",
                "Calculated Weight (g)": 453.59237,
            },
            {
                "Production Stage": "WIP-Cultivation",
                "Calculated Weight (g)": 907.18474,
            },
        ]

        chart = DashboardState._executive_inventory_stage_chart_data(rows)

        self.assertEqual(chart[0], {"Stage": "CPG", "Pounds": 1.0, "Packages": 1})
        self.assertEqual(
            chart[2],
            {"Stage": "Cultivation WIP", "Pounds": 2.0, "Packages": 1},
        )

    def test_supply_risk_chart_applies_current_weeks_of_supply_rules(self):
        velocity = [
            {"Brand": "Clade9", "Strain": "A", "SKU Type": "3.5g", "Avg Weekly Units": 10, "Current Units": 0},
            {"Brand": "Clade9", "Strain": "B", "SKU Type": "3.5g", "Avg Weekly Units": 10, "Current Units": 30},
            {"Brand": "Clade9", "Strain": "C", "SKU Type": "3.5g", "Avg Weekly Units": 10, "Current Units": 60},
            {"Brand": "Clade9", "Strain": "D", "SKU Type": "3.5g", "Avg Weekly Units": 10, "Current Units": 100},
        ]

        chart = DashboardState._executive_supply_risk_chart_data(
            velocity, {}, False
        )[0]

        self.assertEqual(chart["Stockout"], 1)
        self.assertEqual(chart["Balanced"], 1)
        self.assertEqual(chart["Warning"], 1)
        self.assertEqual(chart["Excess"], 1)

    def test_demand_supply_chart_skips_history_and_limits_horizon(self):
        periods = [
            {"crop": "F5.10", "is_historical": True},
            {"crop": "F1.11", "clone_cut_date": "2026-09-11", "is_historical": False},
        ]
        rows = [
            {"metric": "Scheduled", "values": [{"value": 0}, {"value": 40}]},
            {"metric": "Two-Week Demand", "values": [{"value": 0}, {"value": 25}]},
            {"metric": "Current Pounds", "values": [{"value": 0}, {"value": 60}]},
        ]

        chart = DashboardState._executive_demand_supply_chart_data(periods, rows)

        self.assertEqual(len(chart), 1)
        self.assertEqual(chart[0]["Crop"], "F1.11")
        self.assertEqual(chart[0]["Scheduled Supply"], 40)
        self.assertEqual(chart[0]["Two-Week Demand"], 25)
        self.assertEqual(chart[0]["Projected Balance"], 60)

    def test_distribution_chart_keeps_package_outcomes_separate(self):
        chart = DashboardState._executive_exception_chart_data([
            {"State": "Shipped"},
            {"State": "Rejected"},
            {"State": "Rejected"},
            {"State": "Returned"},
            {"State": "Accepted"},
        ])[0]

        self.assertEqual(chart["Open"], 1)
        self.assertEqual(chart["Rejected"], 2)
        self.assertEqual(chart["Returned"], 1)


if __name__ == "__main__":
    unittest.main()
