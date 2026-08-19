import unittest
from types import SimpleNamespace

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class RetailFilterTest(unittest.TestCase):
    def setUp(self):
        self.state = SimpleNamespace(
            retail_delivery_history=[
                {"Brand": "Clade9", "Strain": "Diamond Bar"},
                {"Brand": "Clade9", "Strain": "J1"},
                {"Brand": "Clade9", "Strain": "Diamond Bar"},
                {"Brand": "Craft Kings", "Strain": "Ice Cream Cake"},
                {"Brand": "Craft Kings", "Strain": ""},
            ],
            retail_brand_filter="All Brands",
            retail_strain_filter="Ice Cream Cake",
        )
        self.state._retail_strains_for_brand = (
            lambda brand: DashboardState._retail_strains_for_brand(
                self.state, brand
            )
        )

    def test_clade9_brand_only_offers_clade9_strains(self):
        self.assertEqual(
            DashboardState._retail_strains_for_brand(self.state, "Clade9"),
            ["Diamond Bar", "J1"],
        )

    def test_all_brands_keeps_complete_sorted_strain_list(self):
        self.assertEqual(
            DashboardState._retail_strains_for_brand(
                self.state, "All Brands"
            ),
            ["Diamond Bar", "Ice Cream Cake", "J1"],
        )

    def test_brand_change_resets_an_incompatible_strain(self):
        DashboardState.change_retail_brand_filter.fn(self.state, "Clade9")

        self.assertEqual(self.state.retail_brand_filter, "Clade9")
        self.assertEqual(self.state.retail_strain_filter, "All Strains")

    def test_brand_change_preserves_a_compatible_strain(self):
        self.state.retail_strain_filter = "Diamond Bar"

        DashboardState.change_retail_brand_filter.fn(self.state, "Clade9")

        self.assertEqual(self.state.retail_strain_filter, "Diamond Bar")


if __name__ == "__main__":
    unittest.main()
