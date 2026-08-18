import unittest

import pandas as pd

from qcc_reflex_pilot import data


class ProductionCacheDeletionTest(unittest.TestCase):
    def setUp(self):
        self.original_operational = dict(data._OPERATIONAL_CONTEXT)
        self.original_dashboard = dict(data._DASHBOARD_CACHE)
        self.original_sales = dict(data._SALES_DASHBOARD_CACHE)

    def tearDown(self):
        data._OPERATIONAL_CONTEXT.clear()
        data._OPERATIONAL_CONTEXT.update(self.original_operational)
        data._DASHBOARD_CACHE.clear()
        data._DASHBOARD_CACHE.update(self.original_dashboard)
        data._SALES_DASHBOARD_CACHE.clear()
        data._SALES_DASHBOARD_CACHE.update(self.original_sales)

    def test_delete_keeps_inventory_warm_and_removes_only_selected_plan(self):
        inventory = pd.DataFrame([{"package_tag": "1A", "quantity": 10}])
        data._OPERATIONAL_CONTEXT.update({
            "loaded_at": 1.0,
            "payload": {
                "inventory_packages": inventory,
                "plans": pd.DataFrame([
                    {"plan_id": "PLAN-1"}, {"plan_id": "PLAN-2"}
                ]),
                "outputs": pd.DataFrame([
                    {"plan_id": "PLAN-1"}, {"plan_id": "PLAN-2"}
                ]),
                "sources": pd.DataFrame([
                    {"plan_id": "PLAN-1"}, {"plan_id": "PLAN-2"}
                ]),
            },
        })
        data._DASHBOARD_CACHE.update({"loaded_at": 1.0, "payload": {"x": 1}})
        data._SALES_DASHBOARD_CACHE.update({"loaded_at": 1.0, "payload": {"x": 1}})

        data._remove_deleted_plans_from_caches(["PLAN-1"])

        payload = data._OPERATIONAL_CONTEXT["payload"]
        self.assertIs(payload["inventory_packages"], inventory)
        for key in ("plans", "outputs", "sources"):
            self.assertEqual(payload[key]["plan_id"].tolist(), ["PLAN-2"])
        self.assertIsNone(data._DASHBOARD_CACHE["payload"])
        self.assertIsNone(data._SALES_DASHBOARD_CACHE["payload"])


if __name__ == "__main__":
    unittest.main()
