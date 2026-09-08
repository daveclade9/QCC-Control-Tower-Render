"""Regression tests for the bundled cultivation yield history."""

import unittest
from unittest.mock import patch

from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState
from qcc_reflex_pilot.cultivation_registry import default_room_rows

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


def test_historical_yield_editor_label_hides_technical_record_id() -> None:
    record = {
        "harvest_id": "QCC-HY-F5-10-DIAMOND-BAR",
        "crop": "F5.10",
        "room": "Flower Room 5",
        "strain": "Diamond Bar",
        "harvest_date": "2026-10-05",
    }

    label = DashboardState._historical_yield_label(record)

    assert label == "F5.10 · Flower Room 5 · Diamond Bar · 2026-10-05"
    assert record["harvest_id"] not in label


def test_extended_harvest_data_exposes_ab_c_and_unclassified_flower() -> None:
    records = [{
        "crop": "F5.10", "room": "Flower Room 5", "strain": "Diamond Bar",
        "harvest_date": "2026-10-05", "planted_canopy_sqft": 200,
        "planted_plants": 150, "actual_ff_plants": 0,
        "dry_flower_lbs": 40, "ab_flower_lbs": 30,
        "c_flower_lbs": 8, "trim_lbs": 9,
    }]

    rows = DashboardState._historical_yield_extended_rows(
        records, "Flower Room 5"
    )

    assert rows[0]["AB Flower (lb)"] == 30
    assert rows[0]["C Flower (lb)"] == 8
    assert rows[0]["Unclassified Dry (lb)"] == 2


def test_room_strain_performance_aggregates_only_matching_room_and_strain() -> None:
    records = [
        {
            "room": "Flower Room 5", "strain": "Diamond Bar",
            "harvest_date": "2026-10-05", "planted_canopy_sqft": 200,
            "dry_flower_lbs": 40, "ab_flower_lbs": 30,
            "c_flower_lbs": 8, "trim_lbs": 9, "quality_score": 8,
        },
        {
            "room": "Flower Room 5", "strain": "Diamond Bar",
            "harvest_date": "2027-01-01", "planted_canopy_sqft": 200,
            "dry_flower_lbs": 42, "ab_flower_lbs": 31,
            "c_flower_lbs": 9, "trim_lbs": 10, "quality_score": 9,
        },
        {
            "room": "Flower Room 4", "strain": "Diamond Bar",
            "harvest_date": "2026-09-01", "planted_canopy_sqft": 200,
            "dry_flower_lbs": 50,
        },
        {
            "room": "Flower Room 5", "strain": "",
            "record_scope": "Room Total", "harvest_date": "2027-01-01",
            "planted_canopy_sqft": 1400, "dry_flower_lbs": 250,
        },
    ]

    rows = DashboardState._room_strain_performance_rows(
        records, "Flower Room 5", "Diamond Bar"
    )

    assert len(rows) == 1
    assert rows[0]["Harvests"] == 2
    assert rows[0]["Dry Flower (lb)"] == 82
    assert rows[0]["Avg Quality"] == 8.5
    assert rows[0]["Latest Harvest"] == "2027-01-01"


def test_crop_name_selects_its_flower_room_and_manual_room_selection_persists() -> None:
    state = DashboardState(_reflex_internal_init=True)
    state._cultivation_registry = {
        "programs": [], "rooms": default_room_rows(), "benches": [],
        "schedule": [], "historical_yields": [],
    }
    state.cultivation_registry_loaded = True

    state.change_cultivation_yield_crop("F4.8")
    assert state.cultivation_yield_room == "Flower Room 4"

    state.change_cultivation_yield_room("Flower Room 3")
    assert state.cultivation_yield_room == "Flower Room 3"
    assert "Room mismatch" in state.cultivation_yield_entry_warning


def test_historical_yield_save_reports_success_and_surfaces_saved_record() -> None:
    state = DashboardState(_reflex_internal_init=True)
    state.cultivation_yield_crop = "F4.8"
    state.cultivation_yield_room = "Flower Room 4"
    state.cultivation_yield_harvest_date = "2026-08-01"
    saved = {
        "harvest_id": "QCC-HY-TEST", "crop": "F4.8",
        "room": "Flower Room 4", "strain": "Diamond Bar",
        "record_scope": "Strain Detail", "harvest_date": "2026-08-01",
        "dry_flower_lbs": 40, "updated_by": "Tester",
    }
    payload = {
        "programs": [], "rooms": default_room_rows(), "benches": [],
        "schedule": [], "historical_yields": [saved],
        "voided_historical_yields": [], "historical_yield_revisions": [],
    }

    with patch(
        "qcc_reflex_pilot.qcc_reflex_pilot.save_historical_yield",
        return_value="QCC-HY-TEST",
    ), patch(
        "qcc_reflex_pilot.qcc_reflex_pilot.load_registry",
        return_value=payload,
    ):
        list(state.save_historical_yield_editor())

    assert state.cultivation_yield_error == ""
    assert "Saved F4.8 historical yield" in state.cultivation_yield_message
    assert state.cultivation_historical_manage_rows[0]["record_id"] == "QCC-HY-TEST"


def test_historical_yield_save_displays_database_error() -> None:
    state = DashboardState(_reflex_internal_init=True)
    with patch(
        "qcc_reflex_pilot.qcc_reflex_pilot.save_historical_yield",
        side_effect=ValueError("Matching record already exists."),
    ):
        list(state.save_historical_yield_editor())

    assert state.cultivation_yield_message == ""
    assert state.cultivation_yield_error == (
        "Historical yield was not saved: Matching record already exists."
    )


class HistoricalYieldRegistryTests(unittest.TestCase):
    def test_editor_label_hides_technical_record_id(self):
        test_historical_yield_editor_label_hides_technical_record_id()

    def test_extended_data_exposes_flower_classes(self):
        test_extended_harvest_data_exposes_ab_c_and_unclassified_flower()

    def test_room_strain_performance_aggregation(self):
        test_room_strain_performance_aggregates_only_matching_room_and_strain()

    def test_crop_room_link_and_manual_selection(self):
        test_crop_name_selects_its_flower_room_and_manual_room_selection_persists()

    def test_save_feedback_and_visible_record(self):
        test_historical_yield_save_reports_success_and_surfaces_saved_record()

    def test_save_error_feedback(self):
        test_historical_yield_save_displays_database_error()


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
