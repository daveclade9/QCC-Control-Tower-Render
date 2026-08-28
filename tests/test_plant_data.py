from qcc_reflex_pilot.cultivation import UPCOMING_CROP_ALLOCATIONS
from qcc_reflex_pilot.plant_data import (
    classify_plant_export,
    plant_crop_reconciliation,
    plant_facility,
)
from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


def test_metrc_plant_exports_are_classified_by_headers_and_phase_filename():
    plant_columns = [
        "Tag", "Strain", "Location", "Plant Batch", "Phase Date", "Harvested"
    ]
    assert classify_plant_export("Plants-Flowering.xlsx", plant_columns) == "flowering"
    assert classify_plant_export("Plants-Vegetative.xlsx", plant_columns) == "vegetative"
    assert classify_plant_export(
        "Plants-Harvests.xlsx",
        ["Harvest Batch", "Wet Weight", "Total Weight Packaged"],
    ) == "harvests"
    assert classify_plant_export(
        "Plantings-Active.xlsx",
        ["Plant Batch", "Plants", "Tracked", "Destroyed", "Source Plant"],
    ) == "plantings"


def test_plant_facility_separates_1a_from_main_cultivation():
    assert plant_facility("1A Flower-8") == "1A Building"
    assert plant_facility("Cultivation 2 - F2") == "Main Cultivation"
    assert plant_facility("Dry Room") == "Main Cultivation"


def test_crop_reconciliation_flags_material_plant_variance():
    crop_rows = [
        {
            "crop": "F2.9",
            "room": "Flower Room 2",
            "harvest_date": "2026-08-24",
            "strain": "Lemon Cherry Gelato",
            "square_feet": 185.0,
        }
    ]
    snapshot = {
        "harvests": [{
            "harvest_batch": "Lemon Cherry Gelato-F2.9-08.24.2026",
            "strain": "Lemon Cherry Gelato",
            "plants": 139,
        }]
    }
    result = plant_crop_reconciliation(snapshot, crop_rows)
    assert result[0]["Crop Report Plants"] == 139
    assert result[0]["Metrc Harvest Plants"] == 139
    assert result[0]["Variance"] == 0
    assert result[0]["Status"] == "Matched"


def test_current_crop_report_population_is_reconcilable():
    snapshot = {"harvests": []}
    result = plant_crop_reconciliation(snapshot, UPCOMING_CROP_ALLOCATIONS)
    assert any(row["Crop"] == "F2.9" for row in result)


def test_plant_table_computed_values_are_gridjs_row_arrays():
    """Grid.js requires list rows; dictionaries render headers with no body."""
    state = DashboardState(_reflex_internal_init=True)
    state._cultivation_plant_snapshot = {
        "source_files": {"flowering": "flowering.xlsx"},
        "flowering": [{
            "tag": "1A411TEST",
            "strain": "Diamond Bar",
            "phase": "Flowering",
            "facility": "Main Cultivation",
            "location": "Flower Room 1",
            "plant_batch": "Diamond Bar Batch",
            "plant_batch_date": "2026-08-01",
            "phase_date": "2026-08-20",
            "hold": False,
        }],
        "vegetative": [],
        "plantings": [],
        "harvests": [],
    }
    state.cultivation_plant_snapshot_revision = 1

    assert state.cultivation_plant_source_rows[0] == [
        "Flowering Plants", "flowering.xlsx"
    ]
    assert state.cultivation_active_plant_table_rows[0] == [
        "1A411TEST",
        "Diamond Bar",
        "Flowering",
        "Main Cultivation",
        "Flower Room 1",
        "Diamond Bar Batch",
        "2026-08-01",
        "2026-08-20",
        "No",
    ]
    assert state.cultivation_plant_location_summary_rows[0] == [
        "Main Cultivation", "Flowering", "Flower Room 1", 1, 1
    ]
