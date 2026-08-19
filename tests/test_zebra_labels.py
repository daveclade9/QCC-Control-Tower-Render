import unittest
from datetime import date

from qcc_reflex_pilot.zebra_labels import (
    build_zpl,
    expiration_from_harvest,
    label_layout,
    prepare_label_context,
)


DIAMOND_ANALYTES = [
    {"Test": "Total Cannabinoids (%)", "Result": 39.93, "Passed": "Yes"},
    {"Test": "Total THC (%)", "Result": 32.70, "Passed": "Yes"},
    {"Test": "THCA (%)", "Result": 27.02, "Passed": "Yes"},
    {"Test": "Total CBD (%)", "Result": 0.06, "Passed": "Yes"},
    {"Test": "D9-THC (%)", "Result": 9.00, "Passed": "Yes"},
    {"Test": "Total CBG (%)", "Result": 1.92, "Passed": "Yes"},
    {"Test": "Total Terpenes (%)", "Result": 2.88, "Passed": "Yes"},
    {"Test": "(R)-(+)-Limonene (%)", "Result": 1.10, "Passed": "Yes"},
    {"Test": "Linalool (%)", "Result": 0.49, "Passed": "Yes"},
    {"Test": "Alpha-Pinene (%)", "Result": 0.46, "Passed": "Yes"},
]


class ZebraLabelRulesTest(unittest.TestCase):
    def package(self, **overrides):
        package = {
            "package_tag": "1A4110300002A31000037498",
            "source_package_labels": "1A4110300002A31000037497",
            "source_harvest_names": "DB-F4.8-07.13.2026-L1",
            "brand": "Clade9",
            "strain": "Diamond Bar",
            "sku_type": "Test Sample",
            "qa_outcome": "Passed",
        }
        package.update(overrides)
        return package

    def test_expiration_uses_six_calendar_months_plus_45_days(self):
        self.assertEqual(
            expiration_from_harvest(date(2026, 7, 13)),
            date(2027, 2, 27),
        )
        self.assertEqual(
            expiration_from_harvest(date(2026, 6, 15)),
            date(2027, 1, 29),
        )

    def test_clade9_35_flower_is_the_only_vertical_rule(self):
        self.assertEqual(label_layout("Clade9", "3.5g Flower"), "Flower Vertical")
        self.assertEqual(label_layout("Clade9", "7g Flower"), "Flower Horizontal")
        self.assertEqual(label_layout("Craft Kings", "3.5g Flower"), "Flower Horizontal")
        self.assertEqual(label_layout("Clade9", "1g Pre-Roll"), "Pre-Roll Horizontal")

    def test_lab_sample_results_print_the_associated_bulk_uid(self):
        context, errors = prepare_label_context(
            self.package(), DIAMOND_ANALYTES, "3.5g Flower"
        )
        self.assertEqual(errors, [])
        self.assertEqual(context["lab_tag"], "1A4110300002A31000037498")
        self.assertEqual(context["bulk_uid"], "1A4110300002A31000037497")
        self.assertEqual(context["barcode_value"], "1A4110300002A31000037497-A")
        self.assertEqual(context["expiration_date"], "2027-02-27")
        self.assertEqual(context["layout"], "Flower Vertical")
        self.assertAlmostEqual(context["analytes"]["other_terpenes"], 0.83)

        zpl = build_zpl(context, errors)
        self.assertIn("^PW457", zpl)
        self.assertIn("^LL0254", zpl)
        self.assertIn("Diamond Bar", zpl)
        self.assertIn("1A4110300002A31000037497-A", zpl)
        self.assertNotIn("1A4110300002A31000037498-A", zpl)

    def test_package_format_controls_suffix_weight_and_quantity(self):
        context, errors = prepare_label_context(
            self.package(brand="Craft Kings", strain="Hybrid Blend"),
            DIAMOND_ANALYTES,
            "1g Pre-Roll",
            quantity=12,
        )
        self.assertEqual(errors, [])
        self.assertEqual(context["suffix"], "D")
        self.assertEqual(context["net_weight"], "1g")
        self.assertEqual(context["serving_size"], "0.035oz")
        self.assertEqual(context["quantity"], 12)
        zpl = build_zpl(context, errors)
        self.assertIn("^PW812", zpl)
        self.assertIn("^LL0121", zpl)
        self.assertIn("^PQ12,0,1,Y", zpl)

    def test_missing_required_lab_values_blocks_generation(self):
        context, errors = prepare_label_context(
            self.package(), [], "3.5g Flower"
        )
        self.assertTrue(any("Missing required lab values" in error for error in errors))
        with self.assertRaises(ValueError):
            build_zpl(context, errors)

    def test_lab_sample_tag_cannot_be_used_as_printed_uid(self):
        context, errors = prepare_label_context(
            self.package(source_package_labels=""),
            DIAMOND_ANALYTES,
            "3.5g Flower",
            bulk_uid="1A4110300002A31000037498",
        )
        self.assertTrue(any("laboratory sample tag" in error for error in errors))
        self.assertEqual(context["pesticides"], "REVIEW")


if __name__ == "__main__":
    unittest.main()
