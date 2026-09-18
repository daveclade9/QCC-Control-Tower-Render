import unittest

from qcc_reflex_pilot import manufacturing_work_orders as service


class ManufacturingWorkOrderRuleTests(unittest.TestCase):
    def test_me_and_mp_close_rules_are_distinct(self):
        config = dict(service.DEFAULT_SETTINGS)
        self.assertTrue(service.should_close_work_order("ME", True, "", config))
        self.assertFalse(service.should_close_work_order("ME", False, "ATS", config))
        self.assertFalse(service.should_close_work_order("MP", True, "", config))
        self.assertTrue(service.should_close_work_order("MP", False, "listed", config))

    def test_substitution_requires_distinct_authorized_approver(self):
        config = dict(service.DEFAULT_SETTINGS)
        with self.assertRaisesRegex(ValueError, "cannot approve"):
            service.validate_substitution_approver("manager@qccnj.com", "manager@qccnj.com", "Admin", config)
        with self.assertRaisesRegex(ValueError, "configured"):
            service.validate_substitution_approver("operator@qccnj.com", "sales@qccnj.com", "Sales", config)
        service.validate_substitution_approver(
            "operator@qccnj.com", "manager@qccnj.com", "Manufacturing Manager", config
        )

    def test_number_and_quantity_configuration_validation_helpers(self):
        self.assertEqual(service.normalized_type("me"), "ME")
        self.assertEqual(service.quantity("1,250"), 1250)
        self.assertEqual(service.quantity("0", allow_zero=True), 0)
        with self.assertRaises(ValueError):
            service.quantity("0")
        with self.assertRaises(ValueError):
            service.normalized_type("PACK")

    def test_csv_and_json_configuration_lists_normalize_roles(self):
        self.assertEqual(service.json_list("Admin, manufacturing supervisor", []), [
            "ADMIN", "MANUFACTURING SUPERVISOR"
        ])
        self.assertEqual(service.json_list('["ATS", "Listed"]', []), ["ATS", "LISTED"])


if __name__ == "__main__":
    unittest.main()