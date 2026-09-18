import unittest
from unittest.mock import patch

from qcc_reflex_pilot.procurement import (
    box_label_zpl,
    purchase_order_pdf,
    quantity_label_zpl,
    reorder_quantity,
    set_purchase_order_active,
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


    def test_on_hand_field_accepts_only_nonnegative_whole_numbers(self):
        self.assertEqual(whole_number("0", "On Hand"), 0)
        self.assertEqual(whole_number("125", "On Hand"), 125)
        for value in ("-1", "12.5", "ABC"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "On Hand must contain digits only"):
                    whole_number(value, "On Hand")


    def test_source_reorder_formula_is_preserved(self):
        self.assertEqual(reorder_quantity(on_hand=3, safety_stock=5, order_multiple=12), 24)
        self.assertEqual(reorder_quantity(on_hand=8, safety_stock=5, order_multiple=12), 0)


    def test_box_label_keeps_five_separate_qr_values(self):
        zpl = box_label_zpl("PKG-100", "3.5G GLASS JAR", "QCC-PO-2026-0001", "120", "2026-09-16")
        self.assertEqual(zpl.count("^BQB"), 5)
        self.assertIn("^PW812", zpl)
        self.assertIn("^LL1218", zpl)
        self.assertIn("^FWB", zpl)
        for value in ("2026-09-16", "PKG-100", "3.5G GLASS JAR", "QCC-PO-2026-0001", "120"):
            self.assertIn(value, zpl)


    def test_quantity_label_has_separate_item_and_quantity_qr_values(self):
        zpl = quantity_label_zpl("PKG-100", "0")
        self.assertEqual(zpl.count("^BQN"), 2)
        self.assertIn("PKG-100", zpl)
        self.assertIn("QTY: 0", zpl)

    def test_purchase_order_activity_change_requires_reason(self):
        with self.assertRaisesRegex(ValueError, "reason is required"):
            set_purchase_order_active(
                "QCC-PO-2026-0001",
                active=False,
                reason="",
                actor="TESTER",
            )

    @patch(
        "qcc_reflex_pilot.procurement.packaging_suppliers",
        return_value=[{
            "supplier": "COVERED GROUP",
            "address_line_1": "3401 GLENDALE BLVD, UNIT C",
            "city": "LOS ANGELES",
            "state": "CA",
            "postal_code": "90036",
            "country": "U.S.A.",
            "contact_phone": "213-216-4730",
            "contact_email": "brad@covered.group",
        }],
    )
    @patch(
        "qcc_reflex_pilot.procurement.purchase_order_detail",
        return_value={
            "header": {
                "supplier": "COVERED GROUP",
                "order_date": "2026-09-17",
                "required_date": "2026-09-30",
                "contact_email": "brad@covered.group",
                "status": "DRAFT",
                "standard_shipping": 10,
                "expedited_shipping": 0,
                "sales_tax": 5,
                "notes": "DELIVER TO DOOR 3",
            },
            "lines": [{
                "item_id": "PKG-100",
                "description": "3.5G GLASS JAR",
                "ordered_quantity": 120,
                "uom": "EACH",
                "unit_cost": 1.25,
            }],
        },
    )
    def test_purchase_order_pdf_builds_with_supplier_party_block(
        self, _detail, _suppliers
    ):
        pdf = purchase_order_pdf("QCC-PO-2026-0001")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 2_000)


if __name__ == "__main__":
    unittest.main()
