from __future__ import annotations

import pandas as pd

from qcc_reflex_pilot.availability_demand import (
    build_availability_demand_analysis,
)
from qcc_reflex_pilot.data import apply_availability_adjusted_velocity


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
    assert j1["Calendar Weeks"] == 2.14
    assert j1["Availability Weeks"] == 1.14
    assert j1["Experimental Adjusted Velocity"] == 192.5
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


def test_recent_trailing_gap_remains_in_adjusted_denominator() -> None:
    demand = pd.DataFrame([
        {"created_at": "2026-01-05", "brand": "Clade9", "strain": "J1", "sku_type": "3.5g Flower", "shipped_units": 100},
        {"created_at": "2026-01-19", "brand": "Clade9", "strain": "J1", "sku_type": "3.5g Flower", "shipped_units": 120},
        {"created_at": "2026-02-02", "brand": "Clade9", "strain": "Diamond Bar", "sku_type": "3.5g Flower", "shipped_units": 10},
    ])
    velocity = pd.DataFrame([
        {"Brand": "Clade9", "Strain": "J1", "SKU Type": "3.5g Flower", "Avg Weekly Units": 53.1},
    ])

    result = build_availability_demand_analysis(demand, velocity)
    j1 = next(row for row in result["summary"] if row["Strain"] == "J1")

    assert j1["Likely Constrained Weeks"] == 1
    assert j1["Recent Gap Weeks"] == 1
    assert j1["Calendar Weeks"] == 4.14
    assert j1["Availability Weeks"] == 3.14
    assert j1["Experimental Adjusted Velocity"] == 70.0


def test_selected_window_recalculates_velocity_supply_and_status() -> None:
    velocity = pd.DataFrame([
        {
            "Brand": "Clade9", "Strain": "Diamond Bar",
            "SKU Type": "3.5g Flower", "Units Shipped": 600,
            "Avg Weekly Units": 70.0,
            "Avg Weekly Units - Last 30 Days": 75.0,
            "Packages": 2, "Current Units": 280,
            "Weeks of Supply": 4.0,
            "Potential Matching WIP": "0.0 g",
            "Potential WIP Summary": "", "Committed WIP": "0.0 g",
            "Matching Pre-WIP Weight": "0.0 g", "Customers": 10,
            "Demand Status": "Replenishment Watch", "Last Shipped": "2026-02-02",
            "Days Since Last Shipment": 0, "Lifecycle Status": "Active",
            "Lifecycle Reason": "Current inventory exists",
        },
        {
            "Brand": "Clade9", "Strain": "Diamond Bar",
            "SKU Type": "Vape", "Units Shipped": 100,
            "Avg Weekly Units": 20.0,
            "Avg Weekly Units - Last 30 Days": 20.0,
            "Packages": 1, "Current Units": 100,
            "Weeks of Supply": 5.0,
            "Potential Matching WIP": "0.0 g",
            "Potential WIP Summary": "", "Committed WIP": "0.0 g",
            "Matching Pre-WIP Weight": "0.0 g", "Customers": 4,
            "Demand Status": "Replenishment Watch", "Last Shipped": "2026-02-02",
            "Days Since Last Shipment": 0, "Lifecycle Status": "Active",
            "Lifecycle Reason": "Current inventory exists",
        },
    ])
    summary = [{
        "Brand": "Clade9", "Strain": "Diamond Bar",
        "SKU Type": "3.5g Flower",
        "Experimental Adjusted Velocity": 140.0,
        "Likely Constrained Weeks": 4,
        "Recent Gap Weeks": 0,
        "Availability Weeks": 4.0,
    }]

    adjusted = apply_availability_adjusted_velocity(velocity, summary)
    flower = adjusted.loc[adjusted["SKU Type"].eq("3.5g Flower")].iloc[0]
    vape = adjusted.loc[adjusted["SKU Type"].eq("Vape")].iloc[0]

    assert flower["Avg Weekly Units"] == 140.0
    assert flower["Weeks of Supply"] == 2.0
    assert flower["Likely OOS Weeks"] == 4
    assert flower["Demand Status"] == "Stockout Risk Within 4 Weeks"
    assert flower["Velocity Model"] == "Availability-Adjusted"
    assert vape["Avg Weekly Units"] == 20.0
    assert vape["Weeks of Supply"] == 5.0
    assert vape["Velocity Model"] == "Current SKU Velocity — no adjusted evidence"
