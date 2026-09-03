import unittest
from datetime import date, timedelta

from qcc_reflex_pilot.cultivation_registry import (
    default_bench_rows,
    default_cycle_program,
    default_room_rows,
    default_schedule,
)
from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class CloneDemandModelTest(unittest.TestCase):
    def setUp(self):
        self.state = DashboardState(_reflex_internal_init=True)
        self.state.velocity = [
            {
                "Strain": "Diamond Bar",
                "SKU Type": "3.5g Flower",
                "Avg Weekly Units": 5.0,
            }
        ]
        self.state.velocity_windows = {"All Time": list(self.state.velocity)}
        self.state.availability_adjusted_velocity_windows = {
            "All Time": [
                {
                    "Strain": "Diamond Bar",
                    "SKU Type": "3.5g Flower",
                    "Avg Weekly Units": 10.0,
                }
            ],
            "30 Days": [
                {
                    "Strain": "Diamond Bar",
                    "SKU Type": "3.5g Flower",
                    "Avg Weekly Units": 20.0,
                }
            ],
            "60 Days": [
                {
                    "Strain": "Diamond Bar",
                    "SKU Type": "3.5g Flower",
                    "Avg Weekly Units": 30.0,
                }
            ],
        }

    def test_legacy_model_name_loads_as_availability_adjusted(self):
        self.assertEqual(
            self.state._normalized_clone_demand_model(
                "Experimental Availability-Adjusted"
            ),
            "Availability-Adjusted",
        )
        self.assertEqual(
            self.state._normalized_clone_demand_model("AI-Adjusted"),
            "AI-Adjusted",
        )

    def test_clone_demand_uses_selected_adjusted_timeframe(self):
        expected_units = {
            "Availability-Adjusted": 10.0,
            "30-Day Availability-Adjusted": 20.0,
            "60-Day Availability-Adjusted": 30.0,
            "Current SKU Velocity": 5.0,
        }
        for model, units in expected_units.items():
            self.state.cultivation_clone_plan_demand_model = model
            demand = self.state._clone_plan_weekly_demand_by_strain()
            expected_lbs = units * 3.5 / 453.59237
            self.assertAlmostEqual(demand["diamond bar"], expected_lbs)

    def test_product_scope_filters_demand_without_filtering_supply(self):
        rows = [
            {
                "Strain": "Diamond Bar",
                "SKU Type": "3.5g Flower",
                "Avg Weekly Units": 20.0,
            },
            {
                "Strain": "Diamond Bar",
                "SKU Type": "1g Pre-Roll",
                "Avg Weekly Units": 100.0,
            },
        ]
        self.state.availability_adjusted_velocity_windows = {
            "All Time": rows,
            "30 Days": rows,
            "60 Days": rows,
        }
        self.state.cultivation_clone_plan_demand_model = "Availability-Adjusted"

        self.state.cultivation_clone_plan_product_scope = "Flower + Pre-Rolls"
        combined = self.state._clone_plan_weekly_demand_by_strain()["diamond bar"]
        self.state.cultivation_clone_plan_product_scope = "Flower Only"
        flower = self.state._clone_plan_weekly_demand_by_strain()["diamond bar"]
        self.state.cultivation_clone_plan_product_scope = "Pre-Rolls Only"
        preroll = self.state._clone_plan_weekly_demand_by_strain()["diamond bar"]

        self.assertAlmostEqual(combined, flower + preroll)
        self.assertAlmostEqual(flower, 20 * 3.5 / 453.59237)
        self.assertAlmostEqual(preroll, 100 / 453.59237)

    def test_current_pounds_breakdown_uses_formal_wip_and_optional_pre_wip(self):
        self.state.all_inventory = [
            {
                "Strain": "Diamond Bar",
                "Production Stage": "Packaged Goods",
                "Category": "Bud/Flower",
                "QA Status": "Test Passed",
                "License": "Manufacturing",
                "Ownership Status": "QCC-Owned",
                "Calculated Weight (g)": 453.59237,
            },
            {
                "Strain": "Diamond Bar",
                "Production Stage": "WIP-Cultivation",
                "Category": "Bud/Flower - Bulk",
                "QA Status": "Test Passed",
                "License": "Cultivation",
                "Ownership Status": "QCC-Owned",
                "Calculated Weight (g)": 907.18474,
            },
            {
                "Strain": "Diamond Bar",
                "Production Stage": "Pre-WIP-Cultivation",
                "Category": "Bud/Flower - Bulk",
                "QA Status": "Not Submitted",
                "License": "Cultivation",
                "Ownership Status": "QCC-Owned",
                "Calculated Weight (g)": 1360.77711,
            },
        ]

        breakdown = self.state._cultivation_current_inventory_breakdown_by_strain()[
            "diamond bar"
        ]

        self.assertAlmostEqual(breakdown["cpg_lbs"], 1.0)
        self.assertAlmostEqual(breakdown["wip_lbs"], 2.0)
        self.assertAlmostEqual(breakdown["pre_wip_lbs"], 3.0)
        self.assertAlmostEqual(breakdown["total_lbs"], 3.0)

        self.state.cultivation_clone_plan_include_pre_wip = True
        included = self.state._cultivation_current_inventory_breakdown_by_strain()[
            "diamond bar"
        ]
        self.assertAlmostEqual(included["total_lbs"], 6.0)

    def test_smalls_are_grouped_with_the_base_strain_in_current_pounds(self):
        self.state.all_inventory = [
            {
                "Strain": "Diamond Bar Smalls",
                "Production Stage": "Pre-WIP-Cultivation",
                "Category": "Bud/Flower - Bulk",
                "QA Status": "Not Submitted",
                "License": "Cultivation",
                "Ownership Status": "QCC-Owned / Clade9 Origin",
                "Calculated Weight (g)": 453.59237,
            },
        ]
        self.state.cultivation_clone_plan_include_pre_wip = True

        breakdown = self.state._cultivation_current_inventory_breakdown_by_strain()

        self.assertEqual(list(breakdown), ["diamond bar"])
        self.assertAlmostEqual(breakdown["diamond bar"]["pre_wip_lbs"], 1.0)
        self.assertAlmostEqual(breakdown["diamond bar"]["total_lbs"], 1.0)

    def test_missing_adjusted_window_does_not_zero_all_demand(self):
        self.state.availability_adjusted_velocity_windows = {
            "All Time": self.state.availability_adjusted_velocity_windows["All Time"]
        }
        self.state.cultivation_clone_plan_demand_model = (
            "30-Day Availability-Adjusted"
        )
        demand = self.state._clone_plan_weekly_demand_by_strain()
        expected_lbs = 10.0 * 3.5 / 453.59237
        self.assertAlmostEqual(demand["diamond bar"], expected_lbs)

    def test_background_loading_uses_current_velocity_until_adjusted_data_arrives(self):
        self.state.availability_adjusted_velocity_windows = {"All Time": []}
        self.state.cultivation_clone_plan_demand_model = "Availability-Adjusted"
        demand = self.state._clone_plan_weekly_demand_by_strain()
        expected_lbs = 5.0 * 3.5 / 453.59237
        self.assertAlmostEqual(demand["diamond bar"], expected_lbs)

    def test_cached_matrix_recalculates_when_demand_model_changes(self):
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": default_schedule(13),
            "historical_yields": [],
        }
        self.state.cultivation_registry_loaded = True

        def diamond_bar_two_week_demand():
            return next(
                row["values"][0]["value"]
                for row in self.state.cultivation_clone_plan_matrix_rows
                if row["strain"] == "Diamond Bar"
                and row["metric"] == "Two-Week Demand"
            )

        self.state.cultivation_clone_plan_demand_model = "Availability-Adjusted"
        self.state.cultivation_clone_plan_demand_revision += 1
        all_time = diamond_bar_two_week_demand()
        self.state.cultivation_clone_plan_demand_model = "30-Day Availability-Adjusted"
        self.state.cultivation_clone_plan_demand_revision += 1
        thirty_day = diamond_bar_two_week_demand()
        self.assertNotEqual(all_time, thirty_day)
        self.assertAlmostEqual(all_time, round(2 * 10 * 3.5 / 453.59237, 1))
        self.assertAlmostEqual(thirty_day, round(2 * 20 * 3.5 / 453.59237, 1))

    def test_ai_adjusted_matrix_varies_two_week_demand_by_period(self):
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": default_schedule(13),
            "historical_yields": [],
        }
        start = date(2026, 5, 4)
        self.state.availability_demand_weekly = [
            {
                "Strain": "Diamond Bar",
                "SKU Type": "3.5g Flower",
                "Week Starting": (start + timedelta(days=7 * index)).isoformat(),
                "Units Shipped": 40 if index >= 8 else 10,
                "Availability Signal": "Shipping",
            }
            for index in range(12)
        ]
        self.state.cultivation_clone_plan_demand_model = "AI-Adjusted"
        self.state.cultivation_clone_plan_demand_revision += 1

        demand_row = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Diamond Bar"
            and row["metric"] == "Two-Week Demand"
        )
        values = [cell["value"] for cell in demand_row["values"]]
        self.assertGreater(values[0], values[-1])
        self.assertGreater(len(set(values)), 1)

    def test_cultivation_navigation_keeps_loaded_demand_windows(self):
        self.state.workspace_view = "cultivation"
        self.state.sales_loaded_views = []
        expected_velocity = dict(self.state.velocity_windows)
        expected_adjusted = dict(self.state.availability_adjusted_velocity_windows)

        self.state._apply_sales_payload({
            "loaded_at": "test",
            "metrics": {
                "units": 0, "value": 0, "customers": 0, "stockouts": 0,
            },
            "brands": [], "strains": [], "sku_types": [],
            "business_pulse": [], "velocity": self.state.velocity,
            "velocity_windows": expected_velocity,
            "availability_adjusted_velocity_windows": expected_adjusted,
        })

        self.assertEqual(self.state.velocity_windows, expected_velocity)
        self.assertEqual(
            self.state.availability_adjusted_velocity_windows,
            expected_adjusted,
        )

    def test_registered_schedule_uses_workbook_history_for_lookbacks(self):
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": default_schedule(26),
            "historical_yields": [],
        }
        self.state.cultivation_registry_loaded = True

        self.state.cultivation_clone_plan_lookback = "Last 4 Crops"
        four = self.state.cultivation_clone_plan_periods
        self.assertEqual(
            [row["crop"] for row in four[:5]],
            ["F1.10", "F2.10", "F3.10", "F4.10", "F5.10"],
        )
        self.assertTrue(all(row["is_historical"] for row in four[:4]))
        self.assertTrue(four[4]["is_current"])

        self.state.cultivation_clone_plan_lookback = "Last 8 Crops"
        eight = self.state.cultivation_clone_plan_periods
        self.assertEqual(
            [row["crop"] for row in eight[:9]],
            [
                "F2.9", "F3.9", "F4.9", "F5.9", "F1.10",
                "F2.10", "F3.10", "F4.10", "F5.10",
            ],
        )
        diamond_bar = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Diamond Bar"
            and row["metric"] == "Clone Allocation"
        )
        f2_index = next(
            index for index, row in enumerate(eight) if row["crop"] == "F2.10"
        )
        self.assertEqual(diamond_bar["values"][f2_index]["value"], 1.0)


if __name__ == "__main__":
    unittest.main()
