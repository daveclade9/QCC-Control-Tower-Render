import os
import unittest
from unittest.mock import patch

from qcc_reflex_pilot.sales_menu import (
    _access_code_hash,
    _clean_text,
    _expected_inventory_identity,
    _package_size_sort,
    authenticate_menu_customer,
    load_customer_menu_products,
    match_menu_inventory,
    sales_menu_seed_products,
    send_order_email,
)


class SalesMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.products = sales_menu_seed_products()

    def test_supplied_menu_normalizes_all_products(self):
        self.assertEqual(len(self.products), 93)
        self.assertEqual(
            {row["brand"] for row in self.products},
            {"Clade9", "Craft Kings", "Royal Smalls", "Melt x Clade9", "Locals Only"},
        )

    def test_known_clade9_product_fields(self):
        diamond_dust = next(
            row for row in self.products
            if row["brand"] == "Clade9"
            and row["package_size"] == "1g"
            and row["strain"] == "Diamond Dust"
        )
        self.assertEqual(diamond_dust["unit_price"], 9.0)
        self.assertEqual(diamond_dust["units_per_case"], 56)
        self.assertEqual(diamond_dust["category"], "Flower")

    def test_excel_percent_artifacts_are_cleaned(self):
        self.assertEqual(_clean_text(" 29-30%%\xa0"), "29-30%")

    def test_package_sizes_sort_from_smallest_to_largest(self):
        sizes = ["28g", "3.5g", "1g", "14g", "0.5g", "7g"]
        self.assertEqual(
            sorted(sizes, key=_package_size_sort),
            ["0.5g", "1g", "3.5g", "7g", "14g", "28g"],
        )

    def test_access_codes_are_case_and_space_insensitive(self):
        self.assertEqual(_access_code_hash("Buyer 2026"), _access_code_hash("buyer2026"))

    def test_local_demo_buyer_can_preview_every_product(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            buyer = authenticate_menu_customer(" demo 2026 ")
            products = load_customer_menu_products(buyer)
        self.assertEqual(buyer["customer_id"], "DEMO-CUSTOMER")
        self.assertEqual(len(products), 93)
        self.assertTrue(all(row["available_cases"] == 25 for row in products))

    def test_order_persists_even_when_email_is_not_configured(self):
        with patch.dict(
            os.environ,
            {"QCC_MENU_RESEND_API_KEY": "", "RESEND_API_KEY": ""},
            clear=False,
        ):
            sent, message = send_order_email({}, "New order")
        self.assertFalse(sent)
        self.assertIn("not configured", message)

    def test_flower_inventory_converts_units_to_full_cases(self):
        product = next(
            row for row in self.products
            if row["brand"] == "Clade9"
            and row["package_size"] == "3.5g"
            and row["strain"] == "Diamond Bar"
        )
        matched = match_menu_inventory([product], [{
            "brand": "Clade9", "strain": "Diamond Bar",
            "sku_type": "3.5g Flower", "on_hand_units": 175,
        }])[0]
        self.assertEqual(matched["match_status"], "Matched")
        self.assertEqual(matched["metrc_on_hand_units"], 175)
        self.assertEqual(
            matched["metrc_case_equivalent"], 175 // product["units_per_case"]
        )

    def test_ambiguous_inventory_match_requires_review(self):
        product = next(
            row for row in self.products
            if row["brand"] == "Clade9"
            and row["category"] == "Pre-Rolls"
            and row["package_size"] == "1g"
            and row["strain"] == "J1"
            and "non infused" in row["product_type"].lower()
        )
        matched = match_menu_inventory([product], [
            {"brand": "Clade9", "strain": "J1", "sku_type": "1g Pre-Roll A", "on_hand_units": 10},
            {"brand": "Clade9", "strain": "J1", "sku_type": "1g Pre-Roll B", "on_hand_units": 20},
        ])[0]
        self.assertEqual(matched["match_status"], "Multiple Metrc SKU matches")

    def test_known_menu_metrc_aliases_are_explicit(self):
        cases = [
            (
                {"brand": "Clade9", "category": "Flower", "package_size": "7g", "product_type": "Low Tops (Mylar)", "strain": "Private Reserve"},
                ("Clade9", "Private Reserve OG", "7g Flower"),
            ),
            (
                {"brand": "Craft Kings", "category": "Pre-Rolls", "package_size": "1g", "product_type": "Single - Non infused", "strain": "Indica"},
                ("Craft Kings", "Indica Blend", "1g Pre-Roll"),
            ),
            (
                {"brand": "Craft Kings", "category": "Pre-Rolls", "package_size": "1g", "product_type": "Single - Cured Resin Infused", "strain": "Hybrid"},
                ("Craft Kings", "Hybrid Blend", "1g Infused Pre-Roll"),
            ),
            (
                {"brand": "Clade9", "category": "Vape Cartridges", "package_size": "1g", "product_type": "Cured Resin", "strain": "Diamond Bar"},
                ("Clade9", "Diamond Bar", "1g Vape CR"),
            ),
            (
                {"brand": "Clade9", "category": "Disposables", "package_size": "1g", "product_type": "Distillate", "strain": "Blue Dream"},
                ("Clade9", "Blue Dream", "1g Vape DC"),
            ),
            (
                {"brand": "Melt x Clade9", "category": "Disposables", "package_size": "0.5g", "product_type": "Live Rosin", "strain": "Melt x Clade9 Fig Bar"},
                ("Clade9", "Fig Bar", "1g Live Rosin"),
            ),
            (
                {"brand": "Craft Kings", "category": "Edibles", "package_size": "100mg - 10 gummies per pack", "product_type": "Hash Gummies", "strain": "Mango"},
                ("Craft Kings", "Mango", "Edibles"),
            ),
        ]
        for product, expected in cases:
            with self.subTest(product=product):
                self.assertEqual(_expected_inventory_identity(product), expected)


if __name__ == "__main__":
    unittest.main()
