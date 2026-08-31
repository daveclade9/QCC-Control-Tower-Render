import os
import json
import unittest
from unittest.mock import MagicMock, patch

from qcc_reflex_pilot.sales_menu import (
    MenuAdminState,
    ORDER_STATUS_APPROVED,
    ORDER_STATUS_PENDING,
    _access_code_hash,
    _clean_text,
    _expected_inventory_identity,
    _package_size_sort,
    authenticate_menu_customer,
    load_customer_menu_products,
    match_menu_inventory,
    sales_menu_seed_products,
    save_menu_product_review,
    send_order_email,
    undo_menu_order_approval,
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
            {
                "QCC_MENU_SMTP_HOST": "",
                "QCC_MENU_SMTP_USERNAME": "",
                "QCC_MENU_SMTP_APP_PASSWORD": "",
                "QCC_MENU_RESEND_API_KEY": "",
                "RESEND_API_KEY": "",
            },
            clear=False,
        ):
            sent, message = send_order_email({}, "New order")
        self.assertFalse(sent)
        self.assertIn("not configured", message)

    def test_order_email_accepts_multiple_internal_recipients(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with patch.dict(
            os.environ,
            {
                "QCC_MENU_RESEND_API_KEY": "re_test",
                "QCC_MENU_ORDER_EMAIL_TO": "sales@clade9.com, dave@clade9.com",
            },
            clear=False,
        ), patch("qcc_reflex_pilot.sales_menu.urlopen", return_value=response) as send:
            sent, _ = send_order_email(
                {"order_number": "TEST-1", "email": "buyer@example.com", "items": []},
                "New order",
            )
        self.assertTrue(sent)
        payload = json.loads(send.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(
            payload["to"],
            ["sales@clade9.com", "dave@clade9.com", "buyer@example.com"],
        )

    def test_order_email_prefers_google_workspace_smtp(self):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        with patch.dict(
            os.environ,
            {
                "QCC_MENU_SMTP_HOST": "smtp.gmail.com",
                "QCC_MENU_SMTP_PORT": "587",
                "QCC_MENU_SMTP_USERNAME": "orders@clade9.com",
                "QCC_MENU_SMTP_APP_PASSWORD": "abcd efgh ijkl mnop",
                "QCC_MENU_EMAIL_FROM": "orders@clade9.com",
                "QCC_MENU_ORDER_EMAIL_TO": "dave@clade9.com",
                "QCC_MENU_RESEND_API_KEY": "re_fallback",
            },
            clear=False,
        ), patch("qcc_reflex_pilot.sales_menu.smtplib.SMTP", return_value=smtp) as connect:
            sent, message = send_order_email(
                {"order_number": "TEST-2", "email": "buyer@example.com", "items": []},
                "New order",
            )
        self.assertTrue(sent)
        self.assertIn("Google Workspace", message)
        connect.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
        smtp.starttls.assert_called_once_with()
        smtp.login.assert_called_once_with("orders@clade9.com", "abcdefghijklmnop")
        sent_message = smtp.send_message.call_args.args[0]
        self.assertEqual(sent_message["To"], "dave@clade9.com, buyer@example.com")

    def test_product_review_recalculates_full_cases_and_publication(self):
        connection = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        with patch(
            "qcc_reflex_pilot.sales_menu.ensure_sales_menu_schema", return_value=True
        ), patch(
            "qcc_reflex_pilot.sales_menu.database_url", return_value="postgresql://test"
        ), patch(
            "qcc_reflex_pilot.sales_menu.psycopg.connect", return_value=connection
        ):
            save_menu_product_review(
                "MENU-TEST",
                brand="Clade9",
                category="Flower",
                package_size="3.5g",
                product_type="Flower",
                strain="Diamond Bar",
                unit_price=25,
                units_per_case=28,
                notes="Reviewed",
                is_active=True,
                updated_by="QCC Tester",
            )
        statement, parameters = cursor.execute.call_args.args
        self.assertIn("metrc_case_equivalent", statement)
        self.assertIn("available_cases", statement)
        self.assertEqual(parameters[8], True)
        self.assertEqual(parameters[9:11], (28, 28))
        connection.commit.assert_called_once_with()

    def test_product_review_requires_valid_case_configuration(self):
        with self.assertRaisesRegex(ValueError, "Units per case"):
            save_menu_product_review(
                "MENU-TEST",
                brand="Clade9",
                category="Flower",
                package_size="3.5g",
                product_type="Flower",
                strain="Diamond Bar",
                unit_price=25,
                units_per_case=0,
                notes="",
                is_active=False,
                updated_by="QCC Tester",
            )

    def test_undo_approval_restores_exact_cases_and_returns_order_to_pending(self):
        connection = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        cursor.fetchone.return_value = (ORDER_STATUS_APPROVED,)
        cursor.fetchall.return_value = [("MENU-A", 2), ("MENU-B", 3)]
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        order = {"order_id": "ORDER-1", "status": ORDER_STATUS_PENDING}
        with patch(
            "qcc_reflex_pilot.sales_menu.ensure_sales_menu_schema", return_value=True
        ), patch(
            "qcc_reflex_pilot.sales_menu.database_url", return_value="postgresql://test"
        ), patch(
            "qcc_reflex_pilot.sales_menu.psycopg.connect", return_value=connection
        ), patch(
            "qcc_reflex_pilot.sales_menu._order_summary", return_value=order
        ), patch(
            "qcc_reflex_pilot.sales_menu.send_order_email"
        ) as email:
            result = undo_menu_order_approval(
                "ORDER-1", reviewed_by="QCC Tester"
            )
        restore_calls = [
            call for call in cursor.execute.call_args_list
            if "available_cases +" in call.args[0]
        ]
        self.assertEqual(len(restore_calls), 2)
        self.assertEqual(restore_calls[0].args[1], (2, 2, "QCC Tester", "MENU-A"))
        self.assertEqual(restore_calls[1].args[1], (3, 3, "QCC Tester", "MENU-B"))
        status_calls = [
            call for call in cursor.execute.call_args_list
            if "SET status" in call.args[0]
        ]
        self.assertEqual(status_calls[0].args[1][0], ORDER_STATUS_PENDING)
        connection.commit.assert_called_once_with()
        email.assert_called_once()
        self.assertEqual(result, order)

    def test_menu_quantities_create_bold_sku_type_blocks(self):
        state = MenuAdminState(_reflex_internal_init=True)
        state.products = [
            {
                "product_id": "A", "brand": "Clade9", "category": "Flower",
                "package_size": "3.5g", "product_type": "Flower", "strain": "J1",
                "available_cases": 2, "sku_filter_label": "3.5g Flower",
            },
            {
                "product_id": "B", "brand": "Clade9", "category": "Flower",
                "package_size": "3.5g", "product_type": "Flower", "strain": "G13",
                "available_cases": 2, "sku_filter_label": "3.5g Flower",
            },
            {
                "product_id": "C", "brand": "Clade9", "category": "Flower",
                "package_size": "7g", "product_type": "Flower", "strain": "G13",
                "available_cases": 1, "sku_filter_label": "7g Flower",
            },
        ]
        rows = state.filtered_products
        self.assertEqual(
            [row["sku_block_label"] for row in rows],
            ["Clade9 · 3.5g Flower", "Clade9 · 3.5g Flower", "Clade9 · 7g Flower"],
        )
        self.assertEqual(
            [row["starts_sku_block"] for row in rows],
            [True, False, True],
        )

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
