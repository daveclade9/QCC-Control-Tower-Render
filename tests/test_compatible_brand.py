import unittest

from qcc_reflex_pilot.rules import compatible_inventory_brand


class CompatibleBrandRulesTest(unittest.TestCase):
    def row(self, **overrides):
        row = {
            "Production Stage": "WIP-Cultivation",
            "Current Facility": "Building 33 (C9)",
            "Ownership Status": "QCC-Owned / Internal",
            "Strain": "Ice Cream Cake",
            "Item": "Bulk Flower",
            "Category": "Bud/Flower (Bulk)",
            "Brand": "Unbranded / Bulk",
        }
        row.update(overrides)
        return row

    def test_partner_owned_building_1a_is_not_brand_compatible(self):
        row = self.row(
            **{
                "Current Facility": "Building 1A",
                "Ownership Status": "Partner-Owned / Compliance Managed",
            }
        )
        self.assertEqual(compatible_inventory_brand(row), "ROFR / Not Purchased")

    def test_purchased_1a_material_in_building_33_remains_unallocated(self):
        row = self.row(
            **{"Ownership Status": "QCC-Owned / Purchased from Building 1A"}
        )
        self.assertEqual(compatible_inventory_brand(row), "Unallocated QCC Brand")

    def test_diamond_bar_uses_established_clade9_rule(self):
        row = self.row(Strain="Diamond Bar")
        self.assertEqual(
            compatible_inventory_brand(row, {"diamond bar": "Craft Kings"}),
            "Clade9",
        )

    def test_unique_finished_demand_can_map_building_33_wip(self):
        row = self.row(Strain="Ice Cream Cake")
        self.assertEqual(
            compatible_inventory_brand(row, {"ice cream cake": "Craft Kings"}),
            "Craft Kings",
        )

    def test_blend_source_exception_does_not_relabel_generic_bulk(self):
        row = self.row(Strain="Generic Source Flower")
        self.assertEqual(compatible_inventory_brand(row), "Clade9")


if __name__ == "__main__":
    unittest.main()
