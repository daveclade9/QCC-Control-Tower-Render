import unittest

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


if __name__ == "__main__":
    unittest.main()
