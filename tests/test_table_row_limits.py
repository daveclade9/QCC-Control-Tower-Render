import unittest

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class TableRowLimitTest(unittest.TestCase):
    def test_requested_operational_tables_default_to_ten_rows(self):
        state = DashboardState(_reflex_internal_init=True)

        self.assertEqual(state.executive_action_page_size, 10)
        self.assertEqual(state.top_sku_page_size, 10)
        self.assertEqual(state.stockout_page_size, 10)
        self.assertEqual(state.customer_page_size, 10)
        self.assertEqual(state.transfer_import_page_size, 10)
        self.assertEqual(state.transfer_page_size, 10)
        self.assertEqual(state.exception_page_size, 10)

    def test_standard_table_row_limit_accepts_only_supported_values(self):
        state = DashboardState(_reflex_internal_init=True)

        DashboardState.change_top_sku_rows_per_page.fn(state, "25")
        self.assertEqual(state.top_sku_rows_per_page, "25")
        self.assertEqual(state.top_sku_page_size, 25)

        DashboardState.change_top_sku_rows_per_page.fn(state, "500")
        self.assertEqual(state.top_sku_rows_per_page, "10")
        self.assertEqual(state.top_sku_page_size, 10)

    def test_server_paged_transfer_limit_resets_to_first_page(self):
        state = DashboardState(_reflex_internal_init=True)
        state.transfer_page = 4

        event = DashboardState.change_transfer_rows_per_page.fn(state, "50")
        next(event)

        self.assertEqual(state.transfer_rows_per_page, "50")
        self.assertEqual(state.transfer_page_size, 50)
        self.assertEqual(state.transfer_page, 1)


if __name__ == "__main__":
    unittest.main()
