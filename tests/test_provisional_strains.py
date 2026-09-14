from unittest.mock import patch

from qcc_reflex_pilot.cultivation_registry import (
    DEFAULT_PROVISIONAL_STRAINS,
    cultivation_tenant_id,
    load_registry,
)
from qcc_reflex_pilot.qcc_reflex_pilot import DashboardState


def test_registry_fallback_includes_seeded_provisional_strains() -> None:
    with patch("qcc_reflex_pilot.cultivation_registry.psycopg", None):
        registry = load_registry()

    assert [
        row["strain_name"] for row in registry["provisional_strains"]
    ] == list(DEFAULT_PROVISIONAL_STRAINS)


def test_provisional_strain_records_use_configurable_tenant_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("QCC_TENANT_ID", "operator-2")
    assert cultivation_tenant_id() == "operator-2"


def test_non_admin_cannot_add_durable_provisional_strain() -> None:
    state = DashboardState(_reflex_internal_init=True)
    state.auth_role = "Cultivation"
    state.cultivation_new_strain_name = "New Variety"

    with (
        patch.object(DashboardState, "_require_active_session", return_value=True),
        patch(
            "qcc_reflex_pilot.qcc_reflex_pilot.save_provisional_strain"
        ) as save,
    ):
        state.add_cultivation_provisional_strain()

    save.assert_not_called()
    assert "Administrator access is required" in (
        state.cultivation_new_strain_error
    )


def test_admin_adds_and_reloads_durable_provisional_strain() -> None:
    state = DashboardState(_reflex_internal_init=True)
    state.auth_role = "Admin"
    state.auth_name = "Cultivation Admin"
    state.cultivation_new_strain_name = "New Variety"
    registry = {
        "provisional_strains": [
            {
                "strain_name": "New Variety",
                "active": True,
            }
        ]
    }

    with (
        patch.object(DashboardState, "_require_active_session", return_value=True),
        patch(
            "qcc_reflex_pilot.qcc_reflex_pilot.save_provisional_strain",
            return_value="New Variety",
        ) as save,
        patch(
            "qcc_reflex_pilot.qcc_reflex_pilot.load_registry",
            return_value=registry,
        ),
    ):
        state.add_cultivation_provisional_strain()

    save.assert_called_once_with("New Variety", "Cultivation Admin")
    assert state.cultivation_provisional_strains == ["New Variety"]
    assert state.cultivation_new_strain_name == ""
    assert "saved for this organization" in (
        state.cultivation_new_strain_message
    )
