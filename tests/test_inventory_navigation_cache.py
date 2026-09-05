import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class InventoryNavigationCacheTest(unittest.TestCase):
    def test_mt_smalls_filter_requires_cultivation_wip_and_smalls_item(self):
        eligible_wip = {
            "Production Stage": "WIP-Cultivation",
            "Item": "Diamond Bar Smalls Bulk",
        }
        eligible_pre_wip = {
            "Production Stage": "Pre-WIP-Cultivation",
            "Item": "Blue Dream SMALLS",
        }
        excluded_rows = [
            {"Production Stage": "WIP-Cultivation", "Item": "Diamond Bar Bulk"},
            {"Production Stage": "WIP-Manufacturing", "Item": "Diamond Bar Smalls"},
            {"Production Stage": "Pre-WIP-Purchased 1A", "Item": "J1 Smalls"},
        ]

        self.assertTrue(DashboardState._is_mt_smalls(eligible_wip))
        self.assertTrue(DashboardState._is_mt_smalls(eligible_pre_wip))
        for row in excluded_rows:
            with self.subTest(row=row):
                self.assertFalse(DashboardState._is_mt_smalls(row))

    def test_mt_smalls_is_available_as_an_inventory_category(self):
        state = SimpleNamespace(
            all_inventory=[{"Category": "Bud/Flower - Bulk"}],
            _inventory_options=lambda column, all_label: [
                all_label, "Bud/Flower - Bulk"
            ],
        )
        options = DashboardState.inventory_category_options.fget(state)

        self.assertEqual(options[:2], ["All Categories", "MT Smalls"])

    def test_mt_smalls_summary_totals_only_eligible_weight(self):
        rows = [
            {
                "Production Stage": "WIP-Cultivation",
                "Item": "Diamond Bar Smalls",
                "Calculated Weight (g)": 453.59237,
            },
            {
                "Production Stage": "Pre-WIP-Cultivation",
                "Item": "Blue Dream Smalls Bulk",
                "Calculated Weight (g)": 226.796185,
            },
            {
                "Production Stage": "WIP-Cultivation",
                "Item": "Diamond Bar Bulk Flower",
                "Calculated Weight (g)": 4535.9237,
            },
        ]

        self.assertEqual(
            DashboardState._mt_smalls_weight_summary(rows),
            "1.5 lb",
        )

    def test_all_inventory_stage_summary_uses_only_the_requested_stage(self):
        rows = [
            {
                "Production Stage": "Pre-WIP-Cultivation",
                "Calculated Weight (g)": 453.59237,
            },
            {
                "Production Stage": "Pre-WIP-Cultivation",
                "Calculated Weight (g)": 226.796185,
            },
            {
                "Production Stage": "WIP-Cultivation",
                "Calculated Weight (g)": 4535.9237,
            },
        ]

        self.assertEqual(
            DashboardState._stage_package_weight_summary(
                rows, "Pre-WIP-Cultivation"
            ),
            "2 pkg / 1.5 lb",
        )

    def test_purchased_1a_wip_summaries_remain_separate(self):
        rows = [
            {
                "Production Stage": "WIP-Purchased 1A",
                "Calculated Weight (g)": 453.59237,
            },
            {
                "Production Stage": "Pre-WIP-Purchased 1A",
                "Calculated Weight (g)": 907.18474,
            },
        ]

        self.assertEqual(
            DashboardState._stage_package_weight_summary(
                rows, "WIP-Purchased 1A"
            ),
            "1 pkg / 1.0 lb",
        )
        self.assertEqual(
            DashboardState._stage_package_weight_summary(
                rows, "Pre-WIP-Purchased 1A"
            ),
            "1 pkg / 2.0 lb",
        )

    def test_all_inventory_does_not_show_the_samples_metric(self):
        getter = DashboardState.active_inventory_shows_samples.fget
        state = SimpleNamespace(inventory_view_name="all")
        self.assertFalse(getter(state))

        for view_name in ("cpg", "aging_cpg"):
            with self.subTest(view_name=view_name):
                state.inventory_view_name = view_name
                self.assertTrue(getter(state))

    def test_all_inventory_cpg_weight_is_split_by_license(self):
        rows = [
            {
                "Production Stage": "Packaged Goods",
                "License": "Cultivation",
                "Calculated Weight (g)": 453.59237,
            },
            {
                "Production Stage": "Packaged Goods",
                "License": "Manufacturing",
                "Calculated Weight (g)": 907.18474,
            },
            {
                "Production Stage": "WIP-Cultivation",
                "License": "Cultivation",
                "Calculated Weight (g)": 4535.9237,
            },
        ]
        state = SimpleNamespace(
            filtered_all_inventory=rows,
            _filtered_weight_total=lambda selected: (
                f"{sum(row['Calculated Weight (g)'] for row in selected) / 453.59237:,.1f} lb"
            ),
        )

        self.assertEqual(
            DashboardState._all_inventory_cpg_weight_by_license(
                state, "Cultivation"
            ),
            "1.0 lb",
        )
        self.assertEqual(
            DashboardState._all_inventory_cpg_weight_by_license(
                state, "Manufacturing"
            ),
            "2.0 lb",
        )

    def test_source_harvest_is_added_only_to_all_inventory(self):
        state = SimpleNamespace(inventory_weight_unit="Pounds")
        columns_for_view = DashboardState._inventory_columns_for_view

        all_columns = columns_for_view(state, "all")
        self.assertEqual(
            all_columns[all_columns.index("QA Status") - 1],
            "Source Harvest",
        )
        self.assertEqual(
            all_columns[all_columns.index("QA Status") + 1],
            "Metrc Tag",
        )
        self.assertEqual(
            all_columns[all_columns.index("Age (Days)") + 1],
            "Packaged Date",
        )
        self.assertEqual(
            all_columns[all_columns.index("Packaged Date") + 1],
            "Location",
        )

        for view_name in (
            "cpg", "bulk", "wip", "aging_cpg", "aging_bulk", "review"
        ):
            with self.subTest(view_name=view_name):
                self.assertNotIn(
                    "Source Harvest", columns_for_view(state, view_name)
                )
                self.assertNotIn(
                    "Packaged Date", columns_for_view(state, view_name)
                )

    def test_active_view_reuses_its_cached_row_matrix(self):
        state = SimpleNamespace(
            inventory_view_name="cpg",
            cpg_inventory_rows=[["cpg"]],
            bulk_inventory_rows=[["bulk"]],
            wip_inventory_rows=[["wip"]],
            aging_cpg_rows=[["aging_cpg"]],
            aging_bulk_rows=[["aging_bulk"]],
            all_inventory_rows=[["all"]],
            needs_review_rows=[["review"]],
        )
        getter = DashboardState.active_inventory_all_rows.fget
        expected_by_view = {
            "cpg": state.cpg_inventory_rows,
            "bulk": state.bulk_inventory_rows,
            "wip": state.wip_inventory_rows,
            "aging_cpg": state.aging_cpg_rows,
            "aging_bulk": state.aging_bulk_rows,
            "all": state.all_inventory_rows,
            "review": state.needs_review_rows,
        }

        for view_name, expected in expected_by_view.items():
            with self.subTest(view_name=view_name):
                state.inventory_view_name = view_name
                self.assertIs(getter(state), expected)

    def test_navigation_diagnostic_records_slowest_view_transition(self):
        state = SimpleNamespace(
            inventory_view_name="all",
            inventory_page=3,
            active_inventory_all_rows=[["row 1"], ["row 2"]],
        )
        event = DashboardState.change_inventory_view.fn(
            state, "aging_bulk"
        )

        next(event)
        self.assertEqual(state.inventory_view_name, "aging_bulk")
        self.assertEqual(state.inventory_page, 1)

        output = StringIO()
        with redirect_stdout(output), self.assertRaises(StopIteration):
            next(event)
        self.assertIn(
            "All Inventory to Aging Risk Bulk",
            output.getvalue(),
        )
        self.assertIn("2 table rows", output.getvalue())

    def test_duplicate_same_tab_event_is_a_no_op(self):
        state = SimpleNamespace(
            inventory_view_name="aging_bulk",
            inventory_page=4,
        )
        event = DashboardState.change_inventory_view.fn(
            state, "aging_bulk"
        )

        with self.assertRaises(StopIteration):
            next(event)
        self.assertEqual(state.inventory_view_name, "aging_bulk")
        self.assertEqual(state.inventory_page, 4)

    def test_slowest_inventory_views_are_both_prewarmed(self):
        state = SimpleNamespace(
            all_inventory_rows=[["all 1"], ["all 2"]],
            aging_bulk_rows=[["aging"]],
        )

        all_count, aging_count, elapsed_ms = (
            DashboardState._prewarm_slowest_inventory_views(state)
        )

        self.assertEqual(all_count, 2)
        self.assertEqual(aging_count, 1)
        self.assertGreaterEqual(elapsed_ms, 0)


if __name__ == "__main__":
    unittest.main()
