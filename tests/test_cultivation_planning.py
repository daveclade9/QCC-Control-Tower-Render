from qcc_reflex_pilot.cultivation import (
    UPCOMING_CROP_ALLOCATIONS,
    HISTORICAL_CLONE_ALLOCATIONS,
    approved_clone_plan_for_crop,
    bench_plant_capacity,
    crop_is_scheduled_supply,
    clone_plan_edit_window,
    clone_plan_is_editable,
    clone_planning_periods,
    prior_clone_planning_periods,
    cultivation_timeline,
    cultivation_flower_supply_bucket,
    default_split_percentages,
    exact_bench_allocations,
    estimated_yield_g_per_sqft,
    estimated_yield_pounds,
    inventory_counts_as_current_cultivation_supply,
    normalized_strain,
    projected_harvest_dates,
    projected_risk,
    forecast_two_week_balances,
    recommend_clone_trays,
    room_bench_plans,
    scheduled_supply_reconciliation,
    sku_fill_grams,
)

from datetime import date


def test_bench_capacity_uses_confirmed_density():
    assert bench_plant_capacity(128) == 96
    assert bench_plant_capacity(185) == 139


def test_bench_capacity_accepts_a_cultivation_selected_density():
    assert bench_plant_capacity(185, 0.65) == 120
    assert bench_plant_capacity(185, 0.80) == 148


def test_clone_recommendation_rounds_to_complete_32_clone_trays():
    result = recommend_clone_trays(100, 30)

    assert result["trays"] == 4
    assert result["recommended_clones"] == 128
    assert result["actual_overage_percent"] == 28.0


def test_clone_recommendation_clamps_safety_range():
    assert recommend_clone_trays(100, 20)["requested_overage_percent"] == 25
    assert recommend_clone_trays(100, 35)["requested_overage_percent"] == 30


def test_clone_planner_normalizes_known_sales_strain_aliases():
    assert normalized_strain("Private Reserve OG") == "private reserve"
    assert normalized_strain("Lip Smackerz") == "lipsmackerz"
    assert normalized_strain("Lip Smackers") == "lipsmackerz"


def test_south_central_purps_uses_temporary_conservative_yield_override():
    assert estimated_yield_g_per_sqft(
        "South Central Purps", "Flower Room 4"
    ) == 85.0
    assert estimated_yield_pounds(
        185, "South Central Purps", "Flower Room 4"
    ) == 34.7


def test_timeline_uses_21_rooting_days_and_19_veg_days():
    assert cultivation_timeline("2026-10-01") == {
        "clone_cut_date": "2026-08-22",
        "veg_transfer_date": "2026-09-12",
        "flower_entry_date": "2026-10-01",
    }


def test_confirmed_room_layouts_build_expected_benches():
    room_1 = room_bench_plans("Flower Room 1")
    room_5 = room_bench_plans("Flower Room 5")

    assert len(room_1) == 9
    assert sum(row["square_feet"] for row in room_1) == 1250.0
    assert room_1[2]["bench"] == "Bench 3A"
    assert room_1[2]["square_feet"] == 80.0
    assert len(room_5) == 6
    assert room_5[-1]["bench"] == "Bench 6"
    assert room_5[-1]["square_feet"] == 160.0


def test_room_layout_uses_selected_density_for_target_population():
    room_5 = room_bench_plans("Flower Room 5", 0.80)

    assert room_5[0]["target_plants"] == 148
    assert room_5[-1]["target_plants"] == 128


def test_default_bench_splits_always_total_one_hundred_percent():
    assert default_split_percentages(1) == (100.0, 0.0, 0.0)
    assert default_split_percentages(2) == (50.0, 50.0, 0.0)
    assert default_split_percentages(3) == (34.0, 33.0, 33.0)


def test_exact_room_map_replaces_planning_values_with_physical_benches():
    benches = room_bench_plans("Flower Room 5")
    benches[0].update({"strain_1": "Diamond Bar", "percent_1": 100.0})
    benches[1].update({
        "strain_count": 2,
        "strain_1": "Diamond Bar",
        "percent_1": 60.0,
        "strain_2": "J1",
        "percent_2": 40.0,
    })

    allocations = exact_bench_allocations(benches)

    assert allocations == {"Diamond Bar": 1.6, "J1": 0.4}


def test_yield_estimate_blends_strain_and_room_history():
    assert estimated_yield_g_per_sqft("Diamond Bar", "Flower Room 1") == 95.3
    assert estimated_yield_pounds(185, "Diamond Bar", "Flower Room 1") == 38.9


