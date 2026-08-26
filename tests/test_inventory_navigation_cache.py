import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class InventoryNavigationCacheTest(unittest.TestCase):
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

        for view_name in (
            "cpg", "bulk", "wip", "aging_cpg", "aging_bulk", "review"
        ):
            with self.subTest(view_name=view_name):
                self.assertNotIn(
                    "Source Harvest", columns_for_view(state, view_name)
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
