import unittest
from types import SimpleNamespace

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class InventoryNavigationCacheTest(unittest.TestCase):
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
            inventory_navigation_diagnostic="old result",
            active_inventory_all_rows=[["row 1"], ["row 2"]],
        )
        event = DashboardState.change_inventory_view.fn(
            state, "aging_bulk"
        )

        next(event)
        self.assertEqual(state.inventory_view_name, "aging_bulk")
        self.assertEqual(state.inventory_page, 1)
        self.assertEqual(state.inventory_navigation_diagnostic, "")

        with self.assertRaises(StopIteration):
            next(event)
        self.assertIn(
            "All Inventory to Aging Risk Bulk",
            state.inventory_navigation_diagnostic,
        )
        self.assertIn("2 table rows", state.inventory_navigation_diagnostic)
        self.assertIn("MB row payload", state.inventory_navigation_diagnostic)

    def test_duplicate_same_tab_event_does_not_replace_diagnostic(self):
        state = SimpleNamespace(
            inventory_view_name="aging_bulk",
            inventory_page=1,
            inventory_navigation_diagnostic=(
                "All Inventory to Aging Risk Bulk | original result"
            ),
        )
        event = DashboardState.change_inventory_view.fn(
            state, "aging_bulk"
        )

        with self.assertRaises(StopIteration):
            next(event)
        self.assertEqual(
            state.inventory_navigation_diagnostic,
            "All Inventory to Aging Risk Bulk | original result",
        )


if __name__ == "__main__":
    unittest.main()