def test_new_strain_uses_room_history_until_its_own_yield_exists():
    assert estimated_yield_g_per_sqft(
        "Hood Candy", "Flower Room 1"
    ) == estimated_yield_g_per_sqft("Jelly Cake", "Flower Room 1")
    assert estimated_yield_pounds(185, "Hood Candy", "Flower Room 1") > 0


def test_gelato_cherry_lemon_aliases_to_hood_candy_not_lcg():
    assert normalized_strain("GCL") == "hood candy"
    assert normalized_strain("Gelato Cherry Lemon") == "hood candy"
    assert normalized_strain("Lemon Cherry Gelato") == "lemon cherry gelato"


def test_gcl_canopy_is_separate_from_lemon_cherry_gelato():
    expected = {
        "F2.9": {"lemon cherry gelato": 185.0, "hood candy": 82.5},
        "F4.9": {"lemon cherry gelato": 185.0, "hood candy": 60.0},
        "F1.10": {"lemon cherry gelato": 165.0, "hood candy": 80.0},
    }
    actual: dict[str, dict[str, float]] = {}
    for row in UPCOMING_CROP_ALLOCATIONS:
        crop = str(row["crop"])
        if crop not in expected:
            continue
        strain = normalized_strain(row["strain"])
        if strain not in {"lemon cherry gelato", "hood candy"}:
            continue
        actual.setdefault(crop, {})[strain] = (
            actual.setdefault(crop, {}).get(strain, 0.0)
            + float(row["square_feet"])
        )

    assert actual == expected


def test_projected_dates_include_flower_and_post_harvest_time():
    assert projected_harvest_dates("2026-10-01", 30) == {
        "harvest_date": "2026-12-08",
        "available_date": "2027-01-07",
    }


def test_harvested_crop_remains_scheduled_until_processing_finishes():
    assert crop_is_scheduled_supply(
        date(2026, 8, 10), date(2026, 8, 23), date(2026, 9, 30), 30
    )
    assert crop_is_scheduled_supply(
        date(2026, 8, 10), date(2026, 9, 10), date(2026, 9, 30), 30
    )
    assert not crop_is_scheduled_supply(
        date(2026, 8, 10), date(2026, 9, 25), date(2026, 9, 30), 30
    )


def test_fresh_frozen_plants_reduce_scheduled_by_exact_crop_population():
    result = scheduled_supply_reconciliation(
        36.0, 139, 46, 0.0,
        date(2026, 11, 6), date(2026, 10, 20),
    )

    assert result["fresh_frozen_percent"] == 33.1
    assert result["fresh_frozen_reduction_lbs"] == 11.9
    assert result["net_projected_lbs"] == 24.1
    assert result["forecast_counted_lbs"] == 24.1


def test_actual_fresh_frozen_plants_override_manual_plan_removal():
    with_manual = scheduled_supply_reconciliation(
        36.0, 139, 20, 0.0,
        date(2026, 11, 6), date(2026, 10, 20),
        actual_fresh_frozen_plants=46,
    )
    after_manual_removal = scheduled_supply_reconciliation(
        36.0, 139, 0, 0.0,
        date(2026, 11, 6), date(2026, 10, 20),
        actual_fresh_frozen_plants=46,
    )

    assert with_manual["planned_fresh_frozen_plants"] == 20
    assert after_manual_removal["planned_fresh_frozen_plants"] == 0
    assert with_manual["actual_fresh_frozen_plants"] == 46
    assert with_manual["fresh_frozen_source"] == "Actual Metrc harvest"
    assert with_manual["fresh_frozen_reduction_lbs"] == 11.9
    assert after_manual_removal["fresh_frozen_reduction_lbs"] == 11.9
    assert after_manual_removal["net_projected_lbs"] == 24.1


def test_creative_use_reduces_post_fresh_frozen_scheduled_pounds():
    result = scheduled_supply_reconciliation(
        36.0, 139, 46, 0.0,
        date(2026, 11, 6), date(2026, 10, 20),
        creative_use_reduction_lbs=8.0,
    )

    assert result["fresh_frozen_reduction_lbs"] == 11.9
    assert result["creative_use_reduction_lbs"] == 8.0
    assert result["net_projected_lbs"] == 16.1
    assert result["forecast_counted_lbs"] == 16.1


