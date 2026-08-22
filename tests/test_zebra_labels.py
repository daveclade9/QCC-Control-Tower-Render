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

    def test_g13_lab_sample_is_identified_as_clade9_and_prints_vertical_35g(self):
        lab_results = pd.DataFrame([{
            "packaged_license": "C000313",
            "packaged_facility": "QCC Cultivation",
            "package_tag": "1A4110300002A31000037125",
            "source_harvest_names": "G13 Harvest 07.13.2026",
            "source_package_labels": "1A4110300002A31000037124",
            "item": "G13 Test Sample",
            "category": "Raw Plant Material",
            "lab_testing_status": "TestPassed",
            "test_date": "2026-07-20",
            "lab_facility": "Example Lab",
            "test_name": "Total THC (%)",
            "result": 30.0,
        }])
        prepared = _prepare_qa_packages(lab_results, pd.DataFrame())
        package = prepared.iloc[0].to_dict()
        self.assertEqual(package["brand"], "Clade9")
        context, _errors = prepare_label_context(
            package, DIAMOND_ANALYTES, "3.5g Flower",
            bulk_uid="1A4110300002A31000037124",
        )
        self.assertEqual(context["layout"], "Flower Vertical")
        self.assertEqual(context["net_weight"], "3.5g")
        self.assertEqual(context["suffix"], "A")

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
        self.assertIn("^FO80,8^GB8,16,8,B,0^FS", zpl)
        self.assertIn(
            "^FO47,2^A0B,28,28^FB250,1,0,C,0^FDDiamond Bar^FS",
            zpl,
        )
        self.assertIn("^FO100,2^A0B,21,16^FB250,1,0,C,0", zpl)
        self.assertEqual(zpl.count("Total Cannabinoids:"), 2)
        self.assertLess(zpl.index("Total CBG:"), zpl.index("Total Terpenes:"))
        self.assertLess(zpl.index("Total Terpenes:"), zpl.index("Limonene:"))
        self.assertIn("^FT142,248^A0B,17,14", zpl)
        self.assertIn("^FT184,248^A0B,17,14", zpl)
        self.assertIn("^FO190,2^A0B,18,15^FB250,1,0,C,0", zpl)
        self.assertIn("^FO191,2^A0B,18,15^FB250,1,0,C,0", zpl)
        self.assertEqual(zpl.count("Total Terpenes:"), 2)
        self.assertIn("^FT225,244^A0B,17,9", zpl)
        self.assertIn("^FT246,244^A0B,17,9", zpl)
        self.assertIn("07/13/26      Expiration Date: 02/27/27", zpl)
        self.assertIn("^FT315,250^A0B,14,12", zpl)
        self.assertIn("^BY1,3,22", zpl)
        self.assertIn("1.10%    Linalool: 0.49%", zpl)
        self.assertIn("0.46%    Other: 0.83%", zpl)
        self.assertIn("1A4110300002A31000037497-A", zpl)
        self.assertNotIn("1A4110300002A31000037498-A", zpl)

    def test_vertical_strain_titles_are_centered_and_sized_to_fit(self):
        context, errors = prepare_label_context(
            self.package(), DIAMOND_ANALYTES, "3.5g Flower"
        )
        self.assertEqual(errors, [])
        cases = {
            "J1": 28,
            "Diamond Dust": 28,
            "Private Reserve OG": 26,
            "South Central Purps": 24,
        }
        for strain_name, font_size in cases.items():
            with self.subTest(strain_name=strain_name):
                context["strain"] = strain_name
                zpl = build_zpl(context, errors)
                self.assertIn(
                    f"^FO47,2^A0B,{font_size},{font_size}"
                    f"^FB250,1,0,C,0^FD{strain_name}^FS",
                    zpl,
                )

    def test_lip_smackerz_is_canonicalized_for_label_printing(self):
        context, errors = prepare_label_context(
            self.package(), DIAMOND_ANALYTES, "3.5g Flower"
        )
        self.assertEqual(errors, [])
        context["strain"] = "Lip Smackerz"

        zpl = build_zpl(context, errors)

        self.assertIn("^FDLipsmackerz^FS", zpl)
        self.assertNotIn("^FDLip Smackerz^FS", zpl)

    def test_horizontal_flower_layout_uses_the_revised_shared_positions(self):
        context, errors = prepare_label_context(
            self.package(
                production_batch_number="DB-F1.7-03.23.2026",
            ),
            DIAMOND_ANALYTES,
            "7g Flower",
        )
        self.assertEqual(errors, [])

        zpl = build_zpl(context, errors)

        self.assertIn("^FT145,29^A0N,25,28^FDDiamond Bar^FS", zpl)
        self.assertIn("^FT7,52^A0N,17,14^FDTotal Cannabinoids:", zpl)
        self.assertIn("^FT248,52^A0N,17,14^FDTotal Terpenes:", zpl)
        self.assertIn("^FT17,70^A0N,15,15^FDTotal THC:", zpl)
        self.assertIn("^FT222,70^A0N,15,13^FD", zpl)
        self.assertIn("^FT6,158^A0N,13,9^FDHarvest Date:", zpl)
        self.assertIn("^FT128,158^A0N,13,9^FDExpiration Date:", zpl)
        self.assertIn("^FT6,176^A0N,13,8^FDPesticides:", zpl)
        self.assertIn("^FT94,176^A0N,13,8^FDChemotype:", zpl)
        self.assertIn("^FDLot #: DB-F1.7-03.23.2026-L1^FS", zpl)
        self.assertIn("^FT255,155^A0N,12,12^FDClass 1 - Cultivator^FS", zpl)
        self.assertIn("^FT255,172^A0N,12,12^FDPKG by: The QCC Group LLC^FS", zpl)
        self.assertIn("^FT269,189^A0N,12,12^FDGrow Method: Indoor^FS", zpl)
        self.assertIn("^FT260,206^A0N,12,12^FDLicense Number: C000313^FS", zpl)
        self.assertIn("^BY1,3,20^FT183,227^BCN,,Y,N", zpl)
        self.assertIn("^FT5,244^A0N,13,13^FDNet WT: 7g", zpl)

    def test_horizontal_flower_lot_keeps_an_existing_lot_sequence(self):
        context, errors = prepare_label_context(
            self.package(production_batch_number="DB-F1.7-03.23.2026-L2"),
            DIAMOND_ANALYTES,
            "7g Flower",
        )
        self.assertEqual(errors, [])

        zpl = build_zpl(context, errors)

        self.assertIn("^FDLot #: DB-F1.7-03.23.2026-L2^FS", zpl)
        self.assertNotIn("-L2-L1", zpl)

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

    def test_reported_total_cbg_takes_priority_over_percent_components(self):
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

    def test_total_cbg_prefers_mg_per_g_components_and_converts_to_percent(self):
        analytes = [
            {**row, "Result": 2.16} if row["Test"] == "Total CBG (%)" else row
            for row in DIAMOND_ANALYTES
        ]
        analytes.extend([
            {"Test": "CBGa (%) Raw Plant Material", "Result": 2.00, "Passed": "Yes"},
            {"Test": "CBG (%) Raw Plant Material", "Result": 0.41, "Passed": "Yes"},
            {"Test": "CBGa (mg/g) Raw Plant Material", "Result": 20.05, "Passed": "Yes"},
            {"Test": "CBG (mg/g) Raw Plant Material", "Result": 4.08, "Passed": "Yes"},
        ])
        context, errors = prepare_label_context(
            self.package(), analytes, "3.5g Flower"
        )
        self.assertEqual(errors, [])
        self.assertAlmostEqual(
            context["analytes"]["total_cbg"],
            (20.05 * 0.877 + 4.08) / 10,
        )
        self.assertIn("Total CBG: 2.17%", build_zpl(context, errors))

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
