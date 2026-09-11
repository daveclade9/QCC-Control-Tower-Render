import unittest
from unittest.mock import patch
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
        self.state.cultivation_clone_plan_hide_inactive_strains = False
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

    def test_preroll_demand_combines_known_strain_aliases(self):
        rows = [
            {"Strain": "Pinetar", "SKU Type": "1g Pre Roll", "Avg Weekly Units": 45.0},
            {"Strain": "Private Reserve OG", "SKU Type": "3.5g Pre-Rolls", "Avg Weekly Units": 20.0},
        ]
        self.state.availability_adjusted_velocity_windows = {"All Time": rows}
        demand = self.state._clone_plan_weekly_demand_by_strain(
            demand_model="Availability-Adjusted", product_scope="Pre-Rolls Only"
        )
        self.assertAlmostEqual(demand["pine tar"], 45.0 / 453.59237)
        self.assertAlmostEqual(demand["private reserve"], 70.0 / 453.59237)

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.save_scheduled_mix_adjustment")
    def test_scheduled_mix_reset_persists_default_and_refreshes_forecast(self, save):
        self.state.cultivation_scheduled_mix_adjustments = {
            "f1.11|diamond bar": {
                "tops_percent": 60.0,
                "mt_smalls_percent": 25.0,
                "loss_percent": 15.0,
            }
        }
        revision = self.state.cultivation_scheduled_mix_revision

        self.state.reset_cultivation_scheduled_mix("F1.11", "Diamond Bar")

        save.assert_called_once_with(
            crop="F1.11",
            strain="Diamond Bar",
            tops_percent=75.0,
            mt_smalls_percent=20.0,
            loss_percent=5.0,
            updated_by="QCC Reflex User",
        )
        self.assertEqual(
            self.state.cultivation_scheduled_mix_adjustments[
                "f1.11|diamond bar"
            ],
            {
                "tops_percent": 75.0,
                "mt_smalls_percent": 20.0,
                "loss_percent": 5.0,
            },
        )
        self.assertEqual(
            self.state.cultivation_scheduled_mix_revision, revision + 1
        )
        self.assertIn("reset to the default", self.state.cultivation_clone_plan_message)

    def test_wip_report_default_scope_identifies_clade9_strains(self):
        self.state.cultivation_provisional_strains = ["New Clade9 Strain"]
        self.state.all_inventory = [
            {"Brand": "Clade9", "Strain": "Inventory Clade9 Strain"},
            {"Brand": "Craft Kings", "Strain": "Golden Goat"},
        ]
        self.state.velocity_windows = {
            "All Time": [
                {"Brand": "Clade9", "Strain": "Demand Clade9 Strain"},
                {"Brand": "Craft Kings", "Strain": "Sour Chem"},
            ]
        }

        keys = self.state._wip_report_clade9_strain_keys()

        self.assertIn("new clade9 strain", keys)
        self.assertIn("inventory clade9 strain", keys)
        self.assertIn("demand clade9 strain", keys)
        self.assertNotIn("golden goat", keys)
        self.assertNotIn("sour chem", keys)

    def test_current_pounds_breakdown_uses_formal_wip_and_optional_pre_wip(self):
        self.state.cultivation_clone_plan_include_pre_wip = False
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

        self.assertAlmostEqual(breakdown["cpg_flower_lbs"], 1.0)
        self.assertAlmostEqual(breakdown["wip_tops_lbs"], 2.0)
        self.assertAlmostEqual(breakdown["pre_wip_tops_lbs"], 3.0)
        self.assertAlmostEqual(breakdown["total_lbs"], 3.0)

        self.state.cultivation_clone_plan_include_pre_wip = True
        included = self.state._cultivation_current_inventory_breakdown_by_strain()[
            "diamond bar"
        ]
        self.assertAlmostEqual(included["total_lbs"], 6.0)

    def test_hide_inactive_strains_preserves_active_production_rows(self):
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": default_schedule(13),
            "historical_yields": [],
        }
        self.state.cultivation_provisional_strains = ["Dormant Test Strain"]
        self.state.cultivation_clone_plan_allocations = {"Diamond Bar": 1.0}

        visible_before = {
            row["strain"] for row in self.state.cultivation_clone_plan_matrix_rows
        }
        self.assertIn("Dormant Test Strain", visible_before)

        self.state.cultivation_clone_plan_hide_inactive_strains = True
        visible_after = {
            row["strain"] for row in self.state.cultivation_clone_plan_matrix_rows
        }
        self.assertNotIn("Dormant Test Strain", visible_after)
        self.assertIn("Diamond Bar", visible_after)

    def test_clone_planner_hides_inactive_strains_by_default(self):
        state = DashboardState(_reflex_internal_init=True)
        self.assertTrue(state.cultivation_clone_plan_hide_inactive_strains)

    def test_clone_planner_includes_pre_wip_by_default(self):
        state = DashboardState(_reflex_internal_init=True)
        self.assertTrue(state.cultivation_clone_plan_include_pre_wip)

    def test_scheduled_mix_filters_usable_supply_by_product_scope(self):
        reconciliation = {"forecast_counted_lbs": 100.0}
        combined = self.state._scheduled_reconciliation_for_scope(
            reconciliation, "F1.11", "Diamond Bar",
            product_scope="Flower + Pre-Rolls",
        )
        flower = self.state._scheduled_reconciliation_for_scope(
            reconciliation, "F1.11", "Diamond Bar",
            product_scope="Flower Only",
        )
        preroll = self.state._scheduled_reconciliation_for_scope(
            reconciliation, "F1.11", "Diamond Bar",
            product_scope="Pre-Rolls Only",
        )

        self.assertEqual(combined["forecast_counted_lbs"], 95.0)
        self.assertEqual(flower["forecast_counted_lbs"], 75.0)
        self.assertEqual(preroll["forecast_counted_lbs"], 20.0)
        self.assertEqual(combined["scheduled_loss_lbs"], 5.0)

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_manual_fresh_frozen_marks_affected_scheduled_cell(self, current_schedule_mock):
        schedule = default_schedule(13)
        current_schedule_mock.return_value = schedule[0]
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": schedule,
            "historical_yields": [],
        }
        self.state.cultivation_clone_plan_allocations = {"Diamond Bar": 1.0}
        crop = schedule[0]["crop"]
        self.state.cultivation_fresh_frozen_adjustments = {
            f"{crop.casefold()}|diamond bar": 10
        }

        scheduled_row = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Diamond Bar" and row["metric"] == "Scheduled"
        )

        self.assertTrue(
            any(cell["manual_adjustment"] for cell in scheduled_row["values"])
        )

    def test_smalls_are_grouped_with_the_base_strain_in_current_pounds(self):
        self.state.all_inventory = [
            {
                "Strain": "Diamond Bar Smalls",
                "Item": "Diamond Bar MT Smalls Bulk",
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
        self.assertAlmostEqual(
            breakdown["diamond bar"]["pre_wip_mt_smalls_lbs"], 1.0
        )
        self.assertAlmostEqual(breakdown["diamond bar"]["total_lbs"], 1.0)

    def test_actual_fresh_frozen_harvests_are_grouped_by_crop_and_strain(self):
        self.state._cultivation_plant_snapshot = {
            "harvests": [
                {
                    "harvest_batch": "Diamond Dust-F2.9-08.21.2026-WPFF",
                    "strain": "Diamond Dust",
                    "plants": 60,
                    "wet_weight_lb": 127.28,
                    "fresh_frozen": True,
                },
                {
                    "harvest_batch": "Diamond Dust-F2.9-08.22.2026-WPFF",
                    "strain": "Diamond Dust",
                    "plants": 5,
                    "wet_weight_lb": 10.0,
                    "fresh_frozen": True,
                },
            ],
        }

        actual = self.state._clone_plan_actual_fresh_frozen()[
            ("f2.9", "diamond dust")
        ]

        self.assertEqual(actual["plants"], 65)
        self.assertAlmostEqual(actual["wet_weight_lbs"], 137.28)
        self.assertEqual(len(actual["batches"]), 2)

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

    def test_manual_two_week_demand_fills_only_a_missing_velocity(self):
        periods = [
            {"is_historical": False},
            {"is_historical": False},
        ]
        self.state.cultivation_clone_plan_demand_assumptions = {
            "New Strain": 6.5,
            "Diamond Bar": 99.0,
        }

        demand = self.state._clone_plan_two_week_demand_by_strain(periods)

        self.assertEqual(demand["new strain"], [6.5, 6.5])
        self.assertNotEqual(demand["diamond bar"], [99.0, 99.0])

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_strategy_horizon_includes_full_crop_outlook_runway(
        self, current_schedule_mock
    ):
        schedule = default_schedule(26)
        current_schedule_mock.return_value = next(
            row for row in schedule if row["crop"] == "F1.11"
        )
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": schedule,
            "historical_yields": [],
        }
        self.state.cultivation_clone_plan_strategy_mode = True
        self.state.cultivation_clone_plan_strategy_horizon = "5 Crops"

        periods = self.state.cultivation_clone_plan_periods

        self.assertEqual(len(periods), 15)
        self.assertEqual(sum(bool(row["is_scenario"]) for row in periods), 4)
        self.assertEqual(sum(bool(row["is_outlook"]) for row in periods), 10)

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_strategy_extends_a_saved_schedule_for_26_crop_horizon(
        self, current_schedule_mock
    ):
        schedule = default_schedule(26)
        current_schedule_mock.return_value = schedule[0]
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": schedule,
            "historical_yields": [],
        }
        self.state.cultivation_clone_plan_strategy_mode = True
        self.state.cultivation_clone_plan_strategy_horizon = "26 Crops"

        periods = self.state.cultivation_clone_plan_periods

        self.assertEqual(len(periods), 36)
        self.assertEqual(sum(bool(row["is_scenario"]) for row in periods), 25)
        self.assertEqual(sum(bool(row["is_outlook"]) for row in periods), 10)
        self.assertEqual(len({row["crop"] for row in periods}), 36)

        last_scenario = next(row for row in reversed(periods) if row["is_scenario"])
        self.state.change_cultivation_clone_plan_strategy_allocation(
            last_scenario["crop"], "Runway Test Strain", "1.0"
        )
        scheduled = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Runway Test Strain"
            and row["metric"] == "Scheduled"
        )
        supply_indexes = [
            index for index, cell in enumerate(scheduled["values"])
            if cell["value"] > 0
        ]
        self.assertTrue(supply_indexes)
        self.assertTrue(periods[supply_indexes[0]]["is_outlook"])
        self.assertEqual(supply_indexes[0], len(periods) - 1)

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_strategy_allocation_adds_future_scheduled_supply(
        self, current_schedule_mock
    ):
        schedule = default_schedule(26)
        current_schedule_mock.return_value = next(
            row for row in schedule if row["crop"] == "F1.11"
        )
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": schedule,
            "historical_yields": [],
        }
        self.state.cultivation_clone_plan_strategy_mode = True
        self.state.cultivation_clone_plan_strategy_horizon = "5 Crops"
        baseline = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Diamond Bar" and row["metric"] == "Scheduled"
        )
        baseline_total = sum(cell["value"] for cell in baseline["values"])

        self.state.change_cultivation_clone_plan_strategy_allocation(
            "F2.11", "Diamond Bar", "1.0"
        )
        scenario = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Diamond Bar" and row["metric"] == "Scheduled"
        )

        self.assertGreater(
            sum(cell["value"] for cell in scenario["values"]), baseline_total
        )
        allocation_row = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Diamond Bar"
            and row["metric"] == "Clone Allocation"
        )
        f2_index = next(
            index for index, row in enumerate(self.state.cultivation_clone_plan_periods)
            if row["crop"] == "F2.11"
        )
        self.assertEqual(allocation_row["values"][f2_index]["value"], 1.0)
        self.assertTrue(
            allocation_row["values"][f2_index]["editable_scenario_allocation"]
        )
        self.assertEqual(
            self.state.cultivation_clone_plan_strategy_summary["crops"], "1"
        )
        self.assertEqual(
            self.state.cultivation_clone_plan_strategy_summary["benches"], "1.0"
        )

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_future_approved_plan_is_locked_in_strategy_mode(
        self, current_schedule_mock
    ):
        schedule = default_schedule(26)
        current_schedule_mock.return_value = next(
            row for row in schedule if row["crop"] == "F1.11"
        )
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": schedule,
            "historical_yields": [],
        }
        self.state.cultivation_clone_plan_history = [{
            "crop": "F2.11",
            "status": "Approved",
            "allocations": {"Diamond Bar": 1.0},
        }]
        self.state.cultivation_clone_plan_strategy_mode = True
        allocation_row = next(
            row for row in self.state.cultivation_clone_plan_matrix_rows
            if row["strain"] == "Diamond Bar"
            and row["metric"] == "Clone Allocation"
        )
        f2_index = next(
            index for index, row in enumerate(self.state.cultivation_clone_plan_periods)
            if row["crop"] == "F2.11"
        )

        self.assertEqual(allocation_row["values"][f2_index]["value"], 1.0)
        self.assertTrue(allocation_row["values"][f2_index]["approved_allocation"])
        self.assertFalse(
            allocation_row["values"][f2_index]["editable_scenario_allocation"]
        )

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_preliminary_plan_capacity_rounds_measured_canopy_up(
        self, current_schedule_mock
    ):
        current_schedule_mock.return_value = {
            "crop": "F2.11",
            "room": "Flower Room 2",
            "clone_cut_date": "2026-09-25",
            "flower_entry_date": "2026-11-04",
            "harvest_date": "2027-01-11",
            "available_date": "2027-02-10",
            "source": "Generated",
        }
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": [],
            "historical_yields": [],
        }
        self.state.cultivation_clone_plan_allocations = {"Diamond Bar": 7.0}

        self.assertEqual(self.state._clone_plan_capacity_error(), "")
        self.state.cultivation_clone_plan_allocations = {"Diamond Bar": 7.1}
        self.assertIn("7 planning benches", self.state._clone_plan_capacity_error())

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

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_registered_schedule_uses_workbook_history_for_lookbacks(
        self, current_schedule_mock
    ):
        schedule = default_schedule(26)
        current_schedule_mock.return_value = next(
            row for row in schedule if row["crop"] == "F5.10"
        )
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": schedule,
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

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.load_clone_plans")
    @patch("qcc_reflex_pilot.qcc_reflex_pilot.save_clone_plan")
    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_current_crop_approval_uses_resolved_period_in_confirmation(
        self, current_schedule_mock, save_mock, load_mock
    ):
        schedule = default_schedule(26)
        period = next(row for row in schedule if row["crop"] == "F1.11")
        current_schedule_mock.return_value = period
        save_mock.return_value = "QCC-CLONE-F1-11"
        load_mock.return_value = []
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": schedule,
            "historical_yields": [],
        }
        self.state.cultivation_registry_loaded = True
        self.state.cultivation_clone_plan_allocations = {"Diamond Bar": 1.0}
        self.state.cultivation_clone_plan_override = True
        self.state.cultivation_clone_plan_override_reason = "Approval regression test"
        self.state.auth_role = "Administrator"

        list(self.state.approve_cultivation_clone_plan())

        self.assertEqual(self.state.cultivation_clone_plan_error, "")
        self.assertIn("F1.11 Clone Allocation Plan", self.state.cultivation_clone_plan_message)
        self.assertEqual(save_mock.call_args.kwargs["crop"], "F1.11")

    @patch("qcc_reflex_pilot.qcc_reflex_pilot.load_clone_plans")
    @patch("qcc_reflex_pilot.qcc_reflex_pilot.save_clone_plan")
    @patch("qcc_reflex_pilot.qcc_reflex_pilot.current_schedule_row")
    def test_approval_saves_manual_demand_assumptions(
        self, current_schedule_mock, save_mock, load_mock
    ):
        period = next(row for row in default_schedule(26) if row["crop"] == "F1.11")
        current_schedule_mock.return_value = period
        save_mock.return_value = "QCC-CLONE-F1-11"
        load_mock.return_value = []
        self.state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": default_bench_rows(),
            "schedule": default_schedule(26),
            "historical_yields": [],
        }
        self.state.cultivation_clone_plan_allocations = {"New Strain": 1.0}
        self.state.cultivation_clone_plan_demand_assumptions = {"New Strain": 4.5}
        self.state.cultivation_clone_plan_override = True
        self.state.cultivation_clone_plan_override_reason = "Demand assumption test"
        self.state.auth_role = "Administrator"

        list(self.state.approve_cultivation_clone_plan())

        self.assertEqual(
            save_mock.call_args.kwargs["demand_assumptions"],
            {"New Strain": 4.5},
        )


if __name__ == "__main__":
    unittest.main()
