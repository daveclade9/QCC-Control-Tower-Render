import unittest

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class ExecutiveChartTests(unittest.TestCase):
    def test_inventory_stage_chart_uses_weight_and_package_counts(self):
        rows = [
            {
                "Production Stage": "Packaged Goods",
                "Calculated Weight (g)": 453.59237,
                "Age": 30,
            },
            {
                "Production Stage": "WIP-Cultivation",
                "Calculated Weight (g)": 907.18474,
                "Age": 80,
            },
        ]

        chart = DashboardState._executive_inventory_stage_chart_data(rows)

        self.assertEqual(chart[0]["Current Pounds"], 1.0)
        self.assertEqual(chart[0]["Aging 75+ Days"], 0.0)
        self.assertEqual(chart[0]["Packages"], 1)
        self.assertEqual(chart[2]["Current Pounds"], 0.0)
        self.assertEqual(chart[2]["Aging 75+ Days"], 2.0)
        self.assertEqual(chart[2]["Aging Packages"], 1)

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

        detail = DashboardState._executive_supply_risk_detail_data(
            velocity, {}, False
        )
        self.assertEqual({row["Risk"] for row in detail}, {
            "Stockout", "Balanced", "Warning", "Excess",
        })
        self.assertTrue(all(row["Recommended Action"] for row in detail))

    def test_inventory_detail_separates_aging_rows(self):
        detail = DashboardState._executive_inventory_detail_data([
            {
                "Production Stage": "WIP-Cultivation",
                "Calculated Weight (g)": 453.59237,
                "Age": 90,
                "Compatible Brand": "Clade9",
                "Strain": "Diamond Bar",
                "SKU Type": "Not Packaged SKU",
                "Item": "Diamond Bar Smalls",
            },
        ])

        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]["Stage"], "Cultivation WIP")
        self.assertEqual(detail[0]["Age Band"], "Aging 75+ Days")
        self.assertEqual(detail[0]["SKU / Bulk Type"], "MT Smalls")
        self.assertEqual(detail[0]["Weight (lb)"], 1.0)

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
        self.assertEqual(chart[0]["Projected Inventory"], 60)

    def test_executive_mobile_cards_preserve_priority_detail(self):
        sku_card = DashboardState._executive_mobile_card_data("SKU Risk", [{
            "Brand": "Clade9",
            "Strain": "Diamond Bar",
            "SKU Type": "3.5g Flower",
            "Current Units": 120,
            "Avg Weekly Units": 30,
            "Weeks of Supply": 4,
            "Risk": "Balanced",
            "Recommended Action": "Monitor and replenish against demand",
        }])[0]

        self.assertEqual(sku_card["title"], "Diamond Bar")
        self.assertEqual(sku_card["badge"], "Balanced")
        self.assertEqual(sku_card["label_1"], "Weeks of Supply")
        self.assertEqual(sku_card["value_1"], "4")
        self.assertIn("Monitor", sku_card["detail_value"])

        inventory_card = DashboardState._executive_mobile_card_data(
            "Inventory by Stage",
            [{
                "Stage": "Cultivation WIP",
                "Age Band": "Aging 75+ Days",
                "Brand": "Clade9",
                "Strain": "Diamond Dust",
                "SKU / Bulk Type": "MT Smalls",
                "Packages": 2,
                "Weight (lb)": 14.5,
            }],
        )[0]

        self.assertEqual(inventory_card["status"], "Aging 75+ Days")
        self.assertEqual(inventory_card["value_2"], "14.5 lb")

    def test_executive_mobile_cards_use_default_priority_sorting(self):
        inventory = DashboardState._executive_mobile_sorted_rows(
            "Inventory by Stage",
            [
                {"Strain": "Small", "Weight (lb)": 5.0},
                {"Strain": "Large", "Weight (lb)": 25.0},
                {"Strain": "Medium", "Weight (lb)": 12.5},
            ],
        )
        self.assertEqual(
            [row["Strain"] for row in inventory],
            ["Large", "Medium", "Small"],
        )

        sku_risk = DashboardState._executive_mobile_sorted_rows(
            "SKU Risk",
            [
                {"Strain": "Balanced", "Weeks of Supply": 4.0},
                {"Strain": "Excess", "Weeks of Supply": 12.0},
                {"Strain": "Warning", "Weeks of Supply": 7.0},
            ],
        )
        self.assertEqual(
            [row["Weeks of Supply"] for row in sku_risk],
            [12.0, 7.0, 4.0],
        )


if __name__ == "__main__":
    unittest.main()
