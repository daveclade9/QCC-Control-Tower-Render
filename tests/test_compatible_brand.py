import unittest

from qcc_reflex_pilot.rules import (
    CLADE9_COMPATIBLE_BULK_STRAINS,
    compatible_inventory_brand,
)


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
        self.assertEqual(compatible_inventory_brand(row), "Clade9")

    def test_smalls_grade_uses_the_base_strain_brand_rule(self):
        for strain in CLADE9_COMPATIBLE_BULK_STRAINS:
            with self.subTest(strain=strain):
                row = self.row(
                    Strain=f"{strain} Smalls",
                    Item=f"{strain} Smalls Bulk",
                )
                self.assertEqual(compatible_inventory_brand(row), "Clade9")

    def test_unapproved_strain_is_not_inferred_from_demand(self):
        row = self.row(Strain="Ice Cream Cake")
        self.assertEqual(
            compatible_inventory_brand(row), "Compatibility Needs Review"
        )

    def test_all_approved_clade9_strains_are_supported(self):
        strains = [
            "J1", "Fig Bar", "Orange Push Pop", "Diamond Bar",
            "Diamond Dust", "Lemon Cherry Gelato", "G13",
            "Private Reserve", "Tahoe OG", "Blue Dream",
            "Razberry Runtz", "Brooklyn Runtz", "South Central Purps",
            "Lipsmackerz", "Pinetar", "LA Piff",
        ]
        self.assertEqual(len(CLADE9_COMPATIBLE_BULK_STRAINS), 16)
        for strain in strains:
            with self.subTest(strain=strain):
                self.assertEqual(
                    compatible_inventory_brand(self.row(Strain=strain)),
                    "Clade9",
                )

    def test_blend_source_exception_does_not_relabel_generic_bulk(self):
        row = self.row(Strain="Generic Source Flower", Item="Clade9 Bulk Flower")
        self.assertEqual(
            compatible_inventory_brand(row), "Compatibility Needs Review"
        )

    def test_building_1a_origin_wins_over_approved_strain(self):
        for strain in ["Lemon Cherry Gelato", "Blue Dream"]:
            with self.subTest(strain=strain):
                row = self.row(
                    Strain=strain,
                    **{
                        "Ownership Status":
                            "QCC-Owned / Purchased from Building 1A"
                    },
                )
                self.assertEqual(
                    compatible_inventory_brand(row), "Unallocated QCC Brand"
                )

        row = self.row(
            Strain="Diamond Bar",
            Facility="Building 1A",
            **{"Ownership Status": "QCC-Owned / Internal"},
        )
        self.assertEqual(
            compatible_inventory_brand(row), "Unallocated QCC Brand"
        )


if __name__ == "__main__":
    unittest.main()
