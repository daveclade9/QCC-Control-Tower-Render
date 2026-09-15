"""Shared Metrc file-ingestion adapters for the Reflex administration workspace.

The adapters in this module are UI-independent.  A future Metrc API client can
feed the same normalize/store functions without changing downstream modules.
"""

from __future__ import annotations

import hashlib
import io
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

try:
    import psycopg
except ImportError:  # pragma: no cover - demo builds can compile without DB extras.
    psycopg = None

from .data import _invalidate_dashboard_caches, database_url


TENANT_ID = os.getenv("QCC_TENANT_ID", "qcc").strip() or "qcc"

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