def test_actual_processing_hides_unconfirmed_projection_remainder():
    result = scheduled_supply_reconciliation(
        36.0, 139, 0, 22.0,
        date(2026, 8, 10), date(2026, 9, 10),
    )

    assert result["unconfirmed_remainder_lbs"] == 14.0
    assert result["forecast_counted_lbs"] == 0.0
    assert result["status"] == "Actual detected — remainder unconfirmed"


def test_scheduled_supply_expires_45_days_after_harvest():
    result = scheduled_supply_reconciliation(
        36.0, 139, 0, 0.0,
        date(2026, 8, 10), date(2026, 9, 25),
    )

    assert result["expired"]
    assert result["forecast_counted_lbs"] == 0.0


def test_historical_clone_workbook_allocations_cover_last_eight_crops():
    assert list(HISTORICAL_CLONE_ALLOCATIONS) == [
        "F2.9", "F3.9", "F4.9", "F5.9",
        "F1.10", "F2.10", "F3.10", "F4.10",
    ]
    assert HISTORICAL_CLONE_ALLOCATIONS["F2.10"]["Diamond Bar"] == 1.0
    assert HISTORICAL_CLONE_ALLOCATIONS["F4.10"]["Jelly Cake"] == 0.5


def test_f19_uses_confirmed_crop_report_allocations():
    f19 = [row for row in UPCOMING_CROP_ALLOCATIONS if row["crop"] == "F1.9"]
    assert sum(row["square_feet"] for row in f19) == 1245.0
    diamond_bar = [row for row in f19 if row["strain"] == "Diamond Bar"]
    assert diamond_bar == [{
        "crop": "F1.9", "room": "Flower Room 1",
        "harvest_date": "2026-08-10", "strain": "Diamond Bar",
        "square_feet": 185.0,
    }]


def test_trim_and_retention_do_not_count_as_current_cultivation_supply():
    assert not inventory_counts_as_current_cultivation_supply(
        {"Production Stage": "Trim", "Location": "Secure Storage"}
    )
    assert not inventory_counts_as_current_cultivation_supply(
        {"Production Stage": "Packaged Goods", "Location": "Retention/Stability Storage"}
    )
    assert not inventory_counts_as_current_cultivation_supply(
        {"Production Stage": "WIP-Cultivation", "Location": "WIP Quarantine Room 1"}
    )


def test_current_flower_supply_uses_the_agreed_net_flower_rule():
    tested_flower = {
        "Production Stage": "Packaged Goods", "License": "Manufacturing",
        "Category": "Bud/Flower - Packaged", "QA Status": "Test Passed",
        "SKU Type": "3.5g Flower", "Item": "Diamond Bar Packaged 3.5g EA",
        "Location": "Vault 1 Cultivation - Approved For Sale",
    }
    pre_wip_flower = {
        "Production Stage": "Pre-WIP-Cultivation", "License": "Cultivation",
        "Category": "Bud/Flower - Bulk", "QA Status": "Not Submitted",
        "SKU Type": "Not Packaged SKU", "Item": "Diamond Bar Bulk",
        "Location": "Vault - Pending Testing",
    }
    passed_quarantine = {
        "Production Stage": "WIP-Cultivation", "License": "Cultivation",
        "Category": "Bud/Flower - Bulk", "QA Status": "Test Passed",
        "SKU Type": "Not Packaged SKU", "Item": "Diamond Bar Bulk",
        "Location": "WIP Quarantine Room 1",
    }
    assert cultivation_flower_supply_bucket(tested_flower) == "CPG"
    assert cultivation_flower_supply_bucket(pre_wip_flower) == ""
    assert cultivation_flower_supply_bucket(
        pre_wip_flower, include_pre_wip=True
    ) == "Pre-WIP-Cultivation"
    assert cultivation_flower_supply_bucket(passed_quarantine) == "WIP-Cultivation"
    for excluded in (
        {**passed_quarantine, "QA Status": "Not Submitted"},
        {**tested_flower, "Category": "Shake/Trim (by strain)", "Item": "Diamond Bar Trim"},
        {**tested_flower, "Category": "Concentrate (weight)", "Item": "Diamond Bar Bulk Concentrate"},
        {**tested_flower, "Category": "Vape Carts", "Item": "Diamond Bar Vape - DC 1g"},
        {**tested_flower, "Location": "Vault - Retention/Stability Storage"},
    ):
        assert cultivation_flower_supply_bucket(excluded) == ""


