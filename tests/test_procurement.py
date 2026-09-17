import unittest

from qcc_reflex_pilot.procurement import (
    box_label_zpl,
    quantity_label_zpl,
    reorder_quantity,
    supply_seed_items,
    supply_seed,
    whole_number,
)


class ProcurementTests(unittest.TestCase):
    def test_supply_seed_contains_every_source_workbook_item(self):
        items = supply_seed_items()
        self.assertEqual(len(items), 88)
        self.assertEqual(len({row["item_id"] for row in items}), 88)
        self.assertTrue(all(row["description"] == row["description"].upper() for row in items))
        self.assertEqual(int(supply_seed()["count_cadence_days"]), 14)


    def test_scanner_quantity_accepts_digits_and_zero(self):
        for value, expected in (("0", 0), ("25", 25), ("1,000", 1000)):
            with self.subTest(value=value):
                self.assertEqual(whole_number(value), expected)


    def test_scanner_quantity_rejects_non_digit_barcodes(self):
        for value in ("ABC123", "12.5", "-1", "", "  "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "digits only"):
                    whole_number(value)


    def test_source_reorder_formula_is_preserved(self):
        self.assertEqual(reorder_quantity(on_hand=3, safety_stock=5, order_multiple=12), 24)
        self.assertEqual(reorder_quantity(on_hand=8, safety_stock=5, order_multiple=12), 0)


    def test_box_label_keeps_five_separate_qr_values(self):
        zpl = box_label_zpl("PKG-100", "3.5G GLASS JAR", "QCC-PO-2026-0001", "120", "2026-09-16")
        self.assertEqual(zpl.count("^BQN"), 5)
        for value in ("2026-09-16", "PKG-100", "3.5G GLASS JAR", "QCC-PO-2026-0001", "120"):
            self.assertIn(value, zpl)


    def test_quantity_label_has_separate_item_and_quantity_qr_values(self):
        zpl = quantity_label_zpl("PKG-100", "0")
        self.assertEqual(zpl.count("^BQN"), 2)
        self.assertIn("PKG-100", zpl)
        self.assertIn("QTY: 0", zpl)


if __name__ == "__main__":
    unittest.main()
