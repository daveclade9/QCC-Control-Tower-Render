from __future__ import annotations

import pandas as pd

from qcc_reflex_pilot.availability_demand import (
    build_availability_demand_analysis,
)


def test_builds_all_strain_series_without_changing_velocity() -> None:
    demand = pd.DataFrame([
        {"created_at": "2026-01-05", "brand": "Clade9", "strain": "J1", "sku_type": "3.5g Flower", "shipped_units": 100},
        {"created_at": "2026-01-19", "brand": "Clade9", "strain": "J1", "sku_type": "3.5g Flower", "shipped_units": 120},
        {"created_at": "2026-01-12", "brand": "Clade9", "strain": "LA Piff", "sku_type": "7g Flower", "shipped_units": 40},
        {"created_at": "2026-01-19", "brand": "Clade9", "strain": "LA Piff", "sku_type": "7g Flower", "shipped_units": 60},
        {"created_at": "2026-01-19", "brand": "Clade9", "strain": "J1", "sku_type": "1g Pre-Roll", "shipped_units": 999},
    ])
    velocity = pd.DataFrame([
        {"Brand": "Clade9", "Strain": "J1", "SKU Type": "3.5g Flower", "Avg Weekly Units": 73.3},
        {"Brand": "Clade9", "Strain": "LA Piff", "SKU Type": "7g Flower", "Avg Weekly Units": 50.0},
        {"Brand": "Clade9", "Strain": "J1", "SKU Type": "1g Pre-Roll", "Avg Weekly Units": 999.0},
    ])

    original = velocity.copy(deep=True)
    result = build_availability_demand_analysis(demand, velocity)

    assert len(result["summary"]) == 3
    j1 = next(
        row for row in result["summary"]
        if row["Strain"] == "J1" and row["SKU Type"] == "3.5g Flower"
    )
    assert j1["Shipping Weeks"] == 2
    assert j1["Likely Constrained Weeks"] == 1
    assert j1["Experimental Adjusted Velocity"] == 110.0
    j1_weekly = [
        row for row in result["weekly"]
        if row["Strain"] == "J1" and row["SKU Type"] == "3.5g Flower"
    ]
    assert [row["Availability Signal"] for row in j1_weekly] == [
        "Shipping", "Likely OOS proxy", "Shipping"
    ]
    pd.testing.assert_frame_equal(velocity, original)


def test_preroll_history_is_included() -> None:
    demand = pd.DataFrame([
        {"created_at": "2026-01-05", "brand": "Clade9", "strain": "J1", "sku_type": "1g Pre-Roll", "shipped_units": 100},
    ])
    result = build_availability_demand_analysis(demand, pd.DataFrame())
    assert len(result["summary"]) == 1
    assert result["summary"][0]["SKU Type"] == "1g Pre-Roll"
