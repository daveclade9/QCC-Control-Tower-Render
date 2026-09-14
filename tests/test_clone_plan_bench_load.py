from qcc_reflex_pilot.cultivation import exact_bench_allocations
from qcc_reflex_pilot.cultivation_registry import (
    default_bench_rows,
    default_cycle_program,
    default_room_rows,
)
from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


def _state_with_approved_plan(bench_assignments=None):
    state = DashboardState(_reflex_internal_init=True)
    state._cultivation_registry = {
        "programs": [default_cycle_program()],
        "rooms": default_room_rows(),
        "benches": default_bench_rows(),
        "schedule": [],
        "historical_yields": [],
    }
    state.cultivation_clone_plan_history = [{
        "plan_id": "F1.11-approved",
        "crop": "F1.11",
        "flower_room": "Flower Room 1",
        "clone_cut_date": "2026-09-11",
        "status": "Approved",
        "demand_model": "Availability-Adjusted",
        "demand_product_scope": "Flower + Pre-Rolls",
        "allocations": {"Diamond Bar": 1.0, "Fig Bar": 0.5},
        "bench_assignments": bench_assignments or [],
    }]
    return state


def test_loading_approved_plan_prefills_room_metadata_and_benches():
    state = _state_with_approved_plan()

    state.load_approved_clone_plan_to_allocation("F1.11-approved")

    assert state.cultivation_cycle_name == "F1.11"
    assert state.cultivation_flower_room == "Flower Room 1"
    assert state.cultivation_flower_entry_date == "2026-10-21"
    assert exact_bench_allocations(state.cultivation_bench_plans) == {
        "Diamond Bar": 1.0,
        "Fig Bar": 0.5,
    }
    assert "proposed room map was filled" in state.cultivation_message


def test_loading_plan_keeps_a_previously_saved_exact_bench_map():
    # The persisted plan uses the Room Bench Map shape, not registry rows.
    state = _state_with_approved_plan()
    exact_map = state._registered_room_bench_plans("Flower Room 1")
    exact_map[0].update({"strain_1": "J1", "percent_1": 100.0})
    state.cultivation_clone_plan_history[0]["bench_assignments"] = exact_map

    state.load_approved_clone_plan_to_allocation("F1.11-approved")

    assert state.cultivation_bench_plans[0]["strain_1"] == "J1"
    assert "saved room bench map is loaded" in state.cultivation_message


def test_strain_specific_overage_changes_only_that_clone_recommendation():
    state = _state_with_approved_plan()
    room_map = state._registered_room_bench_plans("Flower Room 1")
    room_map[0].update({"strain_1": "Diamond Bar", "percent_1": 100.0})
    room_map[1].update({"strain_1": "Fig Bar", "percent_1": 100.0})
    state.cultivation_bench_plans = room_map

    baseline = {
        row["strain"]: row["recommended_clones"]
        for row in state.cultivation_strain_summary_rows
    }
    state.change_cultivation_strain_overage("Diamond Bar", "60")
    adjusted = {
        row["strain"]: row
        for row in state.cultivation_strain_summary_rows
    }

    assert adjusted["Diamond Bar"]["requested_overage_percent"] == 60
    assert adjusted["Diamond Bar"]["custom_overage"] is True
    assert adjusted["Diamond Bar"]["recommended_clones"] > baseline["Diamond Bar"]
    assert adjusted["Fig Bar"]["recommended_clones"] == baseline["Fig Bar"]


def test_loading_saved_room_map_restores_strain_specific_overage():
    state = _state_with_approved_plan()
    exact_map = state._registered_room_bench_plans("Flower Room 1")
    exact_map[0].update({
        "strain_1": "Diamond Bar",
        "percent_1": 100.0,
        "saved_strain_overage_percentages": {"diamond bar": 55},
    })
    state.cultivation_clone_plan_history[0]["bench_assignments"] = exact_map

    state.load_approved_clone_plan_to_allocation("F1.11-approved")

    assert state.cultivation_strain_overage_percentages == {"diamond bar": 55}
    diamond_bar = next(
        row
        for row in state.cultivation_strain_summary_rows
        if row["strain"] == "Diamond Bar"
    )
    assert diamond_bar["requested_overage_percent"] == 55
