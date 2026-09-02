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


if __name__ == "__main__":
    unittest.main()
