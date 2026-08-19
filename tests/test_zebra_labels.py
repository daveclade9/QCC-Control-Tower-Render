import unittest
from datetime import date

import pandas as pd

from qcc_reflex_pilot.data import _prepare_qa_packages
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
            "production_batch_number": "DB-F4.8-07.13.2026-L1",
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
        self.assertIn("^FO80,8^GB5,16,5,B,0^FS", zpl)
        self.assertIn("^FO100,2^A0B,19,14^FB250,1,0,C,0", zpl)
        self.assertEqual(zpl.count("Total Cannabinoids:"), 2)
        self.assertLess(zpl.index("Total CBG:"), zpl.index("Total Terpenes:"))
        self.assertLess(zpl.index("Total Terpenes:"), zpl.index("Limonene:"))
        self.assertIn("^FO194,2^A0B,17,14^FB250,1,0,C,0", zpl)
        self.assertIn("^FT220,244", zpl)
        self.assertIn("^FT240,244", zpl)
        self.assertIn("1.10%    Linalool: 0.49%", zpl)
        self.assertIn("0.46%    Other: 0.83%", zpl)
        self.assertIn("1A4110300002A31000037497-A", zpl)
        self.assertNotIn("1A4110300002A31000037498-A", zpl)

    def test_production_batch_number_is_the_default_lot(self):
        context, errors = prepare_label_context(
            self.package(
                source_harvest_names="Diamond Bar Harvest 07.13.2026",
                production_batch_number="DB-F4.8-07.13.2026-L1",
            ),
            DIAMOND_ANALYTES,
            "3.5g Flower",
        )
        self.assertEqual(errors, [])
        self.assertEqual(context["lot_number"], "DB-F4.8-07.13.2026-L1")

    def test_lab_sample_resolves_bulk_production_batch_number(self):
        lab_results = pd.DataFrame([{
            "packaged_license": "C000313",
            "packaged_facility": "QCC Cultivation",
            "package_tag": "1A4110300002A31000037498",
            "source_harvest_names": "Diamond Bar Harvest 07.13.2026",
            "source_package_labels": "1A4110300002A31000037497",
            "item": "Diamond Bar Test Sample",
            "category": "Raw Plant Material",
            "lab_testing_status": "TestPassed",
            "test_date": "2026-07-20",
            "lab_facility": "Example Lab",
            "test_name": "Total THC (%)",
            "result": 32.7,
        }])
        inventory_packages = pd.DataFrame([{
            "package_tag": "1A4110300002A31000037497",
            "brand": "Clade9",
            "strain": "Diamond Bar",
            "sku_type": "Bulk Flower",
            "production_batch_number": "DB-F4.8-07.13.2026-L1",
        }])
        prepared = _prepare_qa_packages(lab_results, inventory_packages)
        self.assertEqual(
            prepared.iloc[0]["production_batch_number"],
            "DB-F4.8-07.13.2026-L1",
        )

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

    def test_required_values_accept_metrc_panel_prefixes_and_unit_suffixes(self):
        analytes = [
            {
                **row,
                "Test": "Cannabinoids - " + row["Test"].replace("(%)", "Percent"),
            }
            if row["Test"] != "Total Terpenes (%)"
            else {**row, "Test": "Terpenes - Total Terpenes Percent"}
            for row in DIAMOND_ANALYTES
        ]
        context, errors = prepare_label_context(
            self.package(), analytes, "3.5g Flower"
        )
        self.assertEqual(errors, [])
        self.assertEqual(context["analytes"]["total_thc"], 32.70)
        self.assertEqual(context["analytes"]["thca"], 27.02)
        self.assertEqual(context["analytes"]["total_terpenes"], 2.88)
        self.assertEqual(
            [name for name, _value in context["analytes"]["top_terpenes"]],
            ["Limonene", "Linalool", "Alpha-Pinene"],
        )
        self.assertAlmostEqual(context["analytes"]["other_terpenes"], 0.83)

    def test_metrc_raw_plant_material_aliases_and_total_cbg_formula(self):
        analytes = [
            row for row in DIAMOND_ANALYTES
            if row["Test"] not in {"D9-THC (%)", "Total CBG (%)"}
        ]
        analytes.extend([
            {"Test": "THC (%) Raw Plant Material", "Result": 9.00, "Passed": "Yes"},
            {"Test": "CBGa (%) Raw Plant Material", "Result": 2.00, "Passed": "Yes"},
            {"Test": "CBG (%) Raw Plant Material", "Result": 0.166, "Passed": "Yes"},
        ])
        context, errors = prepare_label_context(
            self.package(), analytes, "3.5g Flower"
        )
        self.assertEqual(errors, [])
        self.assertEqual(context["analytes"]["d9_thc"], 9.00)
        self.assertAlmostEqual(
            context["analytes"]["total_cbg"],
            2.00 * 0.877 + 0.166,
        )

    def test_reported_total_cbg_takes_priority_over_calculation(self):
        analytes = [
            *DIAMOND_ANALYTES,
            {"Test": "CBGa (%) Raw Plant Material", "Result": 10.0, "Passed": "Yes"},
            {"Test": "CBG (%) Raw Plant Material", "Result": 10.0, "Passed": "Yes"},
        ]
        context, errors = prepare_label_context(
            self.package(), analytes, "3.5g Flower"
        )
        self.assertEqual(errors, [])
        self.assertEqual(context["analytes"]["total_cbg"], 1.92)

    def test_zpl_uses_reference_print_speed_and_darkness(self):
        context, errors = prepare_label_context(
            self.package(), DIAMOND_ANALYTES, "3.5g Flower"
        )
        zpl = build_zpl(context, errors)
        self.assertIn("^PR4,4", zpl)
        self.assertIn("~SD15", zpl)

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
