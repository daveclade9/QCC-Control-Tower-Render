import unittest
from unittest.mock import patch

import pandas as pd

from qcc_reflex_pilot import data


class InventoryRefreshCacheTest(unittest.TestCase):
    def setUp(self):
        self.original_operational = dict(data._OPERATIONAL_CONTEXT)
        self.original_dashboard = dict(data._DASHBOARD_CACHE)

    def tearDown(self):
        data._OPERATIONAL_CONTEXT.clear()
        data._OPERATIONAL_CONTEXT.update(self.original_operational)
        data._DASHBOARD_CACHE.clear()
        data._DASHBOARD_CACHE.update(self.original_dashboard)

    @staticmethod
    def _context(marker: str) -> dict:
        return {
            "snapshot": {"snapshot_id": marker},
            "inventory_skus": pd.DataFrame(),
            "inventory_packages": pd.DataFrame(),
            "plans": pd.DataFrame(),
            "outputs": pd.DataFrame(),
            "sources": pd.DataFrame(),
            "production_templates": pd.DataFrame(),
        }

    def test_operational_force_refresh_bypasses_warm_inventory_cache(self):
        data._OPERATIONAL_CONTEXT.update({
            "loaded_at": data.time.monotonic(),
            "payload": self._context("stale"),
        })
        fresh = self._context("fresh")

        with (
            patch.object(data, "load_latest_inventory_bundle", return_value=(
                fresh["snapshot"],
                fresh["inventory_skus"],
                fresh["inventory_packages"],
            )) as inventory_loader,
            patch.object(data, "load_production_data", return_value=(
                fresh["plans"], fresh["outputs"], fresh["sources"],
            )),
            patch.object(
                data,
                "load_reflex_production_templates",
                return_value=fresh["production_templates"],
            ),
        ):
            cached = data.load_operational_context()
            refreshed = data.load_operational_context(force_refresh=True)

        self.assertEqual(cached["snapshot"]["snapshot_id"], "stale")
        self.assertEqual(refreshed["snapshot"]["snapshot_id"], "fresh")
        inventory_loader.assert_called_once_with()

    def test_dashboard_force_refresh_reaches_operational_inventory_loader(self):
        data._DASHBOARD_CACHE.update({
            "loaded_at": data.time.monotonic(),
            "payload": {"cached": True},
        })

        with patch.object(
            data,
            "build_dashboard_data",
            return_value={"cached": False},
        ) as builder:
            result = data.get_dashboard_data(force_refresh=True)

        self.assertEqual(result, {"cached": False})
        builder.assert_called_once_with(
            include_sales=False,
            force_operational_refresh=True,
        )


if __name__ == "__main__":
    unittest.main()
