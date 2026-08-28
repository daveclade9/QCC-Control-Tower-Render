"""Normalize Metrc plant exports for the cultivation control tower."""

from __future__ import annotations

import hashlib
import io
import re
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd


REQUIRED_PLANT_EXPORTS = ("harvests", "flowering", "vegetative", "plantings")


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).strip().split())


def _number(value: Any) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(parsed) else float(parsed)


def _whole(value: Any) -> int:
    return max(0, int(round(_number(value))))


def _boolean(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1", "y"}
    return bool(value)


def _iso_date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.date().isoformat()


def plant_facility(location: Any) -> str:
    """Group Metrc locations into the two cultivation operating areas."""
    label = _text(location)
    return "1A Building" if label.casefold().startswith("1a ") else "Main Cultivation"


def classify_plant_export(filename: str, columns: Iterable[Any]) -> str:
    """Classify one Metrc workbook using headers first and filename second."""
    names = {_text(column).casefold() for column in columns}
    if {"harvest batch", "wet weight", "total weight packaged"} <= names:
        return "harvests"
    if {"source plant", "tracked", "destroyed", "plants"} <= names:
        return "plantings"
    if {"tag", "plant batch", "phase date", "harvested"} <= names:
        lowered = filename.casefold()
        if "flower" in lowered:
            return "flowering"
        if "vegetative" in lowered or "veg" in lowered:
            return "vegetative"
        raise ValueError(
            f"{filename} has individual-plant columns, but its phase cannot be "
            "identified from the filename. Include Flowering or Vegetative."
        )
    raise ValueError(f"{filename} does not match a supported Metrc plant export.")


def _active_plant_rows(frame: pd.DataFrame, phase: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        location = _text(row.get("Location"))
        rows.append({
            "tag": _text(row.get("Tag")),
            "strain": _text(row.get("Strain")),
            "location": location,
            "facility": plant_facility(location),
            "sublocation": _text(row.get("Sublocation")),
            "phase": phase,
            "hold": _boolean(row.get("Hold", False)),
            "plant_batch": _text(row.get("Plant Batch")),
            "plant_batch_type": _text(row.get("Plant Batch Type")),
            "plant_batch_date": _iso_date(row.get("Plant Batch Date")),
            "phase_date": _iso_date(row.get("Phase Date")),
        })
    return rows


def _planting_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        location = _text(row.get("Location"))
        rows.append({
            "plant_batch": _text(row.get("Plant Batch")),
            "strain": _text(row.get("Strain")),
            "location": location,
            "facility": plant_facility(location),
            "type": _text(row.get("Type")),
            "hold": _boolean(row.get("Hold", False)),
            "plants": _whole(row.get("Plants")),
            "tracked": _whole(row.get("Tracked")),
            "packaged": _whole(row.get("Packaged")),
            "destroyed": _whole(row.get("Destroyed")),
            "source_package": _text(row.get("Source Package")),
            "source_plant": _text(row.get("Source Plant")),
            "source_plant_batch": _text(row.get("Source Plant Batch")),
            "batch_date": _iso_date(row.get("Batch Date")),
        })
    return rows


def _harvest_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        location = _text(row.get("Location"))
        harvest_batch = _text(row.get("Harvest Batch"))
        rows.append({
            "harvest_batch": harvest_batch,
            "strain": _text(row.get("Strain")),
            "location": location,
            "facility": plant_facility(location),
            "plants": _whole(row.get("Plants")),
            "wet_weight_lb": round(_number(row.get("Wet Weight")), 4),
            "waste_lb": round(_number(row.get("Waste")), 4),
            "packaged_weight_lb": round(_number(row.get("Total Weight Packaged")), 4),
            "package_count": _whole(row.get("Package Count")),
            "remaining_weight_lb": round(_number(row.get("Weight")), 4),
            "unit": _text(row.get("Unit Of Measure")),
            "lab_testing": _text(row.get("Lab Testing")),
            "administrative_hold": _boolean(row.get("Administrative Hold", False)),
            "harvest_date": _iso_date(row.get("Date")),
            "fresh_frozen": bool(re.search(r"(?:WPFF|FRESH[ _-]*FROZEN)", harvest_batch, re.I)),
        })
    return rows


def parse_metrc_plant_exports(
    files: Iterable[tuple[str, bytes]],
    *,
    imported_at: datetime | None = None,
) -> dict[str, Any]:
    """Parse one complete four-workbook Metrc plant snapshot."""
    frames: dict[str, pd.DataFrame] = {}
    source_files: dict[str, str] = {}
    digest = hashlib.sha256()
    for filename, content in files:
        if not filename.lower().endswith(".xlsx"):
            raise ValueError(f"{filename} is not an .xlsx Metrc plant export.")
        digest.update(filename.encode("utf-8", errors="ignore"))
        digest.update(content)
        frame = pd.read_excel(io.BytesIO(content), sheet_name=0)
        kind = classify_plant_export(filename, frame.columns)
        if kind in frames:
            raise ValueError(f"More than one {kind.title()} export was selected.")
        frames[kind] = frame
        source_files[kind] = filename
    missing = [kind for kind in REQUIRED_PLANT_EXPORTS if kind not in frames]
    if missing:
        raise ValueError(
            "Select all four active Metrc exports. Missing: "
            + ", ".join(label.title() for label in missing)
            + "."
        )
    loaded_at = imported_at or datetime.now().astimezone()
    flowering = _active_plant_rows(frames["flowering"], "Flowering")
    vegetative = _active_plant_rows(frames["vegetative"], "Vegetative")
    plantings = _planting_rows(frames["plantings"])
    harvests = _harvest_rows(frames["harvests"])
    active_tags = [row["tag"] for row in [*flowering, *vegetative] if row["tag"]]
    if len(active_tags) != len(set(active_tags)):
        raise ValueError("The selected Flowering and Vegetative exports contain duplicate tags.")
    snapshot_id = f"PLANT-{loaded_at:%Y%m%dT%H%M%S}-{digest.hexdigest()[:10]}"
    harvest_dates = [row["harvest_date"] for row in harvests if row["harvest_date"]]
    return {
        "snapshot_id": snapshot_id,
        "imported_at": loaded_at.isoformat(),
        "source_files": source_files,
        "flowering": flowering,
        "vegetative": vegetative,
        "plantings": plantings,
        "harvests": harvests,
        "summary": {
            "flowering_plants": len(flowering),
            "vegetative_plants": len(vegetative),
            "active_plantings": len(plantings),
            "planting_plants": sum(row["plants"] for row in plantings),
            "harvest_batches": len(harvests),
            "fresh_frozen_batches": sum(1 for row in harvests if row["fresh_frozen"]),
            "harvest_date_min": min(harvest_dates) if harvest_dates else "",
            "harvest_date_max": max(harvest_dates) if harvest_dates else "",
        },
    }


def crop_code(value: Any) -> str:
    match = re.search(r"\bF([1-5])[.](\d+)\b", _text(value), re.I)
    return f"F{match.group(1)}.{match.group(2)}" if match else ""


def plant_crop_reconciliation(
    snapshot: dict[str, Any],
    crop_allocations: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare crop-report expected plants with actual Metrc harvest populations."""
    expected: dict[tuple[str, str], dict[str, Any]] = {}
    for row in crop_allocations:
        crop = _text(row.get("crop"))
        strain = _text(row.get("strain"))
        key = (crop.casefold(), strain.casefold())
        item = expected.setdefault(key, {
            "Crop": crop,
            "Room": _text(row.get("room")),
            "Strain": strain,
            "Harvest Date": _iso_date(row.get("harvest_date")),
            "Crop Report Plants": 0,
        })
        item["Crop Report Plants"] += _whole(_number(row.get("square_feet")) * 0.75)
    actual: dict[tuple[str, str], int] = {}
    for row in snapshot.get("harvests", []):
        crop = crop_code(row.get("harvest_batch"))
        strain = _text(row.get("strain"))
        if crop and strain:
            key = (crop.casefold(), strain.casefold())
            actual[key] = actual.get(key, 0) + _whole(row.get("plants"))
    today = date.today().isoformat()
    results: list[dict[str, Any]] = []
    for key, row in expected.items():
        actual_plants = actual.get(key, 0)
        expected_plants = int(row["Crop Report Plants"])
        variance = actual_plants - expected_plants if actual_plants else 0
        if actual_plants:
            status = "Matched" if abs(variance) <= 2 else "Review"
        elif row["Harvest Date"] > today:
            status = "Upcoming"
        else:
            status = "Awaiting Metrc Harvest"
        results.append({
            **row,
            "Metrc Harvest Plants": actual_plants,
            "Variance": variance,
            "Status": status,
        })
    return sorted(results, key=lambda row: (row["Harvest Date"], row["Crop"], row["Strain"]), reverse=True)
