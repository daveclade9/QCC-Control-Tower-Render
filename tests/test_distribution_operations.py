import unittest
from unittest.mock import patch

import pandas as pd

from qcc_reflex_pilot import data


class DistributionOperationsTest(unittest.TestCase):
    def setUp(self):
        transfers = pd.DataFrame([
            {
                "Manifest": f"M{index}",
                "Brand": "Clade9" if index < 3 else "Craft Kings",
                "Strain": "Diamond Bar",
                "SKU Type": "3.5g Flower",
                "Package Tag": f"TAG{index}",
            }
            for index in range(5)
        ])
        packages = pd.DataFrame([
            {
                "Manifest": "OPEN1",
                "State": "Shipped",
                "Brand": "Clade9",
                "Strain": "J1",
                "SKU Type": "7g Flower",
                "Package Tag": "OPEN-TAG",
                "Shipper Value": 50,
            },
            {
                "Manifest": "REJECT1",
                "State": "Rejected",
                "Brand": "Clade9",
                "Strain": "J1",
                "SKU Type": "7g Flower",
                "Package Tag": "REJECT-TAG",
                "Shipper Value": 75,
            },
        ])
        summaries = pd.DataFrame([
            {"Manifest": "OPEN1", "State": "Shipped"},
            {"Manifest": "REJECT1", "State": "Rejected"},
        ])
        with data._DISTRIBUTION_CACHE_LOCK:
            data._DISTRIBUTION_CACHE.update({
                "loaded_at": 1.0,
                "transfers": transfers,
                "exceptions": summaries,
                "exception_packages": packages,
            })

    @patch("qcc_reflex_pilot.data.get_sales_dashboard_data")
    def test_transfer_page_is_filtered_and_sliced_on_server(self, sales):
        sales.return_value = {"transfer_import_log": [{"source_rows": 5}]}
        payload = data.get_distribution_operations_data(
            "transfers",
            page=2,
            page_size=2,
            brand_filter="Clade9",
        )

        self.assertEqual(payload["transfer_total"], 3)
        self.assertEqual(payload["transfer_page"], 2)
        self.assertEqual(len(payload["transfer_data"]), 1)
        self.assertEqual(payload["transfer_data"][0]["Package Tag"], "TAG2")

    @patch("qcc_reflex_pilot.data.get_sales_dashboard_data")
    def test_exception_request_returns_only_selected_package_state(self, sales):
        sales.return_value = {}
        payload = data.get_distribution_operations_data(
            "exceptions",
            exception_state="Rejected",
            page=1,
            page_size=1,
        )

        self.assertEqual(len(payload["exception_packages"]), 1)
        self.assertEqual(payload["exception_total"], 1)
        self.assertEqual(payload["exception_manifests"], 1)
        self.assertEqual(payload["exception_value"], 75)
        self.assertEqual(
            payload["exception_packages"][0]["Package Tag"], "REJECT-TAG"
        )
        self.assertEqual(payload["exceptions"][0]["Manifest"], "REJECT1")


if __name__ == "__main__":
    unittest.main()
