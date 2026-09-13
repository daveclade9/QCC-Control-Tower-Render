from datetime import datetime, timezone

from qcc_reflex_pilot.qcc_reflex_pilot import format_snapshot_upload_eastern


def test_snapshot_upload_timestamp_converts_to_eastern_daylight_time() -> None:
    assert (
        format_snapshot_upload_eastern("2026-09-13T01:30:00+00:00")
        == "Sep 12, 2026 · 9:30 PM ET"
    )


def test_snapshot_upload_timestamp_converts_to_eastern_standard_time() -> None:
    assert (
        format_snapshot_upload_eastern(
            datetime(2026, 1, 15, 17, 5, tzinfo=timezone.utc)
        )
        == "Jan 15, 2026 · 12:05 PM ET"
    )


def test_snapshot_upload_timestamp_handles_missing_or_invalid_values() -> None:
    assert format_snapshot_upload_eastern("") == "—"
    assert format_snapshot_upload_eastern("Demo") == "—"
    assert format_snapshot_upload_eastern("not-a-date") == "—"
