from qcc_reflex_pilot.packaging_inventory import (
    packaging_bom_recipes,
    packaging_items,
    packaging_planning_rows,
    packaging_snapshot_rows,
    packaging_suppliers,
)


def test_workbook_seed_has_expected_erp_foundation():
    assert len(packaging_items()) == 265
    assert len(packaging_planning_rows()) >= 190
    assert len(packaging_bom_recipes()) >= 30
    assert len(packaging_suppliers()) >= 20
    assert len(packaging_snapshot_rows()) == 20


def test_items_have_stable_ids_and_ownership():
    items = packaging_items()
    assert len({item["material_id"] for item in items}) == len(items)
    assert {item["ownership"] for item in items} == {"QCC Owned", "Customer Supplied"}
    assert all(item["latest_count_date"] for item in items)


def test_vendor_spelling_variants_are_normalized():
    vendors = {item["vendor"] for item in packaging_items()}
    assert "Artrix" in vendors
    assert "ATRTIX" not in vendors
    assert "ARTIX" not in vendors
    assert "Gamut Packaging" in vendors


def test_boms_are_provisional_and_have_components():
    recipes = packaging_bom_recipes()
    assert all(recipe["status"] == "Provisional" for recipe in recipes)
    assert all(recipe["components"] for recipe in recipes)
