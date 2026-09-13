import pytest
from qcc_reflex_pilot.warehouse_ui import warehouse_workspace


@pytest.mark.parametrize("section, expected, absent", [
    ("registry_import", "Preview CSV", ["Save Location", "Preview Activity"]),
    ("locations", "Save Location", ["Preview CSV", "Preview Activity"]),
    ("inventory_activity", "Preview Activity", ["Preview CSV", "Save Location"]),
])
def test_sections_only_render_their_own_controls(section, expected, absent):
    rendered = str(warehouse_workspace(section))
    assert expected in rendered
    for label in absent:
        assert label not in rendered


def test_unknown_section_rejected():
    with pytest.raises(ValueError, match="Unknown warehouse section"):
        warehouse_workspace("unknown")
