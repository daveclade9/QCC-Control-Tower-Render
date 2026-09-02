import unittest

from qcc_reflex_pilot.cultivation_registry import (
    default_cycle_program,
    default_room_rows,
    default_schedule,
)
from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


class CultivationSchedulePreviewTests(unittest.TestCase):
    def test_preview_editor_populates_display_rows(self):
        state = DashboardState(_reflex_internal_init=True)
        state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": [],
            "schedule": default_schedule(),
            "historical_yields": [],
        }
        state.cultivation_schedule_program = "main-five-room"
        state.cultivation_schedule_start_crop = "F5.10"
        state.cultivation_schedule_first_cut = "2026-08-28"
        state.cultivation_schedule_count = 26

        state.preview_cultivation_schedule_editor()

        self.assertEqual(len(state.cultivation_schedule_preview), 26)
        self.assertEqual(len(state.cultivation_schedule_preview_rows), 26)
        self.assertEqual(state.cultivation_schedule_preview_rows[0]["Crop"], "F5.10")
        self.assertEqual(
            state.cultivation_schedule_preview_rows[0]["Room"], "Flower Room 5"
        )
        self.assertEqual(
            state.cultivation_schedule_preview_rows[0]["Clone Cut Date"],
            "2026-08-28",
        )
        self.assertEqual(state.cultivation_registry_error, "")

    def test_preview_form_accepts_less_than_default_horizon(self):
        state = DashboardState(_reflex_internal_init=True)
        state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": [],
            "schedule": default_schedule(),
            "historical_yields": [],
        }

        state.preview_cultivation_schedule(
            {
                "program_id": "main-five-room",
                "start_crop": "F5.10",
                "first_cut": "2026-08-28",
                "count": "5",
            }
        )

        self.assertEqual(state.cultivation_schedule_count, 5)
        self.assertEqual(len(state.cultivation_schedule_preview), 5)
        self.assertEqual(len(state.cultivation_schedule_preview_rows), 5)
        self.assertEqual(
            state.cultivation_registry_message,
            "Previewed 5 crops. Review them before saving.",
        )
        self.assertEqual(state.cultivation_registry_error, "")

    def test_saved_schedule_rows_are_available_after_registry_reload(self):
        state = DashboardState(_reflex_internal_init=True)
        schedule = default_schedule(26)
        state._cultivation_registry = {
            "programs": [default_cycle_program()],
            "rooms": default_room_rows(),
            "benches": [],
            "schedule": schedule,
            "historical_yields": [],
        }
        state.cultivation_registry_revision += 1

        rows = state.cultivation_schedule_rows

        self.assertEqual(len(rows), 26)
        self.assertEqual(rows[0]["Crop"], "F5.10")
        self.assertEqual(rows[0]["Schedule ID"], "main-five-room-F5-10")
        self.assertEqual(rows[-1]["Crop"], "F5.15")


if __name__ == "__main__":
    unittest.main()
