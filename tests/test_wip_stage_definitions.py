import pandas as pd

from qcc_reflex_pilot.data import (
    normalize_cultivation_byproduct_stages,
    wip_inventory_status,
)


def test_cultivation_trim_and_shake_are_reclassified_as_byproducts():
    packages = pd.DataFrame([
        {
            "production_stage": "WIP-Cultivation",
            "material_type": "Flower",
            "item": "Diamond Bar Bulk Flower",
            "category": "Bud/Flower - Bulk",
        },
        {
            "production_stage": "WIP-Cultivation",
            "material_type": "Trim",
            "item": "Diamond Bar Bulk Trim",
            "category": "Shake/Trim (By Strain)",
        },
        {
            "production_stage": "WIP-Cultivation",
            "material_type": "Shake",
            "item": "Diamond Bar Shake",
            "category": "Shake/Trim (By Strain)",
        },
        {
            "production_stage": "WIP-Cultivation",
            "material_type": "Flower",
            "item": "Diamond Bar Bulk Flower",
            "category": "Bud/Flower - Bulk",
            "location": "Vault - Retention/Stability Storage",
            "is_retention_sample": True,
        },
    ])

    result = normalize_cultivation_byproduct_stages(packages)

    assert result["production_stage"].tolist() == [
        "WIP-Cultivation",
        "Trim",
        "Shake",
        "Retention Storage",
    ]


def test_physical_wip_uses_the_normalized_definition_before_planning():
    packages = pd.DataFrame([
        {
            "package_tag": "FLOWER",
            "production_stage": "WIP-Cultivation",
            "qa_status": "Test Passed",
            "qcc_owned": True,
            "calculated_weight_grams": 100,
            "material_type": "Flower",
            "item": "Diamond Bar Bulk Flower",
        },
        {
            "package_tag": "TRIM",
            "production_stage": "WIP-Cultivation",
            "qa_status": "Test Passed",
            "qcc_owned": True,
            "calculated_weight_grams": 100,
            "material_type": "Trim",
            "item": "Diamond Bar Bulk Trim",
        },
    ])

    result = wip_inventory_status(packages, pd.DataFrame(), pd.DataFrame())

    assert result["package_tag"].tolist() == ["FLOWER"]


def test_source_ownership_and_readiness_create_distinct_unfinished_stages():
    shared = {
        "source_license_type": "Cultivation",
        "material_type": "Flower",
        "item": "Diamond Bar Bulk Flower",
        "category": "Bud/Flower - Bulk",
        "location": "Vault - Approved for Sale",
    }
    packages = pd.DataFrame([
        {
            **shared,
            "production_stage": "WIP-Cultivation",
            "facility": "Building 33 (C9)",
            "current_facility": "Building 33 (C9)",
            "ownership_status": "QCC-Owned / Clade9 Origin",
            "qa_status": "Test Passed",
        },
        {
            **shared,
            "production_stage": "WIP-Cultivation",
            "facility": "Building 1A",
            "current_facility": "Building 33 (C9)",
            "ownership_status": "QCC-Owned / Purchased from Building 1A",
            "qa_status": "Test Passed",
        },
        {
            **shared,
            "production_stage": "Pre-WIP",
            "facility": "Building 1A",
            "current_facility": "Building 33 (C9)",
            "ownership_status": "QCC-Owned / Purchased from Building 1A",
            "qa_status": "Not Submitted",
        },
        {
            **shared,
            "production_stage": "Sellable Bulk",
            "facility": "Building 1A",
            "current_facility": "Building 1A",
            "ownership_status": "Partner-Owned / Compliance Managed",
            "qa_status": "Test Passed",
        },
        {
            **shared,
            "production_stage": "Pre-WIP",
            "facility": "Building 1A",
            "current_facility": "Building 1A",
            "ownership_status": "Partner-Owned / Compliance Managed",
            "qa_status": "Not Submitted",
        },
        {
            **shared,
            "production_stage": "Pre-WIP",
            "facility": "Building 33 (C9)",
            "current_facility": "Building 33 (C9)",
            "ownership_status": "QCC-Owned / Clade9 Origin",
            "qa_status": "Not Submitted",
        },
        {
            **shared,
            "production_stage": "WIP-Cultivation",
            "facility": "Building 1A",
            "current_facility": "Building 33 (C9)",
            "ownership_status": "QCC-Owned / Purchased from Building 1A",
            "qa_status": "Test Passed",
            "location": "Secure Storage",
        },
    ])

    result = normalize_cultivation_byproduct_stages(packages)

    assert result["production_stage"].tolist() == [
        "WIP-Cultivation",
        "WIP-Purchased 1A",
        "Pre-WIP-Purchased 1A",
        "1A Sellable Bulk",
        "1A Pending Bulk Opportunity",
        "Pre-WIP-Cultivation",
        "Pre-WIP-Purchased 1A",
    ]


def test_1a_byproducts_are_opportunities_until_purchased():
    packages = pd.DataFrame([
        {
            "production_stage": "WIP-Cultivation",
            "facility": "Building 1A",
            "current_facility": "Building 1A",
            "ownership_status": "Partner-Owned / Compliance Managed",
            "source_license_type": "Cultivation",
            "qa_status": "Test Passed",
            "material_type": "Trim",
            "item": "Bulk Trim",
        },
        {
            "production_stage": "WIP-Cultivation",
            "facility": "Building 1A",
            "current_facility": "Building 33 (C9)",
            "ownership_status": "QCC-Owned / Purchased from Building 1A",
            "source_license_type": "Cultivation",
            "qa_status": "Test Passed",
            "material_type": "Shake",
            "item": "Bulk Shake",
        },
    ])

    result = normalize_cultivation_byproduct_stages(packages)

    assert result["production_stage"].tolist() == ["1A Sellable Bulk", "Shake"]
