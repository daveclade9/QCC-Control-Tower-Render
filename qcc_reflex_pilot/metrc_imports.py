"""Shared Metrc file-ingestion adapters for the Reflex administration workspace.

The adapters in this module are UI-independent.  A future Metrc API client can
feed the same normalize/store functions without changing downstream modules.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import psycopg
except ImportError:  # pragma: no cover - demo builds can compile without DB extras.
    psycopg = None

from .data import _invalidate_dashboard_caches, database_url
from .rules import classify_sku_type, infer_brand, infer_strain, normalize_strain_name


TENANT_ID = os.getenv("QCC_TENANT_ID", "qcc").strip() or "qcc"
INVENTORY_RULE_VERSION = "0.9.6.121-reflex"
EASTERN = ZoneInfo("America/New_York")

ACTIVE_PACKAGE_REQUIRED_COLUMNS = [
    "Tag", "Source Harvest(s)", "Location", "Item", "Category",
    "Item Strain", "Quantity", "Unit Of Measure", "Lab Test Status",
]
ACTIVE_PACKAGE_COLUMN_MAP = {
    "Tag": "package_tag", "Source Harvest(s)": "source_harvest",
    "Source Package(s)": "source_packages", "Location": "location",
    "Item": "item", "Category": "category", "Item Strain": "strain",
    "Quantity": "quantity", "Unit Of Measure": "unit",
    "Lab Test Status": "lab_status", "Packaged Date": "packaged_date",
    "Administrative Hold": "administrative_hold", "Finished Goods": "finished_goods",
    "Production Batch Number": "production_batch_number",
    "Source Production Batch": "source_production_batch",
}
ACTIVE_PACKAGE_COLUMNS = [
    "package_tag", "source_license_number", "source_license_type", "brand",
    "strain", "source_strain", "metrc_strain", "sku_type", "item", "category",
    "quantity", "unit", "calculated_weight_grams", "location", "source_harvest",
    "source_packages", "production_batch_number", "source_production_batch",
    "production_date_source", "aging_policy", "qa_status", "lab_status",
    "production_stage", "material_type", "current_facility", "facility",
    "ownership_status", "qcc_owned", "inventory_age_days", "packaged_date",
    "harvest_date", "production_date", "aging_start_date", "expiration_date",
    "days_remaining_in_sale_window", "planning_allocation", "commercial_status",
    "business_area", "commercial_intent", "review_reason", "needs_review",
    "is_finished_retail_sku", "include_in_cpg", "is_retention_sample",
    "classification_rule_version", "source_filename",
]
CONFIRMED_PURCHASED_1A_TAGS = {
    "1A4110300002A31000037325", "1A4110300002A31000037328",
    "1A4110300002A31000037331", "1A4110300002A31000037334",
    "1A4110300002A31000037267",
}

TRANSFER_REQUIRED_COLUMNS = [
    "Manifest", "Origin Lic.", "Origin Facility", "Dest. Lic.",
    "Destination Facility", "Dest. Facility Type", "Type", "Created",
    "Package", "State", "Item", "Item Category",
]
TRANSFER_COLUMN_MAP = {
    "Manifest": "manifest",
    "Inv. Nbr": "invoice_number",
    "Origin Lic.": "origin_license",
    "Origin Facility": "origin_facility",
    "Origin Facility Type": "origin_facility_type",
    "Dest. Lic.": "destination_license",
    "Destination Facility": "destination_facility",
    "Dest. Facility Type": "destination_facility_type",
    "Type": "transfer_type",
    "Created": "created_at",
    "Created by User": "created_by_user",
    "Received": "received_at",
    "Received by User": "received_by_user",
    "Voided": "voided",
    "Package": "package_tag",
    "State": "state",
    "Item": "item",
    "Item Category": "item_category",
    "Shipper Dollar Amount": "shipper_dollar_amount",
    "Receiver Dollar Amount": "receiver_dollar_amount",
    "Actual Ship'd": "actual_shipped",
    "UoM": "actual_shipped_uom",
    "Actual Rcv'd": "actual_received",
    "UoM.1": "actual_received_uom",
    "Count Ship'd": "count_shipped",
    "UoM.2": "count_shipped_uom",
    "Count Rcv'd": "count_received",
    "UoM.3": "count_received_uom",
    "Unit Weight (g)": "unit_weight_grams",
    "Count Weight (g)": "count_weight_grams",
    "Weight Ship'd": "weight_shipped",
    "UoM.4": "weight_shipped_uom",
    "Weight Rcv'd": "weight_received",
    "UoM.5": "weight_received_uom",
    "Gross Wgt": "gross_weight",
    "UoM.6": "gross_weight_uom",
    "Count % Var": "count_variance_percent",
    "Weight % Var": "weight_variance_percent",
}
TRANSFER_DB_COLUMNS = [
    "record_key", "manifest", "invoice_number", "origin_license",
    "origin_facility", "origin_facility_type", "destination_license",
    "destination_facility", "destination_facility_type", "transfer_type",
    "created_at", "created_by_user", "received_at", "received_by_user",
    "voided", "package_tag", "state", "item", "item_category",
    "shipper_dollar_amount", "receiver_dollar_amount", "actual_shipped",
    "actual_shipped_uom", "actual_received", "actual_received_uom",
    "count_shipped", "count_shipped_uom", "count_received",
    "count_received_uom", "unit_weight_grams", "count_weight_grams",
    "weight_shipped", "weight_shipped_uom", "weight_received",
    "weight_received_uom", "gross_weight", "gross_weight_uom",
    "count_variance_percent", "weight_variance_percent", "source_filename",
    "source_file_hash", "imported_at",
]
TRANSFER_NUMERIC_COLUMNS = {
    "shipper_dollar_amount", "receiver_dollar_amount", "actual_shipped",
    "actual_received", "count_shipped", "count_received", "unit_weight_grams",
    "count_weight_grams", "weight_shipped", "weight_received", "gross_weight",
    "count_variance_percent", "weight_variance_percent",
}


def _require_database() -> str:
    url = database_url()
    if not url or psycopg is None:
        raise RuntimeError("Supabase database access is not configured.")
    return url


def _read_csv(file_bytes: bytes) -> pd.DataFrame:
    buffer = io.BytesIO(file_bytes)
    try:
        return pd.read_csv(buffer, dtype=str, low_memory=False)
    except UnicodeDecodeError:
        buffer.seek(0)
        return pd.read_csv(buffer, dtype=str, low_memory=False, encoding="latin-1")


def _boolean_int(value: Any) -> int:
    return int(str(value or "").strip().casefold() in {"1", "true", "yes", "y", "on"})


def _sql_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def normalize_transfer_history(
    source_data: pd.DataFrame, filename: str, file_hash: str
) -> pd.DataFrame:
    """Normalize a Metrc Transfers Report using the legacy production contract."""
    source = source_data.copy()
    source.columns = [str(column).replace("\ufeff", "").strip() for column in source]
    missing = [column for column in TRANSFER_REQUIRED_COLUMNS if column not in source]
    if missing:
        raise ValueError("Missing required transfer columns: " + ", ".join(missing))
    data = source.rename(columns=TRANSFER_COLUMN_MAP).copy()
    for column in TRANSFER_DB_COLUMNS:
        if column in {"record_key", "source_filename", "source_file_hash", "imported_at"}:
            continue
        if column not in data:
            data[column] = pd.NA if column in TRANSFER_NUMERIC_COLUMNS else ""
        if column in TRANSFER_NUMERIC_COLUMNS:
            data[column] = pd.to_numeric(data[column], errors="coerce")
        elif column == "voided":
            data[column] = data[column].map(_boolean_int)
        else:
            data[column] = data[column].fillna("").astype(str).str.strip()
    for column in ("created_at", "received_at"):
        parsed = pd.to_datetime(data[column], errors="coerce")
        data[column] = parsed.dt.strftime("%Y-%m-%dT%H:%M:%S")
    valid = (
        data["manifest"].ne("")
        & data["package_tag"].ne("")
        & data["origin_license"].ne("")
    )
    data = data.loc[valid].copy()
    if data.empty:
        raise ValueError("No rows contained an Origin License, Manifest, and Package Tag.")
    data["record_key"] = (
        data["origin_license"] + "|" + data["manifest"] + "|" + data["package_tag"]
    )
    data["source_filename"] = filename
    data["source_file_hash"] = file_hash
    data["imported_at"] = datetime.now(timezone.utc).isoformat()
    return data.drop_duplicates("record_key", keep="last")[TRANSFER_DB_COLUMNS]


def _ensure_import_audit(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_metrc_import_runs (
            import_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            adapter TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            source_rows INTEGER NOT NULL DEFAULT 0,
            stored_rows INTEGER NOT NULL DEFAULT 0,
            inserted_rows INTEGER NOT NULL DEFAULT 0,
            updated_rows INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            imported_by TEXT NOT NULL DEFAULT '',
            imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_qcc_metrc_import_runs_tenant_time "
        "ON qcc_metrc_import_runs(tenant_id, imported_at DESC)"
    )


def record_metrc_import_run(
    *, source_type: str, adapter: str, filename: str, file_hash: str,
    source_rows: int, stored_rows: int, inserted_rows: int, updated_rows: int,
    status: str, details: str = "", imported_by: str = "",
) -> None:
    """Write a tenant-aware audit record for file and future API ingestion."""
    url = _require_database()
    identity = f"{TENANT_ID}|{source_type}|{file_hash}"
    import_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    with psycopg.connect(url, connect_timeout=20) as connection:
        _ensure_import_audit(connection)
        connection.execute(
            """
            INSERT INTO qcc_metrc_import_runs (
                import_id, tenant_id, source_type, adapter, filename, file_hash,
                source_rows, stored_rows, inserted_rows, updated_rows, status,
                details, imported_by, imported_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT(import_id) DO UPDATE SET
                filename=EXCLUDED.filename, source_rows=EXCLUDED.source_rows,
                stored_rows=EXCLUDED.stored_rows, inserted_rows=EXCLUDED.inserted_rows,
                updated_rows=EXCLUDED.updated_rows, status=EXCLUDED.status,
                details=EXCLUDED.details, imported_by=EXCLUDED.imported_by,
                imported_at=EXCLUDED.imported_at
            """,
            (
                import_id, TENANT_ID, source_type, adapter, filename, file_hash,
                source_rows, stored_rows, inserted_rows, updated_rows, status,
                details, imported_by,
            ),
        )
        connection.commit()


def record_failed_metrc_import(
    *, source_type: str, filename: str, file_bytes: bytes, details: str,
    imported_by: str = "",
) -> None:
    """Retain rejected and empty uploads without publishing operational rows."""
    record_metrc_import_run(
        source_type=source_type,
        adapter="Metrc File Upload",
        filename=filename,
        file_hash=hashlib.sha256(file_bytes).hexdigest(),
        source_rows=0,
        stored_rows=0,
        inserted_rows=0,
        updated_rows=0,
        status="Rejected",
        details=details,
        imported_by=imported_by,
    )


def import_transfer_history_bytes(
    filename: str, file_bytes: bytes, *, imported_by: str = ""
) -> dict[str, Any]:
    """Duplicate-safe transfer import shared by Reflex and the future API adapter."""
    url = _require_database()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    source = _read_csv(file_bytes)
    normalized = normalize_transfer_history(source, filename, file_hash)
    with psycopg.connect(url, connect_timeout=20) as connection:
        previous = connection.execute(
            "SELECT stored_rows FROM transfer_import_log WHERE source_file_hash = %s",
            (file_hash,),
        ).fetchone()
        if previous:
            result = {
                "File": filename, "Status": "Already Imported",
                "Source Rows": len(source), "Stored Rows": int(previous[0]),
                "Inserted": 0, "Updated": 0, "Details": "Duplicate file skipped.",
            }
        else:
            existing = {
                row[0] for row in connection.execute(
                    "SELECT record_key FROM transfer_records"
                ).fetchall()
            }
            incoming = set(normalized["record_key"])
            placeholders = ", ".join("%s" for _ in TRANSFER_DB_COLUMNS)
            updates = ", ".join(
                f"{column}=EXCLUDED.{column}"
                for column in TRANSFER_DB_COLUMNS if column != "record_key"
            )
            records = [
                tuple(_sql_value(value) for value in row)
                for row in normalized.itertuples(index=False, name=None)
            ]
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO transfer_records (" + ", ".join(TRANSFER_DB_COLUMNS)
                    + ") VALUES (" + placeholders + ") ON CONFLICT(record_key) "
                    "DO UPDATE SET " + updates,
                    records,
                )
            created = pd.to_datetime(normalized["created_at"], errors="coerce")
            inserted = len(incoming - existing)
            updated = len(incoming & existing)
            connection.execute(
                """
                INSERT INTO transfer_import_log (
                    source_file_hash, source_filename, file_size_bytes, source_rows,
                    stored_rows, inserted_rows, updated_rows, created_min, created_max,
                    imported_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(source_file_hash) DO NOTHING
                """,
                (
                    file_hash, filename, len(file_bytes), len(source), len(normalized),
                    inserted, updated,
                    created.min().isoformat() if created.notna().any() else None,
                    created.max().isoformat() if created.notna().any() else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
            result = {
                "File": filename, "Status": "Imported", "Source Rows": len(source),
                "Stored Rows": len(normalized), "Inserted": inserted,
                "Updated": updated, "Details": "Transfer history published.",
            }
    record_metrc_import_run(
        source_type="Transfer History", adapter="Metrc File Upload",
        filename=filename, file_hash=file_hash,
        source_rows=int(result["Source Rows"]), stored_rows=int(result["Stored Rows"]),
        inserted_rows=int(result["Inserted"]), updated_rows=int(result["Updated"]),
        status=str(result["Status"]), details=str(result["Details"]),
        imported_by=imported_by,
    )
    _invalidate_dashboard_caches()
    return result


def _read_active_package_file(filename: str, payload: bytes) -> pd.DataFrame:
    if filename.lower().endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(payload), dtype=object)
    return _read_csv(payload)


def _license_metadata(filename: str) -> tuple[str, str]:
    match = re.search(r"(?<![A-Za-z0-9])([CM]\d{6})(?!\d)", filename, re.I)
    if not match:
        raise ValueError(
            f"{filename}: license not found in filename. Use the original Metrc "
            "export filename containing C###### or M######."
        )
    number = match.group(1).upper()
    return number, "Manufacturing" if number.startswith("M") else "Cultivation"


def _text(value: Any, default: str = "") -> str:
    if value is None or pd.isna(value):
        return default
    return re.sub(r"\s+", " ", str(value)).strip()


def _parse_date(value: Any) -> pd.Timestamp | Any:
    if value is None or pd.isna(value) or not _text(value):
        return pd.NaT
    number = pd.to_numeric(value, errors="coerce")
    if pd.notna(number) and 20000 <= float(number) <= 80000:
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(float(number), unit="D")
    return pd.to_datetime(value, errors="coerce")


def _harvest_date(value: Any) -> pd.Timestamp | Any:
    dates: list[pd.Timestamp] = []
    for month, day, year in re.findall(
        r"(?<!\d)(\d{1,2})[./_-](\d{1,2})[./_-](\d{2}|\d{4})(?!\d)",
        _text(value),
    ):
        try:
            dates.append(pd.Timestamp(
                year=int(year) + (2000 if len(year) == 2 else 0),
                month=int(month), day=int(day),
            ))
        except ValueError:
            pass
    return min(dates) if dates else pd.NaT


def _production_date(row: pd.Series) -> tuple[pd.Timestamp | Any, str]:
    for column, source in (
        ("source_production_batch", "Source Production Batch"),
        ("production_batch_number", "Production Batch Number"),
    ):
        matches = re.findall(
            r"(?<!\d)(\d{1,2})[./_-](\d{1,2})[./_-](\d{2}|\d{4})(?!\d)",
            _text(row.get(column)),
        )
        for month, day, year in reversed(matches):
            try:
                return pd.Timestamp(
                    year=int(year) + (2000 if len(year) == 2 else 0),
                    month=int(month), day=int(day),
                ), source
            except ValueError:
                pass
    return pd.NaT, "Date Needs Review"


def _qa_status(value: Any) -> str:
    compact = re.sub(r"[^a-z]", "", _text(value).casefold())
    if compact in {"testpassed", "retestpassed", "passed"}: return "Test Passed"
    if compact in {"testfailed", "retestfailed", "failed"}: return "Test Failed"
    if compact in {"submittedfortesting", "submitted"}: return "Submitted for Testing"
    if compact in {"testinginprogress", "inprogress"}: return "Testing in Progress"
    if compact in {"notsubmitted", "notrequired", "untested"}: return "Not Submitted"
    return _text(value, "Unknown")


def _material_type(row: pd.Series) -> str:
    combined = " ".join(_text(row.get(key)).casefold() for key in ("item", "category", "location"))
    rules = (
        (r"edible|gumm|chocolate", "Edible"),
        (r"vape|cartridge|disposable", "Vape"),
        (r"kief|keef", "Kief"),
        (r"infused\s+pre[- ]?roll", "Infused Pre-Roll"),
        (r"pre[- ]?roll", "Pre-Roll"), (r"trim", "Trim"), (r"shake", "Shake"),
        (r"concentrate|extract|distillate|resin|rosin|oil", "Extraction Material"),
        (r"seed|clone", "Cultivation Inventory"), (r"flower|bud|bulk", "Flower"),
    )
    return next((name for pattern, name in rules if re.search(pattern, combined)), "Other")


def _item_weight(item: Any) -> float | None:
    text = _text(item).casefold().replace("½", "0.5")
    multiply = re.search(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mg|g|gram|grams|oz|ounce|ounces)", text)
    if multiply:
        count, amount, unit = float(multiply.group(1)), float(multiply.group(2)), multiply.group(3)
        return count * amount * (0.001 if unit == "mg" else 28.349523125 if unit.startswith("o") else 1)
    converted = [
        float(amount) * (0.001 if unit == "mg" else 28.349523125 if unit.startswith("o") else 1)
        for amount, unit in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*(mg|g|gram|grams|oz|ounce|ounces)\b", text)
    ]
    return max((weight for weight in converted if weight <= 1000), default=None)


def _weight_grams(row: pd.Series) -> float:
    quantity = pd.to_numeric(row.get("quantity"), errors="coerce")
    quantity = 0.0 if pd.isna(quantity) else float(quantity)
    unit = _text(row.get("unit")).casefold()
    factor = {
        "g": 1, "gram": 1, "grams": 1, "kg": 1000, "kilogram": 1000,
        "kilograms": 1000, "lb": 453.59237, "lbs": 453.59237,
        "pound": 453.59237, "pounds": 453.59237, "oz": 28.349523125,
        "ounce": 28.349523125, "ounces": 28.349523125, "mg": 0.001,
    }.get(unit)
    if factor is not None: return quantity * factor
    each = _item_weight(row.get("item")) if unit in {"ea", "each", "unit", "units", "count", "ct"} else None
    return quantity * each if each is not None else 0.0


def _finished_retail(row: pd.Series) -> bool:
    item = _text(row.get("item")).casefold()
    category = _text(row.get("category")).casefold()
    if re.search(r"\b(?:bulk|wip|sample)\b", item) or "raw pre-roll" in category:
        return False
    if row.get("license_type") == "Manufacturing":
        return bool(
            re.search(r"infused\s+pre[- ]?roll|vape|cartridge|disposable|gumm|edible|chocolate", item)
            or ("concentrate (each)" in category and re.search(r"\d+(?:\.\d+)?\s*g\b", item))
            or ("bud/flower - packaged" in category and re.search(r"\b(?:ea|each|\d+(?:\.\d+)?\s*g)\b", item))
        )
    return bool(re.search(r"\b(?:ea|each)\b", item) and re.search(r"packaged|raw\s+pre[- ]?roll", category))


def _bulk_input(row: pd.Series) -> bool:
    combined = " ".join(_text(row.get(key)).casefold() for key in ("item", "category", "location"))
    return not re.search(r"trim|shake|retention|stability|fresh[ -]?frozen", combined) and bool(
        re.search(r"\b(?:bulk|bud|flower|mids?|smalls?)\b", combined)
    )


def _production_stage(row: pd.Series) -> str:
    item = _text(row.get("item")).casefold()
    category = _text(row.get("category")).casefold()
    location = _text(row.get("location")).casefold()
    combined = f"{item} {category} {location}"
    qa = row["qa_status"]
    if re.search(r"secure waste|waste vault|\bwaste\b", combined): return "Secure Waste"
    if re.search(r"retention|stability", combined): return "Retention Storage"
    if row["license_type"] == "Cultivation" and "vault" in location and re.search(r"fresh[\s_-]*frozen|wpff", combined):
        return "Failed - On Hold" if qa == "Test Failed" else "Pre-WIP-Manufacturing"
    if row["is_finished_retail_sku"]:
        return "Failed - On Hold" if qa == "Test Failed" else "Packaged Goods"
    if qa == "Test Failed" or "extraction" in location: return "Pending Extraction"
    if row["license_type"] == "Manufacturing":
        approved = re.sub(r"[^a-z0-9]", "", category) in {
            "concentrateweight", "budflowerbulk", "budflower", "shaketrim",
            "shaketrimbystrain", "bulkgummies",
        }
        if qa == "Test Passed" and approved and re.search(r"bulk|concentrate|trim|distillate|infused\s+pre[- ]?roll", item):
            return "WIP-Manufacturing"
        if qa != "Test Passed" or re.search(r"pending testing|testing", location):
            return "Pre-WIP-Manufacturing"
        return "Needs Review"
    if row["current_facility"] == "Building 1A" and row["facility"] == "Building 1A":
        return "1A Sellable Bulk" if qa == "Test Passed" else "1A Pending Bulk Opportunity"
    if row["material_type"] in {"Trim", "Shake"}: return row["material_type"]
    approved_location = re.sub(r"[^a-z0-9]", "", location) in {
        "vaultapprovedforsale", "vaultpendingtesting", "wipquarantineroom1",
    }
    if _bulk_input(row) and approved_location:
        purchased = row["facility"] == "Building 1A"
        if qa == "Test Passed": return "WIP-Purchased 1A" if purchased else "WIP-Cultivation"
        if qa in {"Not Submitted", "Submitted for Testing", "Testing in Progress", "Unknown"}:
            return "Pre-WIP-Purchased 1A" if purchased else "Pre-WIP-Cultivation"
    return "Needs Review"


def _source_facility(row: pd.Series) -> str:
    harvest = _text(row.get("source_harvest"))
    sources = _text(row.get("source_packages"))
    tag = _text(row.get("package_tag"))
    if tag in CONFIRMED_PURCHASED_1A_TAGS or any(
        purchased in sources for purchased in CONFIRMED_PURCHASED_1A_TAGS
    ):
        return "Building 1A"
    if row.get("license_type") == "Cultivation":
        return "Building 1A" if re.search(r"^1A(?=$|[^A-Za-z0-9])", harvest, re.I) else "Building 33 (C9)"
    internal_1a = bool(re.search(r"(?:^|,\s*)1A(?=$|[^A-Za-z0-9])", harvest, re.I))
    internal_c9 = bool(re.search(r"(?:^|,\s*)(?:F|[^,]*-F)\d+(?:\.\d+)?[-.]", harvest, re.I))
    external = bool(
        re.search(r"distillate|(?:^|\s)dc(?:\s|$)", _text(row.get("item")), re.I)
        or "1a411030000b6d1" in sources.casefold()
    )
    if (external and (internal_1a or internal_c9)) or (internal_1a and internal_c9):
        return "Mixed / Blend"
    if external: return "External Operator"
    if internal_1a: return "Building 1A"
    if internal_c9: return "Building 33 (C9)"
    return "Origin Needs Review"


def normalize_active_package_files(
    files: list[tuple[str, bytes]],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Normalize the paired Metrc Active Packages exports for publication."""
    if len(files) != 2:
        raise ValueError(
            "Upload exactly two Active Packages files: one Cultivation and one Manufacturing."
        )
    frames: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    for filename, payload in files:
        license_number, license_type = _license_metadata(filename)
        source = _read_active_package_file(filename, payload)
        source.columns = [str(column).replace("\ufeff", "").strip() for column in source.columns]
        missing = [column for column in ACTIVE_PACKAGE_REQUIRED_COLUMNS if column not in source]
        if missing:
            raise ValueError(
                f"{filename}: missing required Active Packages columns: {', '.join(missing)}"
            )
        source = source.rename(columns=ACTIVE_PACKAGE_COLUMN_MAP).copy()
        source["license_number"] = license_number
        source["license_type"] = license_type
        source["source_filename"] = filename
        frames.append(source)
        sources.append({
            "filename": filename, "file_bytes": payload,
            "file_hash": hashlib.sha256(payload).hexdigest(),
            "license_number": license_number, "license_type": license_type,
        })
    if {source["license_type"] for source in sources} != {"Cultivation", "Manufacturing"}:
        raise ValueError(
            "The pair must contain one Cultivation and one Manufacturing Active Packages export."
        )
    data = pd.concat(frames, ignore_index=True, sort=False)
    defaults = {
        "source_harvest": "Unknown Harvest", "source_packages": "",
        "location": "Unknown Location", "item": "Unknown Item",
        "category": "Unknown Category", "strain": "Unassigned", "unit": "Unknown",
        "lab_status": "Unknown", "packaged_date": pd.NaT,
        "production_batch_number": "", "source_production_batch": "",
    }
    for column, default in defaults.items():
        if column not in data: data[column] = default
    text_columns = [column for column in defaults if column != "packaged_date"]
    for column in text_columns:
        data[column] = data[column].apply(lambda value, fallback=defaults[column]: _text(value, fallback))
    data["quantity"] = pd.to_numeric(data["quantity"], errors="coerce").fillna(0)
    data["packaged_date"] = data["packaged_date"].apply(_parse_date)
    data["package_tag"] = data["package_tag"].apply(_text)
    data = data[data["package_tag"].ne("")].drop_duplicates("package_tag", keep="last")
    clone_room = data["license_type"].eq("Cultivation") & data["location"].str.contains("clone room", case=False, na=False)
    data = data[~clone_room].copy()
    data["metrc_strain"] = data["strain"]
    data["strain"] = data["strain"].apply(normalize_strain_name)
    data["source_strain"] = data["strain"]
    packaged_signal = data.apply(_finished_retail, axis=1)
    marketed = data["item"].apply(infer_strain)
    use_market_name = (
        (packaged_signal | data["license_type"].eq("Manufacturing"))
        & marketed.ne("") & ~marketed.isin(["Unassigned", "Unknown Item"])
    )
    data.loc[use_market_name, "strain"] = marketed[use_market_name]
    data["harvest_date"] = data["source_harvest"].apply(_harvest_date)
    production = data.apply(_production_date, axis=1)
    data["production_date"] = [result[0] for result in production]
    data["production_date_source"] = [
        result[1] if license_type == "Manufacturing" else "Not Applicable"
        for result, license_type in zip(production, data["license_type"])
    ]
    data["qa_status"] = data["lab_status"].apply(_qa_status)
    data["facility"] = data.apply(_source_facility, axis=1)
    data["current_facility"] = data["location"].apply(
        lambda value: "Building 1A" if _text(value).upper().startswith("1A") else "Building 33 (C9)"
    )
    data["ownership_status"] = data.apply(
        lambda row: "Partner-Owned / Compliance Managed"
        if row["current_facility"] == "Building 1A"
        else "QCC-Owned / Purchased from Building 1A"
        if row["facility"] == "Building 1A"
        else "QCC-Owned / External or Blend"
        if row["facility"] in {"External Operator", "Mixed / Blend"}
        else "QCC-Owned / Clade9 Origin",
        axis=1,
    )
    data["source_license_number"] = data["license_number"]
    data["source_license_type"] = data["license_type"]
    data["qcc_owned"] = ~data["ownership_status"].eq("Partner-Owned / Compliance Managed")
    data["material_type"] = data.apply(_material_type, axis=1)
    data["calculated_weight_grams"] = data.apply(_weight_grams, axis=1)
    data["is_finished_retail_sku"] = packaged_signal
    data["production_stage"] = data.apply(_production_stage, axis=1)
    data["is_retention_sample"] = data["production_stage"].eq("Retention Storage")
    data["include_in_cpg"] = data["is_finished_retail_sku"] & data["production_stage"].isin(["Packaged Goods", "Retention Storage"])
    data["item_category"] = data["category"]
    data["sku_type"] = data.apply(classify_sku_type, axis=1)
    data.loc[~data["include_in_cpg"], "sku_type"] = "Not Packaged SKU"
    data["brand"] = data.apply(
        lambda row: infer_brand(row["item"], row["strain"], row["sku_type"]), axis=1
    )
    today = pd.Timestamp(date.today())
    cultivation = data["license_type"].eq("Cultivation")
    data["aging_start_date"] = data["production_date"]
    data.loc[cultivation, "aging_start_date"] = data.loc[cultivation, "harvest_date"]
    data["inventory_age_days"] = (
        today - pd.to_datetime(data["aging_start_date"], errors="coerce")
    ).dt.days
    data["aging_policy"] = "No Expiration Until Converted"
    data.loc[cultivation, "aging_policy"] = "Cultivation - 225 Days From Harvest"
    finished_manufacturing = data["license_type"].eq("Manufacturing") & data[
        "production_stage"
    ].isin(["Packaged Goods", "Failed - On Hold"])
    data.loc[finished_manufacturing, "aging_policy"] = (
        "Manufactured Finished Good - 180 Days"
    )
    windows = pd.Series(pd.NA, index=data.index, dtype="Float64")
    windows.loc[cultivation] = 225
    windows.loc[finished_manufacturing] = 180
    data["days_remaining_in_sale_window"] = windows - data["inventory_age_days"]
    data["expiration_date"] = pd.to_datetime(
        data["aging_start_date"], errors="coerce"
    ) + pd.to_timedelta(windows, unit="D")
    data["planning_allocation"] = data.apply(
        lambda row: row["brand"]
        if row["production_stage"] == "Packaged Goods"
        else "ROFR / Not Purchased"
        if not row["qcc_owned"]
        else "Unallocated QCC Brand"
        if row["facility"] == "Building 1A"
        else "Clade9 / Internal Supply",
        axis=1,
    )
    data["commercial_status"] = data["production_stage"].map({
        "Packaged Goods": "Approved for Sale",
        "1A Sellable Bulk": "ROFR Eligible - Not QCC Owned",
        "Failed - On Hold": "Failed - Not Saleable",
    }).fillna("Internal Inventory")
    data["business_area"] = data["production_stage"].apply(
        lambda stage: "Finished Goods"
        if stage == "Packaged Goods"
        else "Manufacturing"
        if "WIP" in stage or "Bulk" in stage
        else "Cultivation"
    )
    data["commercial_intent"] = data["production_stage"].apply(
        lambda stage: "Sale"
        if stage == "Packaged Goods"
        else "Pending Testing or Production"
        if "Pre-WIP" in stage
        else "Finished Goods Production"
        if "WIP" in stage
        else "Undecided"
    )
    data["review_reason"] = data.apply(
        lambda row: "Production stage needs review"
        if row["production_stage"] == "Needs Review"
        else "Brand needs review"
        if row["brand"] == "Brand Needs Review" and row["include_in_cpg"]
        else "",
        axis=1,
    )
    data["needs_review"] = data["review_reason"].ne("")
    data["classification_rule_version"] = INVENTORY_RULE_VERSION
    for column in ACTIVE_PACKAGE_COLUMNS:
        if column not in data:
            data[column] = ""
    return data, sources


def publish_active_package_snapshot(
    files: list[tuple[str, bytes]], *, imported_by: str = ""
) -> dict[str, Any]:
    """Publish paired Active Packages files into the durable snapshot contract."""
    url = _require_database()
    data, sources = normalize_active_package_files(files)
    source_hash = hashlib.sha256(
        "|".join(sorted(row["file_hash"] for row in sources)).encode()
    ).hexdigest()
    filename = ", ".join(row["filename"] for row in sources)
    with psycopg.connect(url, connect_timeout=20) as connection:
        prior = connection.execute(
            "SELECT package_count FROM inventory_snapshots WHERE source_hash = %s "
            "AND status = 'Published' ORDER BY published_at DESC LIMIT 1",
            (source_hash,),
        ).fetchone()
        if prior:
            return {
                "File": filename, "Status": "Already Imported",
                "Source Rows": len(data), "Stored Rows": int(prior[0]),
                "Inserted": 0, "Updated": 0,
                "Details": "These files already match a published inventory snapshot.",
            }
        now = datetime.now(EASTERN)
        snapshot_id = hashlib.sha256(
            f"{source_hash}|{now.isoformat()}".encode()
        ).hexdigest()
        cpg = data[
            data["qcc_owned"]
            & data["include_in_cpg"]
            & data["production_stage"].eq("Packaged Goods")
        ].copy()
        sku_columns = [
            "sku_key", "source_license_number", "source_license_type", "brand",
            "strain", "sku_type", "on_hand_units", "package_count",
        ]
        if cpg.empty:
            sku = pd.DataFrame(columns=sku_columns)
        else:
            sku = cpg.groupby(
                ["license_number", "license_type", "brand", "strain", "sku_type"],
                dropna=False,
            ).agg(
                on_hand_units=("quantity", "sum"),
                package_count=("package_tag", "nunique"),
            ).reset_index()
            sku["sku_key"] = sku.apply(
                lambda row: "|".join(
                    _text(row[key]).casefold()
                    for key in ("license_number", "brand", "strain", "sku_type")
                ),
                axis=1,
            )
            sku = sku.rename(columns={
                "license_number": "source_license_number",
                "license_type": "source_license_type",
            })[sku_columns]
        connection.execute(
            "INSERT INTO inventory_snapshots (snapshot_id, business_date, "
            "published_at, published_by, package_count, sku_count, source_hash, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'Published')",
            (
                snapshot_id, now.date().isoformat(), now.isoformat(),
                imported_by or "QCC Reflex User", len(data), len(sku), source_hash,
            ),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO inventory_snapshot_files (snapshot_id, filename, "
                "license_type, file_hash, file_bytes) VALUES (%s,%s,%s,%s,%s)",
                [
                    (snapshot_id, row["filename"], row["license_type"], row["file_hash"], row["file_bytes"])
                    for row in sources
                ],
            )
            if not sku.empty:
                cursor.executemany(
                    "INSERT INTO inventory_snapshot_skus (snapshot_id, sku_key, "
                    "source_license_number, source_license_type, brand, strain, "
                    "sku_type, on_hand_units, package_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (snapshot_id, *(_sql_value(value) for value in row))
                        for row in sku.itertuples(index=False, name=None)
                    ],
                )
            placeholders = ",".join("%s" for _ in range(len(ACTIVE_PACKAGE_COLUMNS) + 1))
            cursor.executemany(
                "INSERT INTO inventory_snapshot_packages (snapshot_id,"
                + ",".join(ACTIVE_PACKAGE_COLUMNS)
                + ") VALUES (" + placeholders + ")",
                [
                    (snapshot_id, *(_sql_value(value) for value in row))
                    for row in data[ACTIVE_PACKAGE_COLUMNS].itertuples(index=False, name=None)
                ],
            )
        connection.commit()
    details = f"Published {len(data):,} packages and {len(sku):,} SKU summaries."
    result = {
        "File": filename, "Status": "Imported", "Source Rows": len(data),
        "Stored Rows": len(data), "Inserted": len(data), "Updated": 0,
        "Details": details,
    }
    record_metrc_import_run(
        source_type="Active Packages", adapter="Metrc File Upload",
        filename=filename, file_hash=source_hash, source_rows=len(data),
        stored_rows=len(data), inserted_rows=len(data), updated_rows=0,
        status="Imported", details=details, imported_by=imported_by,
    )
    _invalidate_dashboard_caches()
    return result


def load_metrc_import_status() -> list[dict[str, str]]:
    """Return the newest publication/import timestamp for each Metrc data family."""
    url = _require_database()
    queries = [
        ("Active Packages", "inventory_snapshots", "published_at", "status = 'Published'"),
        ("Transfer History", "transfer_import_log", "imported_at", "TRUE"),
        ("Lab Results", "lab_import_log", "imported_at", "TRUE"),
        ("Plant & Harvest Data", "qcc_metrc_plant_snapshots", "imported_at", "TRUE"),
    ]
    rows: list[dict[str, str]] = []
    with psycopg.connect(url, connect_timeout=20) as connection:
        for source_type, table, timestamp, where_clause in queries:
            try:
                row = connection.execute(
                    f"SELECT {timestamp} FROM {table} WHERE {where_clause} "
                    f"ORDER BY {timestamp} DESC LIMIT 1"
                ).fetchone()
                latest = str(row[0]) if row and row[0] else "No import found"
            except psycopg.errors.UndefinedTable:
                connection.rollback()
                latest = "No import found"
            rows.append({"Data Set": source_type, "Latest Import": latest})
    return rows


def load_metrc_import_history(limit: int = 25) -> list[dict[str, Any]]:
    """Load recent tenant-owned import runs across every migrated data family."""
    url = _require_database()
    safe_limit = max(1, min(int(limit or 25), 100))
    with psycopg.connect(url, connect_timeout=20) as connection:
        _ensure_import_audit(connection)
        rows = connection.execute(
            """
            SELECT source_type, filename, status, source_rows, stored_rows,
                   inserted_rows, updated_rows, imported_by, imported_at, details
            FROM qcc_metrc_import_runs
            WHERE tenant_id = %s
            ORDER BY imported_at DESC
            LIMIT %s
            """,
            (TENANT_ID, safe_limit),
        ).fetchall()
        connection.commit()
    return [
        {
            "Data Set": row[0], "File": row[1], "Status": row[2],
            "Source Rows": int(row[3] or 0), "Stored Rows": int(row[4] or 0),
            "Inserted": int(row[5] or 0), "Updated": int(row[6] or 0),
            "Imported By": row[7] or "—", "Imported At": str(row[8] or "—"),
            "Details": row[9] or "",
        }
        for row in rows
    ]
