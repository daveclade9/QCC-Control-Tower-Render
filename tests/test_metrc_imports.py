from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from qcc_reflex_pilot.metrc_imports import (
    TRANSFER_DB_COLUMNS,
    normalize_transfer_history,
    record_failed_metrc_import,
)


def transfer_source() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Manifest": "0001409482",
            "Origin Lic.": "C000313",
            "Origin Facility": "The QCC Group LLC",
            "Dest. Lic.": "R000001",
            "Destination Facility": "Test Retailer",
            "Dest. Facility Type": "Retailer",
            "Type": "Transfer",
            "Created": "2026-09-14 09:00:00",
            "Package": "1A4110300002A31000038761",
            "State": "Rejected",
            "Item": "Clade9 Diamond Bar 3.5g Flower",
            "Item Category": "Buds/Flower",
            "Actual Ship'd": "24",
            "UoM": "Each",
            "Voided": "False",
        }
    ])


def test_normalize_transfer_history_matches_production_contract() -> None:
    normalized = normalize_transfer_history(
        transfer_source(), "TransfersReport.csv", hashlib.sha256(b"x").hexdigest()
    )
    assert list(normalized.columns) == TRANSFER_DB_COLUMNS
    assert len(normalized) == 1
    row = normalized.iloc[0]
    assert row["record_key"] == (
        "C000313|0001409482|1A4110300002A31000038761"
    )
    assert row["actual_shipped"] == 24
    assert row["voided"] == 0


def test_normalize_transfer_history_keeps_last_duplicate() -> None:
    source = pd.concat([transfer_source(), transfer_source()], ignore_index=True)
    source.loc[1, "State"] = "Returned"
    normalized = normalize_transfer_history(source, "TransfersReport.csv", "hash")
    assert len(normalized) == 1
    assert normalized.iloc[0]["state"] == "Returned"


def test_normalize_transfer_history_explains_wrong_report_type() -> None:
    with pytest.raises(ValueError, match="Missing required transfer columns"):
        normalize_transfer_history(pd.DataFrame({"Lab License No.": ["L1"]}), "labs.csv", "hash")


def test_failed_import_is_retained_as_rejected_audit(monkeypatch) -> None:
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "qcc_reflex_pilot.metrc_imports.record_metrc_import_run", capture
    )
    record_failed_metrc_import(
        source_type="Transfer History",
        filename="empty.csv",
        file_bytes=b"",
        details="No rows contained required identifiers.",
        imported_by="Test Admin",
    )
    assert captured["status"] == "Rejected"
    assert captured["stored_rows"] == 0
    assert captured["file_hash"] == hashlib.sha256(b"").hexdigest()
    assert captured["imported_by"] == "Test Admin"
