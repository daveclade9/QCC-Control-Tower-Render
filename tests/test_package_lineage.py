import unittest
from unittest.mock import patch

import pandas as pd

from qcc_reflex_pilot.data import load_package_lineage


class PackageLineageTest(unittest.TestCase):
    def test_combines_source_snapshot_and_transfer_history(self):
        tag = "1A4110300002A31000038760"
        source_tag = "1A4110300002A31000030001"
        transfers = pd.DataFrame([{
            "manifest": "0001409482",
            "invoice_number": "3394",
            "origin_license": "C000313",
            "origin_facility": "The QCC Group LLC",
            "destination_license": "RE000108",
            "destination_facility": "Hackettstown Dispensary LLC",
            "transfer_type": "Wholesale Transfer",
            "created_at": "2026-08-26T07:58:04",
            "received_at": "2026-08-26T10:37:09",
            "package_tag": tag,
            "state": "Rejected",
            "item": "Fig Bar Packaged 3.5g EA",
            "item_category": "Bud/Flower - Packaged",
            "shipper_dollar_amount": 960,
            "actual_shipped": 32,
            "actual_shipped_uom": "ea",
            "actual_received": 0,
            "actual_received_uom": "ea",
        }])
        observations = pd.DataFrame([{
            "package_tag": tag,
            "source_packages": source_tag,
            "source_harvest": "Fig Bar-F4.8-07.13.2026",
            "production_batch_number": "FB-F4.8-07.13.2026",
            "source_production_batch": "",
            "item": "Fig Bar Packaged 3.5g EA",
            "brand": "Clade9",
            "strain": "Fig Bar",
            "quantity": 32,
            "unit": "ea",
            "location": "Vault",
            "qa_status": "TestPassed",
            "production_stage": "Packaged Goods",
            "expiration_date": "2027-02-27",
            "source_license_number": "C000313",
            "business_date": "2026-08-25",
            "published_at": "2026-08-25T17:00:00",
        }])
        source_observations = observations.copy()
        source_observations.loc[0, "package_tag"] = source_tag
        source_observations.loc[0, "item"] = "Fig Bar Bulk Flower"
        source_observations.loc[0, "source_packages"] = ""

        with patch(
            "qcc_reflex_pilot.data.query_frame",
            side_effect=[transfers, observations, source_observations],
        ):
            result = load_package_lineage(tag)

        self.assertTrue(result["found"])
        self.assertEqual(result["package_count"], 1)
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["snapshot_count"], 1)
        self.assertEqual(result["transfer_count"], 1)
        self.assertEqual(result["lineage"][1]["Relationship"], "Source Package")
        self.assertEqual(result["timeline"][0]["Status"], "Rejected")

    def test_transfer_only_result_explains_missing_snapshot_genealogy(self):
        tag = "1A4110300002A31000038761"
        transfers = pd.DataFrame([{
            "manifest": "0001409482",
            "package_tag": tag,
            "destination_facility": "Hackettstown Dispensary LLC",
            "created_at": "2026-08-26",
            "state": "Rejected",
            "actual_shipped": 32,
            "actual_shipped_uom": "ea",
            "item": "J1 Packaged 3.5g EA",
        }])
        with patch(
            "qcc_reflex_pilot.data.query_frame",
            side_effect=[transfers, pd.DataFrame()],
        ):
            result = load_package_lineage(tag)

        self.assertTrue(result["found"])
        self.assertEqual(result["snapshot_count"], 0)
        self.assertIn("never captured", result["message"])
        self.assertEqual(result["timeline"][0]["Manifest"], "0001409482")


if __name__ == "__main__":
    unittest.main()
