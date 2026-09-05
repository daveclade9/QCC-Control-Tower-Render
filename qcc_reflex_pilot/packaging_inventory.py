"""Workbook-seeded packaging inventory foundation for Materials & Procurement."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


SEED_PATH = Path(__file__).with_name("packaging_inventory_seed.json")


@lru_cache(maxsize=1)
def packaging_seed() -> dict[str, Any]:
    """Load the normalized, non-sensitive workbook seed bundled with staging."""
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def packaging_items() -> list[dict[str, Any]]:
    return list(packaging_seed().get("items", []))


def packaging_planning_rows() -> list[dict[str, Any]]:
    return list(packaging_seed().get("planning", []))


def packaging_bom_recipes() -> list[dict[str, Any]]:
    return list(packaging_seed().get("bom_recipes", []))


def packaging_suppliers() -> list[dict[str, Any]]:
    return list(packaging_seed().get("suppliers", []))


def packaging_snapshot_rows() -> list[dict[str, Any]]:
    """Summarize each historical count without combining unlike unit measures."""
    items = packaging_items()
    dates = packaging_seed().get("snapshot_dates", [])
    rows: list[dict[str, Any]] = []
    for count_date in reversed(dates):
        observations = [
            point
            for item in items
            for point in item.get("history", [])
            if point.get("date") == count_date
        ]
        rows.append(
            {
                "count_date": count_date,
                "items_counted": len(observations),
                "nonzero_items": sum(float(point.get("quantity", 0) or 0) > 0 for point in observations),
                "zero_items": sum(float(point.get("quantity", 0) or 0) == 0 for point in observations),
                "source": "Packaging Inventory workbook",
            }
        )
    return rows


def coverage_status_color(status: str) -> str:
    return {
        "Critical": "red",
        "Reorder Soon": "yellow",
        "Covered": "green",
        "No Demand / Review": "gray",
    }.get(status, "gray")

