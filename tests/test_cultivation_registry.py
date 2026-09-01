import unittest

from qcc_reflex_pilot.cultivation_registry import (
    calculate_bench_metrics,
    calculate_lighting_total,
    calculate_room_metrics,
    default_cycle_program,
    default_room_rows,
    fresh_frozen_canopy,
    generate_schedule,
    schedule_conflicts,
)


class CultivationRegistryTests(unittest.TestCase):
    def test_room_and_bench_geometry(self):
        self.assertEqual(
            calculate_room_metrics(30, 40, 12),
            {"floor_area_sqft": 1200.0, "volume_cuft": 14400.0},
        )
        self.assertEqual(
            calculate_bench_metrics(40, 5, 0.75),
            {"canopy_sqft": 200.0, "target_plants": 150},
        )

    def test_lighting_override_is_authoritative(self):
        self.assertEqual(calculate_lighting_total(3, 200), 600.0)
        self.assertEqual(calculate_lighting_total(3, 200, 550), 550.0)

    def test_fresh_frozen_uses_plant_proportion_until_actual_canopy(self):
        planned = fresh_frozen_canopy(
            planted_canopy_sqft=200,
            planted_plants=150,
            fresh_frozen_plants=30,
        )
        self.assertEqual(planned["fresh_frozen_canopy_sqft"], 40.0)
        self.assertEqual(planned["net_dry_canopy_sqft"], 160.0)
        actual = fresh_frozen_canopy(
            planted_canopy_sqft=200,
            planted_plants=150,
            fresh_frozen_plants=30,
            actual_fresh_frozen_canopy_sqft=55,
        )
        self.assertEqual(actual["fresh_frozen_canopy_sqft"], 55.0)
        self.assertEqual(actual["net_dry_canopy_sqft"], 145.0)
        self.assertEqual(actual["fresh_frozen_source"], "Actual crop record")

    def test_default_rotation_generates_26_crops_and_rolls_cycle(self):
        rows = generate_schedule(
            program=default_cycle_program(),
            rooms=default_room_rows(),
            start_crop="F5.10",
            first_clone_cut="2026-08-28",
            count=26,
        )
        self.assertEqual(len(rows), 26)
        self.assertEqual(rows[0]["crop"], "F5.10")
        self.assertEqual(rows[1]["crop"], "F1.11")
        self.assertEqual(rows[1]["clone_cut_date"], "2026-09-11")
        self.assertEqual(rows[0]["status"], "Planning")

    def test_independent_room_program_is_supported(self):
        program = {
            **default_cycle_program(),
            "program_id": "room-six",
            "name": "Room 6 Independent",
            "cadence_days": 7,
            "room_rotation": ["Flower Room 6"],
        }
        rooms = [{"room_code": "F6", "name": "Flower Room 6", "active": True}]
        rows = generate_schedule(
            program=program,
            rooms=rooms,
            start_crop="F6.1",
            first_clone_cut="2026-09-01",
            count=3,
        )
        self.assertEqual([row["crop"] for row in rows], ["F6.1", "F6.2", "F6.3"])
        self.assertEqual(rows[1]["clone_cut_date"], "2026-09-08")
        self.assertTrue(schedule_conflicts(rows))


if __name__ == "__main__":
    unittest.main()

