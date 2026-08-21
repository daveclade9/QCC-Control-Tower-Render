from datetime import date

import pandas as pd

from qcc_reflex_pilot.data import repair_manufacturing_inventory_ages


def test_repairs_source_production_batch_ages() -> None:
    packages = pd.DataFrame([
        {
            "package_tag": "1A4110300006019000006984",
            "source_license_type": "Manufacturing",
            "source_production_batch": "LR-FB-F56.08.05.2026",
            "production_batch_number": "",
            "inventory_age_days": 0,
            "production_stage": "Packaged Goods",
            "aging_policy": "Manufactured Finished Good - 180 Days",
        },
        {
            "package_tag": "1A4110300006019000006986",
            "source_license_type": "Manufacturing",
            "source_production_batch": "LCG-F3.7-07.23.2026",
            "production_batch_number": "",
            "inventory_age_days": 0,
            "production_stage": "Packaged Goods",
            "aging_policy": "Manufactured Finished Good - 180 Days",
        },
    ])

    repaired = repair_manufacturing_inventory_ages(
        packages, as_of=date(2026, 8, 20)
    ).set_index("package_tag")

    assert repaired.loc["1A4110300006019000006984", "inventory_age_days"] == 15
    assert repaired.loc["1A4110300006019000006986", "inventory_age_days"] == 28
    assert repaired.loc[
        "1A4110300006019000006984", "production_date_source"
    ] == "Source Production Batch"
    assert repaired.loc[
        "1A4110300006019000006984", "production_date"
    ] == "2026-08-05"
    assert repaired.loc[
        "1A4110300006019000006986", "aging_start_date"
    ] == "2026-07-23"
    assert repaired.loc[
        "1A4110300006019000006986", "days_remaining_in_sale_window"
    ] == 152


def test_uses_production_batch_number_and_never_invents_zero() -> None:
    packages = pd.DataFrame([
        {
            "package_tag": "fallback",
            "source_license_type": "Manufacturing",
            "source_production_batch": "",
            "production_batch_number": "ICC5PKIWHPR-F2.5-08.19.2026",
            "inventory_age_days": 0,
            "production_stage": "Packaged Goods",
            "aging_policy": "Manufactured Finished Good - 180 Days",
        },
        {
            "package_tag": "missing",
            "source_license_type": "Manufacturing",
            "source_production_batch": "",
            "production_batch_number": "NO-DATE",
            "inventory_age_days": 0,
            "production_stage": "Packaged Goods",
            "aging_policy": "Manufactured Finished Good - 180 Days",
        },
    ])

    repaired = repair_manufacturing_inventory_ages(
        packages, as_of=date(2026, 8, 20)
    ).set_index("package_tag")

    assert repaired.loc["fallback", "inventory_age_days"] == 1
    assert repaired.loc[
        "fallback", "production_date_source"
    ] == "Production Batch Number"
    assert pd.isna(repaired.loc["missing", "inventory_age_days"])
    assert repaired.loc[
        "missing", "production_date_source"
    ] == "Date Needs Review"


def test_clears_only_the_resolved_manufacturing_date_review() -> None:
    packages = pd.DataFrame([
        {
            "package_tag": "1A4110300006019000006875",
            "source_license_type": "Manufacturing",
            "source_production_batch": "",
            "production_batch_number": "Sativa Blend-F3.6-07.20.2026",
            "production_stage": "Packaged Goods",
            "aging_policy": "Manufactured Finished Good - 180 Days",
            "needs_review": 1,
            "review_reason": "Manufacturing production date needs review",
        },
        {
            "package_tag": "independent-issue",
            "source_license_type": "Manufacturing",
            "source_production_batch": "",
            "production_batch_number": "Sativa Blend-F3.6-07.20.2026",
            "production_stage": "Packaged Goods",
            "aging_policy": "Manufactured Finished Good - 180 Days",
            "needs_review": 1,
            "review_reason": (
                "Manufacturing production date needs review; Administrative hold"
            ),
        },
    ])
    packages["needs_review"] = packages["needs_review"].astype("int64")

    repaired = repair_manufacturing_inventory_ages(
        packages, as_of=date(2026, 8, 20)
    ).set_index("package_tag")

    assert repaired.loc[
        "1A4110300006019000006875", "production_date"
    ] == "2026-07-20"
    assert repaired.loc[
        "1A4110300006019000006875", "inventory_age_days"
    ] == 31
    assert repaired.loc[
        "1A4110300006019000006875", "needs_review"
    ] == 0
    assert repaired.loc[
        "1A4110300006019000006875", "review_reason"
    ] == ""
    assert repaired.loc["independent-issue", "needs_review"] == 1
    assert repaired.loc[
        "independent-issue", "review_reason"
    ] == "Administrative hold"
