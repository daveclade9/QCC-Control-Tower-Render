import unittest
from unittest.mock import patch

import pandas as pd

from qcc_reflex_pilot.data import (
    _lab_source_discrepancies,
    _prepare_qa_packages,
    normalize_lab_summary,
    read_lab_summary_bytes,
)
from qcc_reflex_pilot.zebra_labels import label_analytes, prepare_label_context


SAMPLE_TAG = "1A4110300002A31000037744"
PARENT_TAG = "1A4110300002A31000037743"


def summary_frame(status: str = "PASSED") -> pd.DataFrame:
    return pd.DataFrame([{
        "Customer Lot": "FB5PKPR-F3.7-04.20.2026",
        "Parent Package ID": PARENT_TAG,
        "METRC #": SAMPLE_TAG,
        "Strain": "Fig Bar - 5pk Pre-Rolls 3.5g Each",
        "Upload Date": None,
        "Passed Test?": status,
        "Final CBDVA": "ND",
        "Final CBGA": 1.2864594653,
        "Final CBG": 0.0808519887,
        "Final d9-THC": 1.7037600522,
        "Final THCA": 30.5668332971,
        "Final Total Cannabinoids": 34.1586611606,
        "Final Total THC": 28.5108728537,
        "Final Total CBD": 0.0479386003,
        "Final Total CBG": 1.2103633993,
        "Final Total CBDV": 0,
        "Alpha-Pinene": 0.1172,
        "beta-Myrcene": 0.2246,
        "(R)-(+)-Limonene": 0.3203,
        "Linalool": 0.2005,
        "trans-Caryophyllene": 0.3891,
        "Total Terpenes": 2.0693,
    }])


class PreliminaryLabSummaryTest(unittest.TestCase):
    def test_two_level_headers_preserve_passed_test_field(self):
        raw = pd.DataFrame([
            [None, None],
            [None, None],
            [None, "Passed Test?"],
            ["METRC #", None],
            [SAMPLE_TAG, "PASSED"],
        ])
        with patch("qcc_reflex_pilot.data.pd.read_excel", return_value=raw):
            source = read_lab_summary_bytes(b"workbook")
        self.assertEqual(list(source.columns), ["METRC #", "Passed Test?"])
        self.assertEqual(source.iloc[0]["Passed Test?"], "PASSED")

    def test_wide_summary_maps_to_precise_label_analytes(self):
        normalized = normalize_lab_summary(
            summary_frame(), "20260828 QCC Preliminary Results Summary.xlsx", "hash"
        )
        self.assertTrue((normalized["package_tag"] == SAMPLE_TAG).all())
        self.assertTrue((normalized["source_package_labels"] == PARENT_TAG).all())
        self.assertTrue((normalized["lab_license"] == "LAB-DIRECT").all())
        self.assertTrue((normalized["test_passed"] == 1).all())
        analytes = label_analytes(normalized.rename(columns={
            "test_name": "Test", "result": "Result"
        }).to_dict("records"))
        self.assertAlmostEqual(analytes["total_thc"], 28.5108728537)
        self.assertAlmostEqual(analytes["total_cbg"], 1.2103633993)
        self.assertAlmostEqual(analytes["total_terpenes"], 2.0693)
        self.assertEqual(analytes["top_terpenes"][0][0], "Caryophyllene")

    def test_passed_summary_can_prepare_label_with_parent_uid(self):
        normalized = normalize_lab_summary(
            summary_frame(), "20260828 QCC Preliminary Results Summary.xlsx", "hash"
        )
        package = _prepare_qa_packages(normalized, pd.DataFrame()).iloc[0].to_dict()
        analyte_rows = normalized.rename(columns={
            "test_name": "Test", "result": "Result", "test_passed": "Passed"
        }).to_dict("records")
        context, errors = prepare_label_context(
            package, analyte_rows, "5pk Pre-Roll"
        )
        self.assertEqual(errors, [])
        self.assertEqual(package["record_origin"], "Lab Direct — Passed / Awaiting Metrc")
        self.assertEqual(context["lab_tag"], SAMPLE_TAG)
        self.assertEqual(context["bulk_uid"], PARENT_TAG)

    def test_failed_summary_is_stored_but_blocked_from_printing(self):
        normalized = normalize_lab_summary(
            summary_frame("FAILED"),
            "20260828 QCC Preliminary Results Summary.xlsx",
            "hash",
        )
        package = _prepare_qa_packages(normalized, pd.DataFrame()).iloc[0].to_dict()
        analyte_rows = normalized.rename(columns={
            "test_name": "Test", "result": "Result", "test_passed": "Passed"
        }).to_dict("records")
        _context, errors = prepare_label_context(
            package, analyte_rows, "5pk Pre-Roll"
        )
        self.assertEqual(package["qa_outcome"], "Failed")
        self.assertTrue(any("Only a passed compliance record" in error for error in errors))

    def test_metrc_replacement_comparison_flags_material_changes(self):
        rows = pd.DataFrame([
            {"package_tag": SAMPLE_TAG, "source_kind": "Lab Direct", "test_name": "Total THC (%)", "result": 28.511},
            {"package_tag": SAMPLE_TAG, "source_kind": "Lab Direct", "test_name": "Total Terpenes (%)", "result": 2.069},
            {"package_tag": SAMPLE_TAG, "source_kind": "Metrc", "test_name": "Total THC (%)", "result": 28.51},
            {"package_tag": SAMPLE_TAG, "source_kind": "Metrc", "test_name": "Total Terpenes (%)", "result": 2.03},
        ])
        message = _lab_source_discrepancies(rows)[SAMPLE_TAG]
        self.assertIn("Total Terpenes", message)
        self.assertNotIn("Total THC,", message)


if __name__ == "__main__":
    unittest.main()
