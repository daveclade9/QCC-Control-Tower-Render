"""Regression tests for the bundled cultivation yield history."""

from qcc_reflex_pilot.historical_yield import (
    HISTORICAL_CYCLE_COLUMNS,
    HISTORICAL_HARVEST_COLUMNS,
    HISTORICAL_ROOM_COLUMNS,
    HISTORICAL_STRAIN_OBSERVATIONS,
    HISTORICAL_STRAIN_COLUMNS,
    historical_cycle_table_data,
    historical_cycle_rows,
    historical_harvest_table_data,
    historical_harvest_rows,
    historical_kpis,
    historical_room_table_data,
    historical_room_rows,
    historical_strain_rows,
    historical_strain_table_data,
)


def test_historical_dataset_coverage() -> None:
    assert len(historical_harvest_rows()) == 51
    assert len(historical_room_rows()) == 5
    assert len(historical_cycle_rows()) == 11
    assert len(historical_strain_rows()) == 18
    assert HISTORICAL_STRAIN_OBSERVATIONS == 323


def test_all_room_kpis_match_the_source_workbook() -> None:
    assert historical_kpis() == {
        "harvests": "51",
        "total_finished": "11,752.9 lb",
        "average_finished": "230.4 lb",
        "weighted_yield": "87.3 g/sqft",
        "average_conversion": "12.0%",
    }


def test_room_filter_changes_harvests_and_kpis() -> None:
    room_rows = historical_harvest_rows("Flower Room 4")
    assert len(room_rows) == 9
    assert {row["Room"] for row in room_rows} == {"Flower Room 4"}
    assert historical_kpis("Flower Room 4")["total_finished"] == "2,238.5 lb"


def test_harvest_rows_are_latest_first() -> None:
    rows = historical_harvest_rows()
    assert rows[0]["Crop"] == "F3.8"
    assert rows[-1]["Crop"] == "F1.1A"


def test_consolidated_harvest_table_marks_fresh_frozen() -> None:
    rows = historical_harvest_table_data()
    fresh_frozen_index = HISTORICAL_HARVEST_COLUMNS.index("Fresh Frozen")
    harvest_date_index = HISTORICAL_HARVEST_COLUMNS.index("Harvest Date")
    crop_index = HISTORICAL_HARVEST_COLUMNS.index("Crop")
    by_crop = {row[crop_index]: row for row in rows}
    assert by_crop["F3.8"][fresh_frozen_index] == "Yes"
    assert by_crop["F3.6"][fresh_frozen_index] == "No"
    assert by_crop["F3.8"][harvest_date_index] == "2026-06-29"
    assert by_crop["F1.1A"][harvest_date_index] == "2024-04-08"


def test_combined_room_table_contains_class_and_lighting_metrics() -> None:
    rows = historical_room_table_data("Flower Room 2")
    assert len(rows) == 1
    row = dict(zip(HISTORICAL_ROOM_COLUMNS, rows[0]))
    assert row["AB %"] == 83.1
    assert row["C %"] == 17.0
    assert row["Upgraded Lighting Harvests"] == 2
    assert row["Upgraded Lighting Yield (g/sqft)"] == 91.64


def test_combined_cycle_table_uses_workbook_class_pounds() -> None:
    rows = historical_cycle_table_data()
    cycle_two = dict(zip(
        HISTORICAL_CYCLE_COLUMNS,
        next(row for row in rows if row[0] == "Cycle 2"),
    ))
    assert cycle_two["Total AB Yield (Lbs)"] == 1064.44
    assert cycle_two["Total C Yield (Lbs)"] == 225.32
    assert cycle_two["Rooms Harvested"] == 5
    assert "F5.2" in cycle_two["Notes regarding Lighting Upgrades"]


def test_strain_benchmark_table_calculates_flower_percentages() -> None:
    rows = historical_strain_table_data("G13")
    assert len(rows) == 1
    row = dict(zip(HISTORICAL_STRAIN_COLUMNS, rows[0]))
    assert row["Harvests"] == 14
    assert row["AB Flower %"] == 84.5
    assert row["C Flower %"] == 15.6


def test_strain_benchmark_filter_limits_the_table() -> None:
    rows = historical_strain_table_data("Diamond Bar")
    assert len(rows) == 1
    assert rows[0][0] == "Diamond Bar"
