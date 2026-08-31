import unittest

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


if __name__ == "__main__":
    unittest.main()