def test_manufacturing_bulk_and_fresh_frozen_do_not_count_as_current_flower():
    cultivation_bulk = {
        "Production Stage": "WIP-Cultivation",
        "License": "Cultivation",
        "Category": "Bud/Flower - Bulk",
        "Item": "Diamond Bar Bulk Flower",
        "QA Status": "Test Passed",
        "Location": "Cultivation - Approved For Sale",
    }
    manufacturing_bulk = {
        **cultivation_bulk,
        "License": "Manufacturing",
        "Location": "Manufacturing - Approved For Sale",
    }
    fresh_frozen = {
        **manufacturing_bulk,
        "Production Stage": "WIP-Manufacturing",
        "Item": "Diamond Bar Fresh Frozen",
    }

    assert cultivation_flower_supply_bucket(cultivation_bulk) == "WIP-Cultivation"
    assert cultivation_flower_supply_bucket(manufacturing_bulk) == ""
    assert cultivation_flower_supply_bucket(fresh_frozen) == ""
    assert cultivation_flower_supply_bucket(
        {
            **cultivation_bulk,
            "Production Stage": "Pre-WIP-Cultivation",
            "Item": "Diamond Bar Fresh Frozen Bulk",
        },
        include_pre_wip=True,
    ) == ""


def test_clone_supply_excludes_purchased_1a_wip_and_its_pre_wip():
    common = {
        "Category": "Bud/Flower - Bulk",
        "License": "Cultivation",
        "Ownership Status": "QCC-Owned / Purchased from Building 1A",
    }
    purchased_wip = {
        **common,
        "Production Stage": "WIP-Purchased 1A",
        "QA Status": "Test Passed",
    }
    purchased_pre_wip = {
        **common,
        "Production Stage": "Pre-WIP-Purchased 1A",
        "QA Status": "Not Submitted",
    }

    assert cultivation_flower_supply_bucket(purchased_wip) == ""
    assert cultivation_flower_supply_bucket(
        purchased_pre_wip, include_pre_wip=True
    ) == ""


def test_clone_planning_schedule_rolls_rooms_and_crop_numbers_every_two_weeks():
    periods = clone_planning_periods(6)

    assert [row["crop"] for row in periods] == [
        "F5.10", "F1.11", "F2.11", "F3.11", "F4.11", "F5.11"
    ]
    assert periods[0]["clone_cut_date"] == "2026-08-28"
    assert periods[1]["clone_cut_date"] == "2026-09-11"
    assert periods[0]["flower_entry_date"] == "2026-10-07"


def test_historical_clone_planning_schedule_walks_back_from_f510():
    periods = prior_clone_planning_periods(8)

    assert [row["crop"] for row in periods[:4]] == [
        "F4.10", "F3.10", "F2.10", "F1.10"
    ]
    assert periods[0]["clone_cut_date"] == "2026-08-14"
    assert periods[4]["crop"] == "F5.9"


def test_clone_plan_edit_window_locks_after_seven_days_without_override():
    start, end = clone_plan_edit_window("2026-08-28")
    assert start.isoformat() == "2026-08-28"
    assert end.isoformat() == "2026-09-03"
    assert clone_plan_is_editable("2026-08-28", date(2026, 9, 3))
    assert not clone_plan_is_editable("2026-08-28", date(2026, 9, 4))
    assert clone_plan_is_editable("2026-08-28", date(2026, 9, 4), override=True)


def test_two_week_forecast_floors_physical_inventory_at_zero():
    assert forecast_two_week_balances(20, 15, [0, 35, 0]) == [0.0, 5.0, 0.0]


def test_approved_current_plan_is_selected_for_planner_restoration():
    plans = [
        {"crop": "F5.10", "status": "Approved", "allocations": {"J1": 1.0}},
        {"crop": "F4.10", "status": "Approved", "allocations": {"G13": 1.0}},
    ]

    selected = approved_clone_plan_for_crop(plans, "f5.10")

    assert selected is not None
    assert selected["allocations"] == {"J1": 1.0}


def test_sku_weights_and_future_risk_bands():
    assert sku_fill_grams("Clade9 3.5g Flower") == 3.5
    assert sku_fill_grams("5pk 1g Pre-Roll") == 5.0
    assert projected_risk(0) == "Balanced"
    assert projected_risk(4) == "Balanced"
    assert projected_risk(4.1) == "Warning"
    assert projected_risk(8) == "Warning"
    assert projected_risk(8.1) == "Excess"
    assert projected_risk(None) == "Review"
