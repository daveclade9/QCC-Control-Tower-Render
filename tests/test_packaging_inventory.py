from qcc_reflex_pilot.packaging_inventory import (
    PACKAGING_BRAND_SCOPES,
    PACKAGING_CATEGORIES,
    packaging_bom_recipes,
    packaging_items,
    packaging_seed_items,
    next_packaging_material_id,
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
    assert {item["ownership"] for item in items} == {"QCC OWNED", "CUSTOMER SUPPLIED"}
    assert all(item["latest_count_date"] for item in items)


def test_vendor_spelling_variants_are_normalized():
    vendors = {item["vendor"] for item in packaging_items()}
    assert "ARTRIX" in vendors
    assert "ATRTIX" not in vendors
    assert "ARTIX" not in vendors
    assert "GAMUT PACKAGING" in vendors


def test_boms_are_provisional_and_have_components():
    recipes = packaging_bom_recipes()
    assert all(recipe["status"] == "Provisional" for recipe in recipes)
    assert all(recipe["components"] for recipe in recipes)


def test_seed_items_have_item_master_defaults():
    item = packaging_seed_items()[0]
    assert item["uom"] == "EACH"
    assert item["default_location"] == "UNASSIGNED"
    assert item["status"] == "ACTIVE"
    assert item["available"] == item["on_hand"]


def test_packaging_master_uses_controlled_uppercase_values():
    assert PACKAGING_BRAND_SCOPES == [
        "CLADE9", "CRAFT KINGS", "LOCALS ONLY", "SHARED USE"
    ]
    assert len(PACKAGING_CATEGORIES) == len(set(PACKAGING_CATEGORIES))
    assert "VAPE HARDWARE" in PACKAGING_CATEGORIES
    assert all(item["item"] == item["item"].upper() for item in packaging_items())
    assert all(supplier["supplier"] == supplier["supplier"].upper() for supplier in packaging_suppliers())


def test_next_material_id_advances_existing_registry():
    assert next_packaging_material_id([
        {"material_id": "PKG-0009"},
        {"material_id": "CUSTOM-1"},
        {"material_id": "PKG-0265"},
    ]) == "PKG-0266"
