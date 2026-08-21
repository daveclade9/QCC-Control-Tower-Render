import pandas as pd

from qcc_reflex_pilot.data import promote_legitimate_manufacturing_samples


def test_promotes_sample_when_stage_is_the_only_review_issue() -> None:
    packages = pd.DataFrame([{
        "package_tag": "1A4110300006019000006903",
        "source_license_type": "Manufacturing",
        "item": "Diamond Bar - Live Rosin 0.5g Sample",
        "qa_status": "Test Passed",
        "aging_start_date": "2026-07-29",
        "inventory_age_days": 22,
        "production_stage": "Needs Review",
        "review_reason": "Production stage unclear",
        "needs_review": True,
        "is_finished_retail_sku": False,
        "include_in_cpg": False,
        "is_retention_sample": False,
    }])

    promoted = promote_legitimate_manufacturing_samples(packages).iloc[0]

    assert promoted["production_stage"] == "Packaged Goods"
    assert bool(promoted["include_in_cpg"])
    assert not bool(promoted["needs_review"])
    assert promoted["review_reason"] == ""


def test_preserves_sample_with_an_independent_review_issue() -> None:
    packages = pd.DataFrame([{
        "package_tag": "blocked-sample",
        "source_license_type": "Manufacturing",
        "item": "Live Rosin 0.5g Sample",
        "qa_status": "Test Passed",
        "aging_start_date": "2026-07-29",
        "inventory_age_days": 22,
        "production_stage": "Needs Review",
        "review_reason": "Production stage unclear; Administrative hold",
        "needs_review": True,
        "is_finished_retail_sku": False,
        "include_in_cpg": False,
        "is_retention_sample": False,
    }])

    preserved = promote_legitimate_manufacturing_samples(packages).iloc[0]

    assert preserved["production_stage"] == "Needs Review"
    assert not bool(preserved["include_in_cpg"])
    assert bool(preserved["needs_review"])
