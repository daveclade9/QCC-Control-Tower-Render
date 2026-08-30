"""Shared Supabase access and dashboard summaries for the Reflex pilot."""

from __future__ import annotations

import os
import gzip
import hashlib
import io
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # Allows demo-mode validation before dependencies install.
    def load_dotenv() -> bool:
        return False

try:
    import psycopg
except ImportError:  # Allows demo-mode validation before dependencies install.
    psycopg = None

from .rules import (
    PRODUCT_LIFECYCLE_OVERRIDES,
    RETIRED_OR_ON_HOLD_STRAINS,
    classify_sku_type,
    gummy_variant,
    infer_brand,
    infer_strain,
    normalize_strain_name,
    prepare_transfer_analysis,
)
from .retailer_directory import CLADE9_LOCATIONS
from .zebra_labels import expiration_from_harvest, extract_metrc_tags
from .availability_demand import build_availability_demand_analysis


load_dotenv()

OPERATIONAL_CACHE_SECONDS = 1800
SALES_CACHE_SECONDS = 1800
QA_CACHE_SECONDS = 3600
_DASHBOARD_CACHE: dict[str, Any] = {"loaded_at": 0.0, "payload": None}
_DASHBOARD_CACHE_LOCK = threading.Lock()
_SALES_DASHBOARD_CACHE: dict[str, Any] = {"loaded_at": 0.0, "payload": None}
_SALES_DASHBOARD_CACHE_LOCK = threading.Lock()
_DISTRIBUTION_CACHE: dict[str, Any] = {
    "loaded_at": 0.0,
    "transfers": None,
    "exceptions": None,
    "exception_packages": None,
}
_DISTRIBUTION_CACHE_LOCK = threading.Lock()
_OPERATIONAL_CONTEXT: dict[str, Any] = {"loaded_at": 0.0, "payload": None}
_OPERATIONAL_CONTEXT_LOCK = threading.Lock()
_OPERATIONAL_BUILD_LOCK = threading.Lock()
_RETAILER_LOCATION_INIT_LOCK = threading.Lock()
_RETAILER_LOCATION_INITIALIZED = False
_RETAILER_LOCATION_CACHE: dict[str, Any] = {"loaded_at": 0.0, "rows": None}
_RETAILER_LOCATION_CACHE_LOCK = threading.Lock()
_PRODUCTION_SCHEMA_LOCK = threading.Lock()
_PRODUCTION_SCHEMA_READY = False

PRODUCTION_LINE_OPTIONS = [
    "Flower Line 1",
    "Flower Line 2",
    "Manufacturing Line 1",
    "Manufacturing Line 2",
    "Flex Line 3",
]
PRODUCTION_LINE_STYLES = {
    "Flower Line 1": ("#166534", "#dcfce7"),
    "Flower Line 2": ("#1d4ed8", "#dbeafe"),
    "Manufacturing Line 1": ("#9a3412", "#ffedd5"),
    "Manufacturing Line 2": ("#6b21a8", "#f3e8ff"),
    "Flex Line 3": ("#a16207", "#fef9c3"),
    "Unassigned": ("#475569", "#e2e8f0"),
}
PRODUCTION_LINE_ALIASES = {
    "Pre-Roll Line 3": "Manufacturing Line 1",
    "Pre-Roll Line 4": "Manufacturing Line 2",
}


def normalized_production_line(value: Any) -> str:
    line = str(value or "Unassigned").strip() or "Unassigned"
    return PRODUCTION_LINE_ALIASES.get(line, line)


def _invalidate_dashboard_caches() -> None:
    for lock, cache in (
        (_DASHBOARD_CACHE_LOCK, _DASHBOARD_CACHE),
        (_SALES_DASHBOARD_CACHE_LOCK, _SALES_DASHBOARD_CACHE),
        (_OPERATIONAL_CONTEXT_LOCK, _OPERATIONAL_CONTEXT),
    ):
        with lock:
            cache["loaded_at"] = 0.0
            cache["payload"] = None
    with _DISTRIBUTION_CACHE_LOCK:
        _DISTRIBUTION_CACHE.update({
            "loaded_at": 0.0,
            "transfers": None,
            "exceptions": None,
            "exception_packages": None,
        })


def _remove_deleted_plans_from_caches(plan_ids: list[str]) -> None:
    """Remove deleted plans without discarding the warm inventory context."""
    deleted = {str(plan_id) for plan_id in plan_ids if str(plan_id)}
    if not deleted:
        return

    # The operational context contains the expensive inventory snapshot plus
    # three small production frames. Replace only the production frames so the
    # post-delete Sales/WIP refresh can reuse the already-loaded inventory.
    with _OPERATIONAL_CONTEXT_LOCK:
        payload = _OPERATIONAL_CONTEXT.get("payload")
        if payload:
            updated = dict(payload)
            for key in ("plans", "outputs", "sources"):
                frame = payload.get(key)
                if (
                    isinstance(frame, pd.DataFrame)
                    and not frame.empty
                    and "plan_id" in frame.columns
                ):
                    updated[key] = frame[
                        ~frame["plan_id"].astype(str).isin(deleted)
                    ].copy()
            _OPERATIONAL_CONTEXT["payload"] = updated
            _OPERATIONAL_CONTEXT["loaded_at"] = time.monotonic()

    # These compact derived payloads include saved-plan and committed-WIP
    # values. Expire them, but leave the operational inventory context warm.
    for lock, cache in (
        (_DASHBOARD_CACHE_LOCK, _DASHBOARD_CACHE),
        (_SALES_DASHBOARD_CACHE_LOCK, _SALES_DASHBOARD_CACHE),
    ):
        with lock:
            cache["loaded_at"] = 0.0
            cache["payload"] = None

LAB_REQUIRED_COLUMNS = [
    "Lab License No.", "Lab Facility", "Packaged Lic. No.",
    "Packaged Facility", "Package", "Source Harvest Names",
    "Source Package Labels", "Item", "Category", "Lab Testing",
    "Test Date", "Overall", "Test Name", "Test Passed", "Result",
    "LTE Date", "Date", "Notes",
]
LAB_COLUMN_MAP = {
    "Lab License No.": "lab_license",
    "Lab Facility": "lab_facility",
    "Packaged Lic. No.": "packaged_license",
    "Packaged Facility": "packaged_facility",
    "Package": "package_tag",
    "Source Harvest Names": "source_harvest_names",
    "Source Package Labels": "source_package_labels",
    "Item": "item",
    "Category": "category",
    "Lab Testing": "lab_testing_status",
    "Test Date": "test_date",
    "Overall": "overall_pass",
    "Test Name": "test_name",
    "Test Passed": "test_passed",
    "Result": "result",
    "LTE Date": "lte_date",
    "Date": "record_date",
    "Notes": "notes",
}
LAB_DB_COLUMNS = [
    "record_key", "lab_license", "lab_facility", "packaged_license",
    "packaged_facility", "package_tag", "source_harvest_names",
    "source_package_labels", "item", "category", "lab_testing_status",
    "test_date", "overall_pass", "test_name", "test_passed", "result",
    "lte_date", "record_date", "notes", "source_filename",
    "source_file_hash", "imported_at",
]
QA_LABEL_FIELDS = {
    "package_tag": "Package Tag",
    "brand": "Brand",
    "sku_type": "SKU Type",
    "source_harvest_names": "Source Harvest",
    "source_package_labels": "Source Package(s)",
    "item": "Item",
    "strain": "Strain",
    "qa_test_type": "Test Type",
    "packaged_license": "Facility License",
    "packaged_facility": "Facility",
    "lab_testing_status": "QA Status",
    "test_date": "Test Date",
    "expiration_date": "Expiration Date",
    "total_thc": "Total THC",
    "total_terpenes": "Total Terpenes",
    "lab_facility": "Testing Laboratory",
    "category": "Category",
    "location": "Location",
    "record_origin": "Record Source",
}
DEFAULT_QA_LABEL_CONFIG = {
    "template_name": "QCC QA Summary",
    "scope": "Both",
    "brand_filter": "All Brands",
    "sku_filter": "All SKU Types",
    "label_size": "4 x 6",
    "title": "QCC QA / Compliance Summary",
    "footer": "Verify current package status in Metrc before release.",
    "fields": list(QA_LABEL_FIELDS),
}

_QA_CACHE: dict[str, Any] = {"loaded_at": 0.0, "payload": None}
_QA_CACHE_LOCK = threading.Lock()

TRANSFER_COLUMNS = [
    "record_key", "manifest", "invoice_number", "origin_license",
    "origin_facility", "origin_facility_type", "destination_license",
    "destination_facility", "destination_facility_type", "transfer_type",
    "created_at", "created_by_user", "received_at", "received_by_user",
    "voided", "package_tag", "state", "item", "item_category",
    "shipper_dollar_amount", "receiver_dollar_amount", "actual_shipped",
    "actual_shipped_uom", "actual_received", "actual_received_uom",
    "count_shipped", "count_shipped_uom", "count_received",
    "count_received_uom", "unit_weight_grams",
]
SALES_TRANSFER_COLUMNS = [
    "manifest", "invoice_number", "origin_facility",
    "destination_license", "destination_facility",
    "destination_facility_type", "transfer_type", "created_at",
    "received_at", "voided", "package_tag", "state", "item",
    "item_category", "shipper_dollar_amount", "actual_shipped",
    "actual_shipped_uom", "count_shipped", "unit_weight_grams",
]


def database_url() -> str:
    url = (
        os.getenv("QCC_SUPABASE_DATABASE_URL", "").strip()
        or os.getenv("SUPABASE_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )
    if any(marker in url for marker in ("PROJECT_REF", "URL_ENCODED_PASSWORD", "POOLER_HOST")):
        return ""
    return url


def connection_status_label() -> str:
    return "Supabase configured" if database_url() else "Configuration required"


def query_frame(
    query: str,
    parameters: tuple[Any, ...] = (),
    statement_timeout_seconds: int = 30,
) -> pd.DataFrame:
    """Execute one backend-only read and return a DataFrame."""
    url = database_url()
    if not url:
        raise RuntimeError(
            "Add QCC_SUPABASE_DATABASE_URL to the pilot .env file."
        )
    if psycopg is None:
        raise RuntimeError(
            "Install the pilot requirements before connecting to Supabase."
        )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with psycopg.connect(url, connect_timeout=15) as connection:
                with connection.cursor() as cursor:
                    timeout_seconds = max(int(statement_timeout_seconds), 1)
                    cursor.execute(
                        f"SET LOCAL statement_timeout = '{timeout_seconds}s'"
                    )
                    cursor.execute(query, parameters)
                    if not cursor.description:
                        return pd.DataFrame()
                    columns = [column.name for column in cursor.description]
                    return pd.DataFrame(cursor.fetchall(), columns=columns)
        except Exception as error:
            last_error = error
            text = str(error).lower()
            transient = (
                isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError))
                or any(marker in text for marker in (
                    "ssl connection has been closed", "consuming input failed",
                    "server closed the connection", "connection reset",
                    "connection timed out", "terminating connection",
                ))
            )
            if not transient or attempt:
                raise
            time.sleep(0.35)
    raise last_error or RuntimeError("Supabase query failed.")


def streamed_query_frame(
    query: str, parameters: tuple[Any, ...] = (), batch_size: int = 2500
) -> pd.DataFrame:
    """Stream a large read in bounded batches instead of one libpq result."""
    url = database_url()
    if not url or psycopg is None:
        raise RuntimeError("Supabase database access is not configured.")
    frames: list[pd.DataFrame] = []
    with psycopg.connect(url, connect_timeout=15) as connection:
        connection.execute("SET LOCAL statement_timeout = '120s'")
        with connection.cursor(name="qcc_sales_transfer_stream") as cursor:
            cursor.execute(query, parameters)
            columns = [column.name for column in cursor.description or []]
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                frames.append(pd.DataFrame.from_records(rows, columns=columns))
    return (
        pd.concat(frames, ignore_index=True)
        if frames else pd.DataFrame(columns=columns)
    )


def safe_query_frame(
    query: str,
    parameters: tuple[Any, ...] = (),
    statement_timeout_seconds: int = 30,
) -> pd.DataFrame:
    """Allow pilot sections to remain available when an optional table is absent."""
    try:
        return query_frame(query, parameters, statement_timeout_seconds)
    except Exception as error:
        if psycopg is not None and isinstance(
            error, psycopg.errors.UndefinedTable
        ):
            return pd.DataFrame()
        raise


def _directory_retailer_location_rows() -> list[dict[str, Any]]:
    """Convert the bundled Clade9 directory into the shared location schema."""
    return [
        {
            "location_id": f"clade9:{row.get('source_id', index)}",
            "destination_license": "",
            "metrc_business_name": "",
            "public_store_name": str(row.get("name", "")).strip(),
            "street_address": str(row.get("address", "")).strip(),
            "locality": str(row.get("locality", "")).strip(),
            "phone": str(row.get("phone", "")).strip(),
            "website": str(row.get("website", "")).strip(),
            "latitude": None,
            "longitude": None,
            "location_status": "Directory Seed",
            "verified": False,
            "notes": "",
            "source": "Clade9 Store Locator",
            "source_id": str(row.get("source_id", "")).strip(),
        }
        for index, row in enumerate(CLADE9_LOCATIONS, start=1)
    ]


def _initialize_retailer_locations_database_once() -> None:
    """Create and safely seed the persistent retailer-location directory."""
    url = database_url()
    if not url or psycopg is None:
        raise RuntimeError("Supabase is required for the retailer directory.")
    with psycopg.connect(url, connect_timeout=15) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS qcc_retailer_locations (
                location_id TEXT PRIMARY KEY,
                destination_license TEXT,
                metrc_business_name TEXT NOT NULL DEFAULT '',
                public_store_name TEXT NOT NULL DEFAULT '',
                street_address TEXT NOT NULL DEFAULT '',
                locality TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '',
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                location_status TEXT NOT NULL DEFAULT 'Not Reviewed',
                verified BOOLEAN NOT NULL DEFAULT FALSE,
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_qcc_retailer_locations_license "
            "ON qcc_retailer_locations(destination_license) "
            "WHERE destination_license IS NOT NULL "
            "AND BTRIM(destination_license) <> ''"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_qcc_retailer_locations_name "
            "ON qcc_retailer_locations(public_store_name)"
        )
        seed_rows = _directory_retailer_location_rows()
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO qcc_retailer_locations (
                    location_id, destination_license, metrc_business_name,
                    public_store_name, street_address, locality, phone,
                    website, latitude, longitude, location_status, verified,
                    notes, source, source_id
                ) VALUES (
                    %(location_id)s, NULLIF(%(destination_license)s, ''),
                    %(metrc_business_name)s, %(public_store_name)s,
                    %(street_address)s, %(locality)s, %(phone)s, %(website)s,
                    %(latitude)s, %(longitude)s, %(location_status)s,
                    %(verified)s, %(notes)s, %(source)s, %(source_id)s
                )
                ON CONFLICT(location_id) DO NOTHING
                """,
                seed_rows,
            )
        connection.commit()


def initialize_retailer_locations_database() -> None:
    """Initialize the shared location table once per application process."""
    global _RETAILER_LOCATION_INITIALIZED
    if _RETAILER_LOCATION_INITIALIZED:
        return
    with _RETAILER_LOCATION_INIT_LOCK:
        if _RETAILER_LOCATION_INITIALIZED:
            return
        _initialize_retailer_locations_database_once()
        _RETAILER_LOCATION_INITIALIZED = True


def load_retailer_locations(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Load the small shared retailer directory without delaying core Sales."""
    now = time.monotonic()
    with _RETAILER_LOCATION_CACHE_LOCK:
        cached = _RETAILER_LOCATION_CACHE.get("rows")
        age = now - float(_RETAILER_LOCATION_CACHE.get("loaded_at", 0.0))
        if cached is not None and not force_refresh and age < SALES_CACHE_SECONDS:
            return cached
    try:
        initialize_retailer_locations_database()
        frame = query_frame(
            "SELECT location_id, COALESCE(destination_license, '') AS "
            "destination_license, metrc_business_name, public_store_name, "
            "street_address, locality, phone, website, latitude, longitude, "
            "location_status, verified, notes, source, source_id, updated_at "
            "FROM qcc_retailer_locations ORDER BY public_store_name",
            statement_timeout_seconds=15,
        )
        rows = record_list(frame)
    except Exception:
        # Retail mapping is optional. The bundled directory keeps the Sales
        # workspace available even if this new table cannot be created yet.
        rows = _directory_retailer_location_rows()
    with _RETAILER_LOCATION_CACHE_LOCK:
        _RETAILER_LOCATION_CACHE["rows"] = rows
        _RETAILER_LOCATION_CACHE["loaded_at"] = time.monotonic()
    return rows


def query_frames(
    statements: list[tuple[str, tuple[Any, ...]]],
    statement_timeout_seconds: int = 30,
) -> list[pd.DataFrame]:
    """Execute several related reads through one Supabase connection."""
    url = database_url()
    if not url or psycopg is None:
        raise RuntimeError("Supabase database access is not configured.")
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            frames: list[pd.DataFrame] = []
            with psycopg.connect(url, connect_timeout=15) as connection:
                with connection.cursor() as cursor:
                    timeout_seconds = max(int(statement_timeout_seconds), 1)
                    cursor.execute(
                        f"SET LOCAL statement_timeout = '{timeout_seconds}s'"
                    )
                    for query, parameters in statements:
                        cursor.execute(query, parameters)
                        if not cursor.description:
                            frames.append(pd.DataFrame())
                            continue
                        columns = [column.name for column in cursor.description]
                        frames.append(pd.DataFrame(cursor.fetchall(), columns=columns))
            return frames
        except Exception as error:
            last_error = error
            text = str(error).lower()
            transient = (
                isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError))
                or any(marker in text for marker in (
                    "ssl connection has been closed", "consuming input failed",
                    "server closed the connection", "connection reset",
                    "connection timed out", "terminating connection",
                ))
            )
            if not transient or attempt:
                raise
            time.sleep(0.35)
    raise last_error or RuntimeError("Supabase query group failed.")


SALES_SNAPSHOT_SCHEMA_VERSION = "qcc-sales-v1"
SALES_ANALYSIS_COLUMNS = [
    "manifest", "invoice_number", "created_at", "received_at", "state",
    "destination_license", "destination_facility", "package_tag", "item",
    "item_key", "brand", "strain", "sku_type", "shipped_units",
    "shipper_dollar_amount", "is_demand", "is_open_shipment",
    "is_shipment_exception", "brand_attribution_reason",
]


def empty_sales_analysis() -> pd.DataFrame:
    """Return an empty but schema-complete Sales frame."""
    return pd.DataFrame(columns=list(dict.fromkeys(SALES_ANALYSIS_COLUMNS)))


def load_published_sales_snapshot() -> tuple[dict[str, Any], pd.DataFrame]:
    """Read and expand the latest compact Sales snapshot from Supabase."""
    snapshots = safe_query_frame(
        "SELECT snapshot_id, schema_version, source_row_count, "
        "source_latest_transfer, payload_gzip, published_at, published_by "
        "FROM reflex_sales_snapshots ORDER BY published_at DESC LIMIT 1",
        statement_timeout_seconds=90,
    )
    if snapshots.empty:
        return {}, empty_sales_analysis()

    metadata = snapshots.iloc[0].drop(labels=["payload_gzip"]).to_dict()
    if str(metadata.get("schema_version", "")) != SALES_SNAPSHOT_SCHEMA_VERSION:
        raise RuntimeError(
            "The published Sales snapshot uses an unsupported schema. "
            "Publish it again from Streamlit 81.5 or newer."
        )
    compressed = snapshots.iloc[0].get("payload_gzip")
    if isinstance(compressed, memoryview):
        compressed = compressed.tobytes()
    payload = json.loads(gzip.decompress(bytes(compressed)).decode("utf-8"))
    if payload.get("schema_version") != SALES_SNAPSHOT_SCHEMA_VERSION:
        raise RuntimeError("The published Sales snapshot payload is invalid.")
    analysis = pd.DataFrame(
        payload.get("records", []),
        columns=payload.get("columns") or None,
    )
    for column in ["created_at", "received_at", "imported_at"]:
        if column in analysis:
            analysis[column] = pd.to_datetime(analysis[column], errors="coerce")
    analysis["shipment_month"] = analysis["created_at"].dt.strftime("%Y-%m")
    analysis["transit_hours"] = (
        analysis["received_at"] - analysis["created_at"]
    ).dt.total_seconds() / 3600
    for column in [
        "shipper_dollar_amount", "receiver_dollar_amount", "actual_shipped",
        "actual_received", "count_shipped", "count_received",
        "unit_weight_grams", "planning_unit_weight_grams", "shipped_units",
        "transit_hours",
    ]:
        if column in analysis:
            analysis[column] = pd.to_numeric(analysis[column], errors="coerce")
    for column in [
        "is_finished_cpg", "is_demand", "is_open_shipment",
        "is_shipment_exception",
    ]:
        if column in analysis:
            analysis[column] = analysis[column].fillna(False).astype(bool)
    missing = [column for column in SALES_ANALYSIS_COLUMNS if column not in analysis]
    if missing:
        raise RuntimeError(
            "The published Sales snapshot is missing required fields: "
            + ", ".join(missing)
        )
    return metadata, analysis


def _initialize_qa_database_once() -> None:
    """Create the shared QA tables used by Streamlit and Reflex."""
    url = database_url()
    if not url or psycopg is None:
        raise RuntimeError("Supabase is required for the QA workspace.")
    definitions = []
    for column in LAB_DB_COLUMNS:
        if column == "record_key":
            definitions.append("record_key TEXT PRIMARY KEY")
        elif column in {"overall_pass", "test_passed"}:
            definitions.append(f"{column} INTEGER")
        elif column == "result":
            definitions.append("result DOUBLE PRECISION")
        else:
            definitions.append(f"{column} TEXT")
    with psycopg.connect(url, connect_timeout=15) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lab_result_records ("
            + ", ".join(definitions) + ")"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS lab_import_log (
                source_file_hash TEXT PRIMARY KEY,
                source_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                source_rows INTEGER NOT NULL,
                stored_rows INTEGER NOT NULL,
                inserted_rows INTEGER NOT NULL,
                updated_rows INTEGER NOT NULL,
                test_min TEXT,
                test_max TEXT,
                imported_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS qa_label_templates (
                template_id TEXT PRIMARY KEY,
                template_name TEXT NOT NULL,
                scope TEXT NOT NULL,
                brand_filter TEXT NOT NULL DEFAULT 'All Brands',
                sku_filter TEXT NOT NULL DEFAULT 'All SKU Types',
                label_size TEXT NOT NULL,
                title TEXT NOT NULL,
                footer TEXT,
                fields_json TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS qa_label_print_log (
                print_id TEXT PRIMARY KEY,
                package_tag TEXT NOT NULL,
                source_harvest TEXT,
                template_id TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                output_type TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                printed_at TEXT NOT NULL,
                printed_by TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS qa_adjusted_coas (
                coa_key TEXT PRIMARY KEY,
                package_tag TEXT NOT NULL,
                packaged_license TEXT NOT NULL DEFAULT '',
                test_date TEXT,
                total_terpenes DOUBLE PRECISION NOT NULL,
                total_cbg DOUBLE PRECISION NOT NULL,
                terpene_names_json TEXT NOT NULL,
                terpene_values_json TEXT NOT NULL,
                suspect_flags_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                updated_by TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lab_package "
            "ON lab_result_records(package_tag)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lab_test_date "
            "ON lab_result_records(test_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lab_package_latest "
            "ON lab_result_records(packaged_license, package_tag, test_date DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lab_test_name "
            "ON lab_result_records(test_name)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_adjusted_coa_package "
            "ON qa_adjusted_coas(package_tag, packaged_license)"
        )
        now = datetime.now().astimezone().isoformat()
        connection.execute(
            """
            INSERT INTO qa_label_templates (
                template_id, template_name, scope, brand_filter, sku_filter,
                label_size, title, footer, fields_json, is_active, version,
                updated_at, updated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1, %s, %s)
            ON CONFLICT(template_id) DO NOTHING
            """,
            (
                "qcc-default-qa-summary",
                DEFAULT_QA_LABEL_CONFIG["template_name"],
                DEFAULT_QA_LABEL_CONFIG["scope"],
                DEFAULT_QA_LABEL_CONFIG["brand_filter"],
                DEFAULT_QA_LABEL_CONFIG["sku_filter"],
                DEFAULT_QA_LABEL_CONFIG["label_size"],
                DEFAULT_QA_LABEL_CONFIG["title"],
                DEFAULT_QA_LABEL_CONFIG["footer"],
                json.dumps(DEFAULT_QA_LABEL_CONFIG["fields"]),
                now,
                "QCC Control Tower",
            ),
        )
        connection.commit()


def initialize_qa_database() -> None:
    """Create QA tables, retrying once after an idle SSL connection failure."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            _initialize_qa_database_once()
            return
        except Exception as error:
            last_error = error
            text = str(error).lower()
            transient = (
                psycopg is not None
                and (
                    isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError))
                    or any(marker in text for marker in (
                        "ssl connection has been closed", "consuming input failed",
                        "server closed the connection", "connection reset",
                        "connection timed out", "terminating connection",
                    ))
                )
            )
            if not transient or attempt:
                raise
            time.sleep(0.35)
    raise last_error or RuntimeError("Supabase QA initialization failed.")


def _lab_boolean(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    return int(str(value or "").strip().lower() in {
        "1", "true", "yes", "y", "passed", "pass",
    })


def _sql_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def normalize_lab_results(
    source_data: pd.DataFrame, filename: str, file_hash: str
) -> pd.DataFrame:
    missing = [name for name in LAB_REQUIRED_COLUMNS if name not in source_data]
    if missing:
        raise ValueError("Missing required lab columns: " + ", ".join(missing))
    data = source_data.rename(columns=LAB_COLUMN_MAP).copy()
    text_columns = [
        column for column in LAB_DB_COLUMNS
        if column not in {
            "record_key", "overall_pass", "test_passed", "result",
            "source_filename", "source_file_hash", "imported_at",
        }
    ]
    for column in text_columns:
        data[column] = data[column].fillna("").astype(str).str.strip()
    data["overall_pass"] = data["overall_pass"].map(_lab_boolean)
    data["test_passed"] = data["test_passed"].map(_lab_boolean)
    data["result"] = pd.to_numeric(data["result"], errors="coerce")
    for column in ["test_date", "lte_date", "record_date"]:
        parsed = pd.to_datetime(data[column], errors="coerce")
        data[column] = parsed.dt.strftime("%Y-%m-%dT%H:%M:%S")
    valid = (
        data["packaged_license"].ne("")
        & data["package_tag"].ne("")
        & data["test_date"].notna()
        & data["lab_license"].ne("")
        & data["test_name"].ne("")
    )
    data = data.loc[valid].copy()
    if data.empty:
        raise ValueError(
            "No rows contained the required facility, package, test date, "
            "laboratory, and test name values."
        )
    data["record_key"] = (
        data["packaged_license"] + "|" + data["package_tag"] + "|"
        + data["test_date"] + "|" + data["lab_license"] + "|"
        + data["test_name"]
    )
    data["source_filename"] = filename
    data["source_file_hash"] = file_hash
    data["imported_at"] = datetime.now().astimezone().isoformat()
    return data.drop_duplicates("record_key", keep="last")[LAB_DB_COLUMNS]


def import_lab_results_bytes(filename: str, file_bytes: bytes) -> dict[str, Any]:
    """Duplicate-safe import of one Metrc LabResultsReport CSV."""
    initialize_qa_database()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        previous = connection.execute(
            "SELECT stored_rows FROM lab_import_log WHERE source_file_hash = %s",
            (file_hash,),
        ).fetchone()
    if previous:
        return {
            "File": filename, "Status": "Already Imported",
            "Source Rows": int(previous[0]), "Stored Rows": int(previous[0]),
            "Inserted": 0, "Updated": 0,
        }
    buffer = io.BytesIO(file_bytes)
    try:
        source = pd.read_csv(buffer, dtype=str, low_memory=False)
    except UnicodeDecodeError:
        buffer.seek(0)
        source = pd.read_csv(
            buffer, dtype=str, low_memory=False, encoding="latin-1"
        )
    normalized = normalize_lab_results(source, filename, file_hash)
    records = [
        tuple(_sql_value(value) for value in row)
        for row in normalized.itertuples(index=False, name=None)
    ]
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        existing = {
            row[0] for row in connection.execute(
                "SELECT record_key FROM lab_result_records"
            ).fetchall()
        }
        incoming = set(normalized["record_key"])
        placeholders = ", ".join("%s" for _ in LAB_DB_COLUMNS)
        updates = ", ".join(
            f"{column} = EXCLUDED.{column}"
            for column in LAB_DB_COLUMNS if column != "record_key"
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO lab_result_records ("
                + ", ".join(LAB_DB_COLUMNS) + ") VALUES (" + placeholders
                + ") ON CONFLICT(record_key) DO UPDATE SET " + updates,
                records,
            )
        dates = pd.to_datetime(normalized["test_date"], errors="coerce")
        connection.execute(
            """
            INSERT INTO lab_import_log (
                source_file_hash, source_filename, file_size_bytes,
                source_rows, stored_rows, inserted_rows, updated_rows,
                test_min, test_max, imported_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(source_file_hash) DO NOTHING
            """,
            (
                file_hash, filename, len(file_bytes), len(source),
                len(normalized), len(incoming - existing),
                len(incoming & existing),
                dates.min().isoformat() if dates.notna().any() else None,
                dates.max().isoformat() if dates.notna().any() else None,
                datetime.now().astimezone().isoformat(),
            ),
        )
        connection.commit()
    with _QA_CACHE_LOCK:
        _QA_CACHE.update({"loaded_at": 0.0, "payload": None})
    return {
        "File": filename, "Status": "Imported",
        "Source Rows": len(source), "Stored Rows": len(normalized),
        "Inserted": len(incoming - existing), "Updated": len(incoming & existing),
    }


def _extract_oldest_harvest_date(value: Any) -> pd.Timestamp:
    matches = re.findall(
        r"\b(\d{1,2})[.-](\d{1,2})[.-](\d{4})\b", str(value or "")
    )
    dates: list[pd.Timestamp] = []
    for month, day, year in matches:
        try:
            dates.append(pd.Timestamp(year=int(year), month=int(month), day=int(day)))
        except ValueError:
            continue
    return min(dates) if dates else pd.NaT


def _classify_qa_test_type(row: pd.Series) -> str:
    operation = str(row.get("operation", ""))
    item = str(row.get("item", ""))
    category = str(row.get("category", ""))
    combined = f"{item} {category}"
    if re.search(r"\bpre[\s-]*rolls?\b", combined, re.I):
        return "Pre-Rolls"
    if operation == "Cultivation":
        return "Flower" if re.search(r"\bbuds?\s*/\s*flower\b", category, re.I) else "Other / Needs Review"
    if re.search(r"\bvapes?\b|\bcarts?\b|\bcartridge\b|\bdisposable\b", combined, re.I):
        return "Vapes"
    if re.search(r"\bedibles?\b|\bgumm(?:y|ies)\b", combined, re.I):
        return "Edibles"
    if re.search(r"\bbuds?\s*/\s*flower\b|\bflower\b", combined, re.I):
        return "Flower"
    if re.search(r"\bconcentrate\b|\brosin\b|\bbadder\b|\bdiamonds?\b", combined, re.I):
        return "Concentrates"
    return "Other / Needs Review"


def _infer_qa_strain(row: pd.Series) -> str:
    """Resolve historical QA strains from inventory, item, and source fields."""
    def text_value(value: Any) -> str:
        return "" if value is None or pd.isna(value) else str(value).strip()

    invalid_values = {
        "", "nan", "none", "<na>", "strain needs review", "needs review",
        "bulk", "bulk flower", "flower", "packaged flower", "test sample",
    }

    # Craft Kings and Royal Smalls flower labels must use Metrc's Item Strain
    # (the Item Strain column in the package export). Their source harvests may
    # contain several cultivars and are lineage, not the marketed label strain.
    brand = text_value(row.get("inventory_brand", "")).lower()
    sku_type = text_value(row.get("inventory_sku_type", "")).lower()
    if brand in {"craft kings", "royal smalls"} and "flower" in sku_type:
        item_strain = text_value(row.get("inventory_item_strain", ""))
        if item_strain.lower() not in invalid_values:
            return normalize_strain_name(item_strain)

    inventory_value = text_value(row.get("inventory_strain", ""))
    if inventory_value.lower() not in invalid_values:
        return normalize_strain_name(inventory_value)

    item = text_value(row.get("item", ""))
    harvest = text_value(row.get("source_harvest_names", ""))
    source_packages = text_value(row.get("source_package_labels", ""))
    combined = " ".join([item, harvest, source_packages])

    # Craft Kings blend products are intentionally made from mixed source
    # batches, so the finished product name—not a single source strain—is the
    # authoritative QA strain.
    for name, pattern in {
        "Hybrid Blend": r"\bhybrid(?:\s+blend)?\b",
        "Sativa Blend": r"\bsativa(?:\s+blend)?\b",
        "Indica Blend": r"\bindica(?:\s+blend)?\b",
    }.items():
        if re.search(pattern, item, re.I):
            return name

    # These common historical strains are not part of the active product-rule
    # catalog, but must remain searchable in the QA history.
    for name, pattern in {
        "Ice Cream Cake": r"\bice\s+cream\s+cake\b",
        "Fruit Stand": r"\bfruit\s+stand\b",
    }.items():
        if re.search(pattern, combined, re.I):
            return name

    inferred = infer_strain(combined)
    if inferred.lower() not in invalid_values and inferred.lower() not in {
        normalize_strain_name(combined).lower(),
    }:
        return inferred

    # Last resort: the first harvest name, with dates and generic packaging
    # terms removed. This retains older strains that are no longer active SKUs.
    candidate = harvest.split(",", 1)[0].strip()
    candidate = re.sub(
        r"\s*[-_]\s*(?:f?\d+(?:[./-]\d+)*|batch\b.*)$", "", candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"^(?:bulk|flower|packaged|test\s+sample)\s*[-_:]?\s*", "", candidate,
        flags=re.I,
    )
    candidate = re.sub(
        r"\s+(?:bulk|flower|packaged|test\s+sample)\b.*$", "", candidate,
        flags=re.I,
    ).strip(" -_")
    normalized = normalize_strain_name(candidate)
    return normalized if normalized.lower() not in invalid_values else "Strain Needs Review"


def _prepare_qa_packages(
    lab_results: pd.DataFrame, inventory_packages: pd.DataFrame
) -> pd.DataFrame:
    if lab_results.empty:
        return pd.DataFrame()
    data = lab_results.copy()
    data["test_date"] = pd.to_datetime(data["test_date"], errors="coerce")
    data["result"] = pd.to_numeric(data["result"], errors="coerce")
    data["operation"] = data["packaged_license"].astype(str).str[0].map(
        {"C": "Cultivation", "M": "Manufacturing"}
    ).fillna("Other")
    compliance = data[
        ~data["test_name"].astype(str).str.contains(
            r"R\s*&\s*D|research", case=False, na=False
        )
    ].copy()
    if compliance.empty:
        return pd.DataFrame()
    current = (
        compliance.sort_values(["test_date", "packaged_license", "package_tag"])
        .drop_duplicates(["packaged_license", "package_tag"], keep="last")
        [[
            "packaged_license", "packaged_facility", "package_tag",
            "source_harvest_names", "source_package_labels", "item",
            "category", "lab_testing_status", "test_date", "lab_facility",
            "operation",
        ]]
        .copy()
    )
    status_key = current["lab_testing_status"].astype(str).str.lower().str.replace(
        r"[^a-z]", "", regex=True
    )
    current["qa_outcome"] = "Pending"
    current.loc[status_key.isin({"testpassed", "retestpassed"}), "qa_outcome"] = "Passed"
    current.loc[status_key.isin({"testfailed", "retestfailed"}), "qa_outcome"] = "Failed"

    if not inventory_packages.empty and "package_tag" in inventory_packages:
        wanted = ["package_tag"]
        rename: dict[str, str] = {}
        for source, target in [
            ("brand", "inventory_brand"),
            ("strain", "inventory_strain"),
            ("metrc_strain", "inventory_item_strain"),
            ("sku_type", "inventory_sku_type"),
            ("expiration_date", "inventory_expiration_date"),
            ("production_batch_number", "inventory_production_batch_number"),
            ("source_production_batch", "inventory_source_production_batch"),
        ]:
            if source in inventory_packages:
                wanted.append(source)
                rename[source] = target
        current = current.merge(
            inventory_packages[wanted].drop_duplicates("package_tag").rename(columns=rename),
            on="package_tag", how="left",
        )
        current["bulk_package_tag"] = current["source_package_labels"].map(
            lambda value: (extract_metrc_tags(value) or [""])[0]
        )
        source_wanted = ["package_tag"]
        source_rename = {"package_tag": "bulk_package_tag"}
        for source, target in [
            ("brand", "source_inventory_brand"),
            ("strain", "source_inventory_strain"),
            ("metrc_strain", "source_inventory_item_strain"),
            ("sku_type", "source_inventory_sku_type"),
            (
                "source_production_batch",
                "source_inventory_source_production_batch",
            ),
            (
                "production_batch_number",
                "source_inventory_production_batch_number",
            ),
        ]:
            if source in inventory_packages:
                source_wanted.append(source)
                source_rename[source] = target
        source_lookup = (
            inventory_packages[source_wanted]
            .drop_duplicates("package_tag", keep="last")
            .rename(columns=source_rename)
        )
        current = current.merge(source_lookup, on="bulk_package_tag", how="left")

        # A tested bulk tag may have been finished and therefore be absent from
        # the latest Active Packages snapshot. Its active child packages still
        # identify that tag in Source Package(s), and carry the authoritative
        # Item Strain and production-batch fields needed by the label.
        if "source_packages" in inventory_packages:
            descendant_wanted = ["source_packages"]
            descendant_rename: dict[str, str] = {}
            for source, target in [
                ("brand", "descendant_inventory_brand"),
                ("strain", "descendant_inventory_strain"),
                ("metrc_strain", "descendant_inventory_item_strain"),
                ("sku_type", "descendant_inventory_sku_type"),
                (
                    "source_production_batch",
                    "descendant_inventory_source_production_batch",
                ),
                (
                    "production_batch_number",
                    "descendant_inventory_production_batch_number",
                ),
            ]:
                if source in inventory_packages:
                    descendant_wanted.append(source)
                    descendant_rename[source] = target
            descendant_lookup = inventory_packages[descendant_wanted].copy()
            descendant_lookup["ancestor_package_tag"] = descendant_lookup[
                "source_packages"
            ].map(extract_metrc_tags)
            descendant_lookup = descendant_lookup.explode("ancestor_package_tag")
            descendant_lookup["ancestor_package_tag"] = descendant_lookup[
                "ancestor_package_tag"
            ].fillna("").astype(str).str.strip()
            descendant_lookup = descendant_lookup[
                descendant_lookup["ancestor_package_tag"].ne("")
            ]
            descendant_lookup = (
                descendant_lookup
                .drop(columns=["source_packages"])
                .drop_duplicates("ancestor_package_tag", keep="first")
                .rename(columns=descendant_rename)
            )
            current = current.merge(
                descendant_lookup,
                left_on="package_tag",
                right_on="ancestor_package_tag",
                how="left",
            )
    for column in [
        "inventory_brand", "inventory_strain", "inventory_item_strain",
        "inventory_sku_type",
        "inventory_expiration_date", "inventory_production_batch_number",
        "inventory_source_production_batch", "source_inventory_brand",
        "source_inventory_strain", "source_inventory_item_strain",
        "source_inventory_sku_type", "source_inventory_production_batch_number",
        "source_inventory_source_production_batch",
        "descendant_inventory_brand", "descendant_inventory_strain",
        "descendant_inventory_item_strain", "descendant_inventory_sku_type",
        "descendant_inventory_production_batch_number",
        "descendant_inventory_source_production_batch",
    ]:
        if column not in current:
            current[column] = pd.NA

    def prefer_nonblank(*columns: str) -> pd.Series:
        preferred = pd.Series(pd.NA, index=current.index, dtype="object")
        for column in columns:
            values = current[column].astype("string").str.strip()
            values = values.mask(values.eq(""), pd.NA)
            preferred = preferred.combine_first(values)
        return preferred

    # The tested package may not itself be present in the inventory snapshot;
    # in that case, inherit its label metadata from the associated bulk package.
    current["inventory_brand"] = prefer_nonblank(
        "inventory_brand", "source_inventory_brand", "descendant_inventory_brand"
    )
    current["inventory_strain"] = prefer_nonblank(
        "inventory_strain", "source_inventory_strain", "descendant_inventory_strain"
    )
    current["inventory_item_strain"] = prefer_nonblank(
        "inventory_item_strain", "source_inventory_item_strain",
        "descendant_inventory_item_strain",
    )
    current["inventory_sku_type"] = prefer_nonblank(
        "inventory_sku_type", "source_inventory_sku_type",
        "descendant_inventory_sku_type",
    )

    # Preserve Metrc Columns N and M separately. The label formatter applies
    # the brand-specific priority: N, then M, then Source Harvest Name.
    current["source_production_batch"] = prefer_nonblank(
        "source_inventory_source_production_batch",
        "inventory_source_production_batch",
        "descendant_inventory_source_production_batch",
    )
    current["production_batch_number"] = prefer_nonblank(
        "source_inventory_production_batch_number",
        "inventory_production_batch_number",
        "descendant_inventory_production_batch_number",
    )

    fallback_rows = current.rename(columns={"category": "item_category"}).copy()
    fallback_rows["unit_weight_grams"] = pd.NA
    current["sku_type"] = current["inventory_sku_type"].fillna(
        fallback_rows.apply(classify_sku_type, axis=1)
    )
    current["strain"] = current.apply(_infer_qa_strain, axis=1)
    current["strain"] = current["strain"].map(normalize_strain_name)
    current["brand"] = current["inventory_brand"]
    craft_blends = current["strain"].isin([
        "Hybrid Blend", "Sativa Blend", "Indica Blend",
    ])
    current.loc[craft_blends, "brand"] = "Craft Kings"
    missing_brand = ~current["brand"].isin([
        "Clade9", "Craft Kings", "Royal Smalls", "Locals Only", "Cookies", "Precious",
    ])
    current.loc[missing_brand, "brand"] = current.loc[missing_brand].apply(
        lambda row: infer_brand(row.get("item"), row.get("strain"), row.get("sku_type")),
        axis=1,
    )
    current["qa_test_type"] = current.apply(_classify_qa_test_type, axis=1)
    current["expiration_date"] = pd.to_datetime(
        current["inventory_expiration_date"], errors="coerce"
    )
    cultivation_missing = current["expiration_date"].isna() & current["operation"].eq("Cultivation")

    def cultivation_expiration(value: Any) -> pd.Timestamp:
        harvest = _extract_oldest_harvest_date(value)
        if pd.isna(harvest):
            return pd.NaT
        return pd.Timestamp(expiration_from_harvest(harvest.date()))

    current.loc[cultivation_missing, "expiration_date"] = current.loc[
        cultivation_missing, "source_harvest_names"
    ].map(cultivation_expiration)

    names = compliance["test_name"].astype(str).str.strip()
    compliance["metric"] = pd.NA
    compliance.loc[names.str.match(r"^Total THC\s*\(%\)", case=False, na=False), "metric"] = "total_thc"
    compliance.loc[names.str.match(r"^Total Terpenes\s*\(%\)", case=False, na=False), "metric"] = "total_terpenes"
    metrics = compliance[compliance["metric"].notna()].copy()
    if not metrics.empty:
        metrics = (
            metrics.sort_values("test_date")
            .drop_duplicates(
                ["packaged_license", "package_tag", "test_date", "metric"],
                keep="last",
            )
            .pivot(index=["packaged_license", "package_tag", "test_date"], columns="metric", values="result")
            .reset_index()
        )
        current = current.merge(
            metrics,
            on=["packaged_license", "package_tag", "test_date"],
            how="left",
        )
    for column in ["total_thc", "total_terpenes"]:
        if column not in current:
            current[column] = pd.NA
    return current.sort_values("test_date", ascending=False).reset_index(drop=True)


def _qa_record_list(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    result = frame.copy()
    for column in ["test_date", "expiration_date"]:
        if column in result:
            result[column] = pd.to_datetime(result[column], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ["total_thc", "total_terpenes"]:
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce").round(3)
    result = result.astype(object).where(pd.notna(result), None)
    return result.to_dict("records")


def load_qa_module_data(force_refresh: bool = False) -> dict[str, Any]:
    """Load compact QA package records, templates, and import history."""
    now = time.monotonic()
    with _QA_CACHE_LOCK:
        cached = _QA_CACHE.get("payload")
        age = now - float(_QA_CACHE.get("loaded_at", 0.0))
        if cached is not None and not force_refresh and age < QA_CACHE_SECONDS:
            return cached
    # Read-only QA navigation should never run DDL. The import workflow owns
    # table initialization; normal page loads use fresh, retryable reads.
    # Pull one current compliance row per package plus only the two potency
    # analytes used by the dashboard. Downloading every historical analyte was
    # blocking the Reflex event queue for several minutes.
    qa_query = """
        WITH compliance AS (
            SELECT packaged_license, packaged_facility, package_tag,
                   source_harvest_names, source_package_labels, item,
                   category, lab_testing_status, test_date, lab_facility,
                   test_name, result
            FROM lab_result_records
            WHERE COALESCE(test_name, '') !~* 'R\\s*&\\s*D|research'
        ), latest_package AS (
            SELECT DISTINCT ON (packaged_license, package_tag) *
            FROM compliance
            ORDER BY packaged_license, package_tag, test_date DESC, test_name
        ), potency AS (
            SELECT * FROM compliance
            WHERE test_name ~* '^Total (THC|Terpenes)\\s*\\(%%\\)'
        )
        SELECT * FROM latest_package
        UNION ALL
        SELECT * FROM potency
        """
    import_query = (
        "SELECT source_filename, source_rows, stored_rows, inserted_rows, "
        "updated_rows, test_min, test_max, imported_at "
        "FROM lab_import_log ORDER BY imported_at DESC"
    )
    template_query = (
        "SELECT * FROM qa_label_templates WHERE is_active = 1 "
        "ORDER BY template_name"
    )
    # These reads share one remote connection. The queries are compact, and
    # avoiding three simultaneous pooler handshakes materially improves the
    # first user's QA load without changing the cached second-user path.
    labs, import_log, templates = query_frames(
        [
            (qa_query, ()),
            (import_query, ()),
            (template_query, ()),
        ],
        statement_timeout_seconds=90,
    )
    packages = None
    with _OPERATIONAL_CONTEXT_LOCK:
        context_age = time.monotonic() - float(
            _OPERATIONAL_CONTEXT.get("loaded_at", 0.0)
        )
        context = (
            _OPERATIONAL_CONTEXT.get("payload")
            if context_age < OPERATIONAL_CACHE_SECONDS else None
        )
        if context:
            packages = context.get("inventory_packages")
    if packages is None:
        # If the initial Inventory build is already running, reuse its package
        # frame instead of issuing duplicate snapshot and package queries.
        packages = load_operational_context()["inventory_packages"]
    prepared = _prepare_qa_packages(labs, packages)
    import_log = import_log.rename(columns={
        "source_filename": "File", "source_rows": "Source Rows",
        "stored_rows": "Stored Rows", "inserted_rows": "Inserted",
        "updated_rows": "Updated", "test_min": "Test Min",
        "test_max": "Test Max", "imported_at": "Imported At",
    })
    template_rows: list[dict[str, Any]] = []
    for row in templates.to_dict("records"):
        try:
            fields = json.loads(str(row.get("fields_json", "[]") or "[]"))
        except json.JSONDecodeError:
            fields = list(DEFAULT_QA_LABEL_CONFIG["fields"])
        if "expiration_date" not in fields:
            fields.append("expiration_date")
        template_rows.append({
            "Template ID": str(row.get("template_id", "")),
            "Template Name": str(row.get("template_name", "")),
            "Scope": str(row.get("scope", "Both")),
            "Brand Filter": str(row.get("brand_filter", "All Brands")),
            "SKU Filter": str(row.get("sku_filter", "All SKU Types")),
            "Label Size": str(row.get("label_size", "4 x 6")),
            "Title": str(row.get("title", "QCC QA / Compliance Summary")),
            "Footer": str(row.get("footer", "")),
            "Fields": [field for field in fields if field in QA_LABEL_FIELDS],
            "Version": int(row.get("version", 1) or 1),
        })
    payload = {
        "packages": _qa_record_list(prepared),
        "templates": template_rows,
        "import_log": _qa_record_list(import_log.head(100)),
        "record_count": int(len(prepared)),
        "analyte_count": int(len(labs)),
    }
    with _QA_CACHE_LOCK:
        _QA_CACHE.update({"loaded_at": now, "payload": payload})
    return payload


def load_qa_analytes(package_tag: str, packaged_license: str) -> list[dict[str, Any]]:
    package_tag = str(package_tag or "").strip()
    packaged_license = str(packaged_license or "").strip()
    if not package_tag:
        return []

    # Metrc package tags are globally unique. Prefer the facility-scoped lookup,
    # but fall back to the tag alone because older imported laboratory rows can
    # contain a blank or differently formatted packaged-license value.
    if packaged_license:
        rows = safe_query_frame(
            "SELECT test_date, test_name, result, test_passed "
            "FROM lab_result_records WHERE package_tag = %s "
            "AND packaged_license = %s ORDER BY test_date DESC, test_name",
            (package_tag, packaged_license),
        )
    else:
        rows = pd.DataFrame()
    if rows.empty:
        rows = safe_query_frame(
            "SELECT test_date, test_name, result, test_passed "
            "FROM lab_result_records WHERE package_tag = %s "
            "ORDER BY test_date DESC, test_name",
            (package_tag,),
        )
    if rows.empty:
        return []
    rows["test_date"] = pd.to_datetime(rows["test_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    rows["result"] = pd.to_numeric(rows["result"], errors="coerce").round(4)
    rows["test_passed"] = rows["test_passed"].fillna(0).astype(bool).map({True: "Yes", False: "No"})
    rows = rows.rename(columns={
        "test_date": "Test Date", "test_name": "Test",
        "result": "Result", "test_passed": "Passed",
    })
    return _qa_record_list(rows)


def adjusted_coa_key(package_tag: str, packaged_license: str = "") -> str:
    """Return the stable identifier used for one laboratory sample COA."""
    identity = f"{str(packaged_license or '').strip()}|{str(package_tag or '').strip()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def load_adjusted_coa(package_tag: str, packaged_license: str = "") -> dict[str, Any]:
    """Load the operator-verified high-precision values for one lab sample."""
    package_tag = str(package_tag or "").strip()
    packaged_license = str(packaged_license or "").strip()
    if not package_tag:
        return {}
    initialize_qa_database()
    rows = safe_query_frame(
        "SELECT * FROM qa_adjusted_coas WHERE coa_key = %s LIMIT 1",
        (adjusted_coa_key(package_tag, packaged_license),),
    )
    if rows.empty and packaged_license:
        rows = safe_query_frame(
            "SELECT * FROM qa_adjusted_coas WHERE package_tag = %s "
            "ORDER BY updated_at DESC LIMIT 1",
            (package_tag,),
        )
    if rows.empty:
        return {}
    record = rows.iloc[0].to_dict()
    try:
        record["terpene_names"] = json.loads(
            str(record.get("terpene_names_json", "[]") or "[]")
        )
        record["terpene_values"] = json.loads(
            str(record.get("terpene_values_json", "[]") or "[]")
        )
        record["suspect_flags"] = json.loads(
            str(record.get("suspect_flags_json", "[]") or "[]")
        )
    except json.JSONDecodeError:
        return {}
    return record


def save_adjusted_coa(
    package_tag: str,
    packaged_license: str,
    test_date: str,
    total_terpenes: float,
    total_cbg: float,
    terpene_names: list[str],
    terpene_values: list[float],
    suspect_flags: list[str],
    updated_by: str,
) -> dict[str, Any]:
    """Upsert one auditable Adjusted COA and return its normalized values."""
    initialize_qa_database()
    package_tag = str(package_tag or "").strip()
    packaged_license = str(packaged_license or "").strip()
    now = datetime.now().astimezone().isoformat()
    coa_key = adjusted_coa_key(package_tag, packaged_license)
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute(
            """
            INSERT INTO qa_adjusted_coas (
                coa_key, package_tag, packaged_license, test_date,
                total_terpenes, total_cbg, terpene_names_json,
                terpene_values_json, suspect_flags_json, updated_at, updated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(coa_key) DO UPDATE SET
                test_date = EXCLUDED.test_date,
                total_terpenes = EXCLUDED.total_terpenes,
                total_cbg = EXCLUDED.total_cbg,
                terpene_names_json = EXCLUDED.terpene_names_json,
                terpene_values_json = EXCLUDED.terpene_values_json,
                suspect_flags_json = EXCLUDED.suspect_flags_json,
                updated_at = EXCLUDED.updated_at,
                updated_by = EXCLUDED.updated_by
            """,
            (
                coa_key, package_tag, packaged_license, str(test_date or ""),
                float(total_terpenes), float(total_cbg),
                json.dumps(list(terpene_names)), json.dumps(list(terpene_values)),
                json.dumps(list(suspect_flags)), now, str(updated_by or ""),
            ),
        )
        connection.commit()
    return {
        "coa_key": coa_key,
        "package_tag": package_tag,
        "packaged_license": packaged_license,
        "test_date": str(test_date or ""),
        "total_terpenes": float(total_terpenes),
        "total_cbg": float(total_cbg),
        "terpene_names": list(terpene_names),
        "terpene_values": [float(value) for value in terpene_values],
        "suspect_flags": list(suspect_flags),
        "updated_at": now,
        "updated_by": str(updated_by or ""),
    }


def log_qa_label_download(
    package: dict[str, Any], template: dict[str, Any], printed_by: str,
    output_type: str = "html",
) -> None:
    initialize_qa_database()
    now = datetime.now().astimezone().isoformat()
    print_id = hashlib.sha256(
        f"{package.get('package_tag')}|{template.get('Template ID')}|{now}".encode()
    ).hexdigest()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute(
            """
            INSERT INTO qa_label_print_log (
                print_id, package_tag, source_harvest, template_id,
                template_version, output_type, snapshot_json, printed_at,
                printed_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                print_id, str(package.get("package_tag", "")),
                str(package.get("source_harvest_names", "")),
                str(template.get("Template ID", "")),
                int(template.get("Version", 1) or 1), output_type,
                json.dumps(package, default=str), now, printed_by,
            ),
        )
        connection.commit()


def load_transfer_rows() -> pd.DataFrame:
    selected = ", ".join(SALES_TRANSFER_COLUMNS)
    return streamed_query_frame(
        f"SELECT {selected} FROM transfer_records "
        "WHERE origin_facility = %s AND COALESCE(voided, 0) = 0 AND ("
        "(transfer_type = 'Wholesale Transfer' "
        "AND COALESCE(destination_facility_type, '') ILIKE '%%Retailer%%' "
        "AND state = 'Accepted' AND ("
        "COALESCE(item, '') ILIKE ANY(ARRAY["
        "'%%packaged%%','%%pre-roll%%','%%preroll%%','%%pre roll%%',"
        "'%%vape%%','%%cartridge%%','%%disposable%%','%%gumm%%',"
        "'%%edible%%','%%chocolate%%']) OR "
        "COALESCE(item_category, '') ILIKE ANY(ARRAY["
        "'%%packaged%%','%%raw pre-roll%%','%%concentrate (each)%%'])"
        ")) OR state IN ('Shipped', 'Rejected', 'Returned'))",
        ("The QCC Group LLC",),
    )


def load_transfer_import_log() -> pd.DataFrame:
    return safe_query_frame(
        "SELECT source_filename, file_size_bytes, source_rows, stored_rows, "
        "inserted_rows, updated_rows, created_min, created_max, imported_at "
        "FROM transfer_import_log ORDER BY imported_at DESC"
    )


def _lineage_date(value: Any) -> str:
    """Format a database timestamp as a customer-service date."""
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.strftime("%Y-%m-%d") if pd.notna(parsed) else "—"


def _lineage_number(value: Any) -> str:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return "—"
    return f"{float(number):,.2f}".rstrip("0").rstrip(".")


def load_package_lineage(search_text: str) -> dict[str, Any]:
    """Trace one package tag or manifest through snapshots and transfers.

    The lookup is deliberately on demand.  Normal dashboard hydration continues
    to load only the current inventory snapshot, while Customer Service can
    search every preserved snapshot without sending that history to the browser.
    The returned shape is also compatible with future Metrc package-event rows.
    """
    target = str(search_text or "").strip()
    if not target:
        raise ValueError("Enter a Metrc package tag or manifest number.")

    transfer_history = query_frame(
        "SELECT manifest, invoice_number, origin_license, origin_facility, "
        "destination_license, destination_facility, transfer_type, created_at, "
        "received_at, package_tag, state, item, item_category, "
        "shipper_dollar_amount, actual_shipped, actual_shipped_uom, "
        "actual_received, actual_received_uom "
        "FROM transfer_records WHERE COALESCE(voided, 0) = 0 "
        "AND (package_tag = %s OR manifest = %s) "
        "ORDER BY created_at, manifest, package_tag",
        (target, target),
    )
    transfer_tags = (
        transfer_history.get("package_tag", pd.Series(dtype=str))
        .fillna("").astype(str).str.strip()
    )
    package_tags = list(dict.fromkeys(tag for tag in transfer_tags if tag))
    if not package_tags:
        package_tags = [target]
    elif target.startswith("1A") and target not in package_tags:
        package_tags.insert(0, target)

    source_patterns = [f"%{tag}%" for tag in package_tags]
    observations = query_frame(
        "SELECT p.package_tag, p.source_packages, p.source_harvest, "
        "p.production_batch_number, p.source_production_batch, p.item, "
        "p.brand, p.strain, p.quantity, p.unit, p.location, p.qa_status, "
        "p.production_stage, p.expiration_date, p.source_license_number, "
        "s.business_date, s.published_at "
        "FROM inventory_snapshot_packages p "
        "JOIN inventory_snapshots s ON s.snapshot_id = p.snapshot_id "
        "WHERE p.package_tag = ANY(%s) OR p.source_packages ILIKE ANY(%s) "
        "ORDER BY s.published_at, p.package_tag LIMIT 1500",
        (package_tags, source_patterns),
    )

    source_tags: list[str] = []
    if not observations.empty:
        exact = observations[observations["package_tag"].isin(package_tags)]
        for value in exact.get("source_packages", pd.Series(dtype=str)):
            for tag in extract_metrc_tags(value):
                if tag not in source_tags and tag not in package_tags:
                    source_tags.append(tag)

    source_observations = pd.DataFrame()
    if source_tags:
        source_observations = query_frame(
            "SELECT p.package_tag, p.source_packages, p.source_harvest, "
            "p.production_batch_number, p.source_production_batch, p.item, "
            "p.brand, p.strain, p.quantity, p.unit, p.location, p.qa_status, "
            "p.production_stage, p.expiration_date, p.source_license_number, "
            "s.business_date, s.published_at "
            "FROM inventory_snapshot_packages p "
            "JOIN inventory_snapshots s ON s.snapshot_id = p.snapshot_id "
            "WHERE p.package_tag = ANY(%s) "
            "ORDER BY s.published_at, p.package_tag LIMIT 1000",
            (source_tags,),
        )

    lineage_rows: list[dict[str, Any]] = []
    combined_tags = [*package_tags, *source_tags]
    for tag in combined_tags:
        frames = []
        if not observations.empty:
            frames.append(observations[observations["package_tag"] == tag])
        if not source_observations.empty:
            frames.append(source_observations[source_observations["package_tag"] == tag])
        tag_rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        latest = tag_rows.iloc[-1] if not tag_rows.empty else {}
        lineage_rows.append({
            "Relationship": "Searched Package" if tag in package_tags else "Source Package",
            "Package Tag": tag,
            "Item": str(latest.get("item", "") or "—"),
            "Strain": str(latest.get("strain", "") or "—"),
            "Source Harvest": str(latest.get("source_harvest", "") or "—"),
            "Production Batch": str(
                latest.get("source_production_batch", "")
                or latest.get("production_batch_number", "") or "—"
            ),
            "First Seen": _lineage_date(tag_rows["business_date"].min()) if not tag_rows.empty else "—",
            "Last Seen": _lineage_date(tag_rows["business_date"].max()) if not tag_rows.empty else "—",
            "Snapshots": str(len(tag_rows)),
        })

    timeline_rows: list[dict[str, Any]] = []
    if not observations.empty:
        observed = observations.copy()
        observed["business_date"] = pd.to_datetime(
            observed["business_date"], errors="coerce"
        )
        observed = observed.sort_values("published_at").drop_duplicates(
            ["business_date", "package_tag"], keep="last"
        )
        for _, row in observed.iterrows():
            relationship = (
                "Inventory snapshot" if row["package_tag"] in package_tags
                else "Descendant package observed"
            )
            timeline_rows.append({
                "Date": _lineage_date(row.get("business_date")),
                "Event": relationship,
                "Package Tag": str(row.get("package_tag", "")),
                "Manifest": "—",
                "Customer / Location": str(row.get("location", "") or "—"),
                "Status": str(row.get("qa_status", "") or row.get("production_stage", "") or "—"),
                "Quantity": _lineage_number(row.get("quantity")),
                "Unit": str(row.get("unit", "") or "—"),
                "Item": str(row.get("item", "") or "—"),
            })
    for _, row in transfer_history.iterrows():
        timeline_rows.append({
            "Date": _lineage_date(row.get("created_at")),
            "Event": "Transfer package outcome",
            "Package Tag": str(row.get("package_tag", "") or "—"),
            "Manifest": str(row.get("manifest", "") or "—"),
            "Customer / Location": str(row.get("destination_facility", "") or "—"),
            "Status": str(row.get("state", "") or "—"),
            "Quantity": _lineage_number(row.get("actual_shipped")),
            "Unit": str(row.get("actual_shipped_uom", "") or "—"),
            "Item": str(row.get("item", "") or "—"),
        })
    timeline_rows.sort(key=lambda row: row["Date"], reverse=True)

    exact_observation_count = 0
    if not observations.empty:
        exact_observation_count = int(
            observations["package_tag"].isin(package_tags).sum()
        )
    found = bool(exact_observation_count or len(transfer_history))
    if not found:
        message = (
            "No stored snapshot or transfer record contains this value. "
            "A Metrc API package lookup will be the next source when connected."
        )
    elif exact_observation_count == 0:
        message = (
            "Transfer history was found, but these packages were never captured "
            "in a published inventory snapshot. Source genealogy is not yet available."
        )
    elif not source_tags:
        message = (
            "Package history was found, but no source package tag was preserved "
            "on its stored snapshot records."
        )
    else:
        message = "Historical inventory, source-package, and transfer evidence was found."

    return {
        "found": found,
        "message": message,
        "package_count": len(package_tags),
        "source_count": len(source_tags),
        "snapshot_count": exact_observation_count,
        "transfer_count": len(transfer_history),
        "lineage": lineage_rows,
        "timeline": timeline_rows,
    }


def load_latest_inventory_skus() -> tuple[dict[str, Any], pd.DataFrame]:
    snapshots = safe_query_frame(
        "SELECT snapshot_id, business_date, published_at, published_by, "
        "package_count, sku_count FROM inventory_snapshots "
        "WHERE status = 'Published' ORDER BY published_at DESC LIMIT 1"
    )
    if snapshots.empty:
        return {}, pd.DataFrame()
    snapshot = snapshots.iloc[0].to_dict()
    rows = query_frame(
        "SELECT snapshot_id, brand, strain, sku_type, on_hand_units, "
        "package_count, source_license_number, source_license_type "
        "FROM inventory_snapshot_skus WHERE snapshot_id = %s",
        (snapshot["snapshot_id"],),
    )
    return snapshot, rows


def load_latest_inventory_packages(snapshot_id: str) -> pd.DataFrame:
    """Load the classified package rows published by Streamlit 81.4+."""
    if not snapshot_id:
        return pd.DataFrame()
    return safe_query_frame(
        "SELECT * FROM inventory_snapshot_packages WHERE snapshot_id = %s",
        (snapshot_id,),
    )


def load_latest_inventory_bundle() -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Load the current snapshot, SKU summary, and packages on one connection."""
    url = database_url()
    if not url or psycopg is None:
        raise RuntimeError("Supabase database access is not configured.")
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with psycopg.connect(url, connect_timeout=15) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '60s'")
                    cursor.execute(
                        "SELECT snapshot_id, business_date, published_at, published_by, "
                        "package_count, sku_count FROM inventory_snapshots "
                        "WHERE status = 'Published' ORDER BY published_at DESC LIMIT 1"
                    )
                    snapshot_row = cursor.fetchone()
                    if snapshot_row is None:
                        return {}, pd.DataFrame(), pd.DataFrame()
                    snapshot_columns = [column.name for column in cursor.description]
                    snapshot = dict(zip(snapshot_columns, snapshot_row))
                    snapshot_id = snapshot["snapshot_id"]

                    cursor.execute(
                        "SELECT snapshot_id, brand, strain, sku_type, on_hand_units, "
                        "package_count, source_license_number, source_license_type "
                        "FROM inventory_snapshot_skus WHERE snapshot_id = %s",
                        (snapshot_id,),
                    )
                    sku_columns = [column.name for column in cursor.description]
                    inventory_skus = pd.DataFrame(
                        cursor.fetchall(), columns=sku_columns
                    )

                    cursor.execute(
                        "SELECT * FROM inventory_snapshot_packages WHERE snapshot_id = %s",
                        (snapshot_id,),
                    )
                    package_columns = [column.name for column in cursor.description]
                    inventory_packages = pd.DataFrame(
                        cursor.fetchall(), columns=package_columns
                    )
                    inventory_packages = repair_manufacturing_inventory_ages(
                        inventory_packages
                    )
                    inventory_packages = promote_legitimate_manufacturing_samples(
                        inventory_packages
                    )
            return snapshot, inventory_skus, inventory_packages
        except Exception as error:
            last_error = error
            text = str(error).lower()
            transient = (
                isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError))
                or any(marker in text for marker in (
                    "ssl connection has been closed", "consuming input failed",
                    "server closed the connection", "connection reset",
                    "connection timed out", "terminating connection",
                ))
            )
            if not transient or attempt:
                raise
            time.sleep(0.35)
    raise last_error or RuntimeError("The current Inventory snapshot could not be read.")


def normalized_rule_text(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def wip_inventory_status(
    packages: pd.DataFrame, plans: pd.DataFrame, sources: pd.DataFrame
) -> pd.DataFrame:
    """Return physical passed WIP with committed and available weights."""
    if packages.empty:
        return packages.copy()
    scope = packages[
        packages["qcc_owned"].fillna(0).astype(bool)
        & packages["production_stage"].isin(["WIP-Cultivation", "WIP-Manufacturing"])
        & packages["qa_status"].eq("Test Passed")
    ].copy()
    scope["calculated_weight_grams"] = pd.to_numeric(
        scope["calculated_weight_grams"], errors="coerce"
    ).fillna(0)
    if scope.empty:
        return scope
    active_ids: set[str] = set()
    if not plans.empty and "status" in plans:
        active_ids = set(
            plans.loc[
                plans["status"].isin(["Planned", "Committed", "In Production"]),
                "plan_id",
            ].astype(str)
        )
    allocated = pd.DataFrame(columns=["package_tag", "allocated_weight_grams"])
    if active_ids and not sources.empty:
        active_sources = sources[sources["plan_id"].astype(str).isin(active_ids)].copy()
        active_sources["allocated_weight_grams"] = pd.to_numeric(
            active_sources["allocated_weight_grams"], errors="coerce"
        ).fillna(0)
        allocated = active_sources.groupby("package_tag", as_index=False)[
            "allocated_weight_grams"
        ].sum()
    scope = scope.merge(allocated, on="package_tag", how="left")
    scope["allocated_weight_grams"] = scope["allocated_weight_grams"].fillna(0)
    scope["available_weight_grams"] = (
        scope["calculated_weight_grams"] - scope["allocated_weight_grams"]
    ).clip(lower=0)
    return scope


def available_wip_inventory(
    packages: pd.DataFrame, plans: pd.DataFrame, sources: pd.DataFrame
) -> pd.DataFrame:
    """Return only WIP that remains available for potential SKU matching."""
    scope = wip_inventory_status(packages, plans, sources)
    if scope.empty:
        return scope
    return scope[scope["available_weight_grams"].gt(0)].copy()


def potential_wip_for_sku(
    wip: pd.DataFrame, brand: str, strain: str, sku_type: str
) -> pd.DataFrame:
    """Apply the same product-specific WIP compatibility rules as Streamlit."""
    if wip.empty:
        return wip.copy()
    scope = wip.copy()
    sku_key = str(sku_type).strip().lower()
    target_strain = normalize_strain_name(strain).strip().lower()
    source_strain = scope["strain"].apply(normalize_strain_name).str.lower()
    same_strain = source_strain.eq(target_strain)
    cultivation = scope["production_stage"].eq("WIP-Cultivation")
    manufacturing = scope["production_stage"].eq("WIP-Manufacturing")
    item = scope["item"].fillna("").astype(str).str.lower()
    category = scope["category"].apply(normalized_rule_text)
    mask = pd.Series(False, index=scope.index)
    component = "Primary"
    shared_blend = str(brand).strip() == "Craft Kings" and target_strain in {
        "hybrid blend", "sativa blend", "indica blend",
    }
    if "flower" in sku_key and "pre-roll" not in sku_key:
        mask = cultivation & same_strain & category.eq("budflowerbulk") & ~item.str.contains(
            r"\b(?:shake|trim|mids?|smalls?)\b", regex=True
        )
    elif sku_key in {"0.5g vape lr", "1g live rosin"}:
        mask = manufacturing & same_strain & item.str.contains(
            r"\b(?:live\s+rosin|rosin)\b", regex=True
        )
    elif sku_key == "1g vape cr":
        mask = manufacturing & same_strain & category.eq("concentrateweight") & item.str.contains(
            r"\bbulk\s+concentrate\b", regex=True
        )
    elif sku_key == "1g vape dc":
        mask = manufacturing & item.str.contains(r"\bdistillate\b", regex=True)
    elif str(brand).strip() == "Locals Only" and (
        "wet badder" in sku_key or "wet diamonds" in sku_key
    ):
        pattern = r"\bwet\s+diamonds?\b" if "wet diamonds" in sku_key else r"\bwet\s+badder\b"
        mask = manufacturing & same_strain & item.str.contains(pattern, regex=True)
    elif "infused pre-roll" in sku_key:
        flower = cultivation & (pd.Series(True, index=scope.index) if shared_blend else same_strain)
        if "iwh" in sku_key:
            infusion = manufacturing & item.str.contains(r"\bbulk\b", regex=True) & item.str.contains(
                r"\b(?:iwh|ice\s+water\s+hash)\b", regex=True
            )
        else:
            infusion = manufacturing & item.str.contains(
                r"\binfused\s+pre[- ]?rolls?\b", regex=True
            ) & ~item.str.contains(r"\b(?:iwh|ice\s+water\s+hash|ea|each)\b", regex=True)
        mask = flower | infusion
        scope["wip_component"] = "Primary"
        scope.loc[flower, "wip_component"] = "Flower"
        scope.loc[infusion, "wip_component"] = "Infusion"
    elif "pre-roll" in sku_key:
        mask = cultivation & (pd.Series(True, index=scope.index) if shared_blend else same_strain)
    elif sku_key == "edibles":
        target = (gummy_variant(strain) or str(strain)).strip().lower()
        source = scope["item"].apply(lambda value: gummy_variant(value) or "").str.lower()
        gummy_signal = (
            category.eq("bulkgummies") & item.str.contains(r"\bbulk\b", regex=True)
        ) | item.str.contains(r"\bbulk\b.*\bgumm(?:y|ies)\b", regex=True)
        mask = manufacturing & gummy_signal & source.eq(target)
    result = scope[mask].copy()
    if "wip_component" not in result:
        result["wip_component"] = component
    return result


def _ensure_production_schema() -> None:
    """Apply small, backward-compatible production planning additions once."""
    global _PRODUCTION_SCHEMA_READY
    if _PRODUCTION_SCHEMA_READY or psycopg is None or not database_url():
        return
    with _PRODUCTION_SCHEMA_LOCK:
        if _PRODUCTION_SCHEMA_READY:
            return
        with psycopg.connect(database_url(), connect_timeout=15) as connection:
            connection.execute(
                "ALTER TABLE production_plans ADD COLUMN IF NOT EXISTS "
                "production_line TEXT NOT NULL DEFAULT 'Unassigned'"
            )
            # Plan deletions and WIP release filter each child table by
            # plan_id. These indexes keep that work fast as plan history grows.
            for statement in (
                "CREATE INDEX IF NOT EXISTS idx_production_plan_audit_plan_id "
                "ON production_plan_audit (plan_id)",
                "CREATE INDEX IF NOT EXISTS idx_production_plan_sources_plan_id "
                "ON production_plan_sources (plan_id)",
                "CREATE INDEX IF NOT EXISTS idx_production_plan_outputs_plan_id "
                "ON production_plan_outputs (plan_id)",
            ):
                connection.execute(statement)
            connection.commit()
        _PRODUCTION_SCHEMA_READY = True


def load_production_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # This additive migration is backward compatible with the live 0.8.9 app.
    # It runs before the read so existing plans receive a safe label.
    _ensure_production_schema()
    plans, outputs, sources = query_frames([
        ("SELECT * FROM production_plans ORDER BY created_at DESC", ()),
        (
            "SELECT * FROM production_plan_outputs "
            "ORDER BY plan_id, unit_weight_grams DESC",
            (),
        ),
        (
            "SELECT plan_id, package_tag, allocated_weight_grams "
            "FROM production_plan_sources ORDER BY plan_id, package_tag",
            (),
        ),
    ])
    return plans, outputs, sources


def load_production_module_data() -> dict[str, Any]:
    """Read only the small production-planning tables after plan changes."""
    plans, outputs, sources = load_production_data()
    saved_plans = build_saved_plan_rows(plans, outputs, sources)
    saved_plan_cards = build_saved_plan_cards(plans, outputs, sources)
    calendar = [
        {
            "Target Date": card["Target Date"],
            "Plan ID": card["Plan ID"],
            "Plan Name": card["Plan Name"],
            "Status": card["Status"],
            "Department": card["Department"],
            "Production Line": card["Production Line"],
            "Line Color": card["Line Color"],
            "Line Background": card["Line Background"],
            "Brand": card["Brand"],
            "Strain": card["Strain"],
            "SKU Type": card["SKU Type"],
            "Output Summary": card["Output Summary"],
        }
        for card in saved_plan_cards
        if card["Target Date"]
    ]
    return {
        "saved_plans": record_list(saved_plans),
        "saved_plan_cards": saved_plan_cards,
        "production_templates": record_list(
            load_reflex_production_templates()
        ),
        "calendar": sorted(calendar, key=lambda row: row["Target Date"]),
    }


ACTIVE_PRODUCTION_STATUSES = {"Planned", "Committed", "In Production"}
PRODUCTION_PLAN_STATUSES = [
    "Planned", "Committed", "In Production", "Completed", "Cancelled",
]


def production_recipe_type(brand: str, sku_type: str) -> str:
    """Return the approved Streamlit 81.4 formulation family."""
    brand_text = str(brand or "").strip()
    sku_text = str(sku_type or "").strip().lower()
    if "infused pre-roll" in sku_text:
        return "Infused Pre-Rolls"
    if "pre-roll" in sku_text:
        return "Non-Infused Pre-Rolls"
    if "flower" in sku_text:
        if brand_text in {"Craft Kings", "Royal Smalls"}:
            return "Craft Kings / Royal Smalls Flower"
        return "Flower Mix"
    if brand_text == "Craft Kings" and (
        "gumm" in sku_text or "edible" in sku_text
    ):
        return "Craft Kings Gummies"
    if "vape" in sku_text:
        return "Clade9 Vapes"
    if any(token in sku_text for token in [
        "concentrate", "live rosin", "wet badder", "wet diamond",
    ]):
        return "Concentrates"
    return "Unsupported"


def production_unit_weight_grams(sku_type: str) -> float:
    """Return the labeled package weight used by the production yield model."""
    text = str(sku_type or "").strip().lower()
    known = {
        "0.5g vape lr": 0.5,
        "1g vape cr": 1.0,
        "1g vape dc": 1.0,
        "1g live rosin": 1.0,
        "1g flower": 1.0,
        "3.5g flower": 3.5,
        "7g flower": 7.0,
        "14g flower": 14.0,
        "28g flower": 28.0,
        "28g flower smalls": 28.0,
    }
    if text in known:
        return known[text]
    import re
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*g\b", text)
    return float(match.group(1)) if match else 0.0


def calculate_flower_batch_mix(
    batch_weight_grams: float, percentages: dict[str, float]
) -> dict[str, Any]:
    """Convert a bulk-flower percentage mix into weights and whole units."""
    batch = max(float(batch_weight_grams or 0), 0.0)
    keys = [
        "flower_28", "flower_14", "flower_7", "flower_35", "flower_1",
        "smalls", "loss",
    ]
    normalized = {
        key: max(float(percentages.get(key, 0) or 0), 0.0) for key in keys
    }
    weights = {key: batch * value / 100 for key, value in normalized.items()}
    units = {
        "flower_28": int(weights["flower_28"] // 28.0),
        "flower_14": int(weights["flower_14"] // 14.0),
        "flower_7": int(weights["flower_7"] // 7.0),
        "flower_35": int(weights["flower_35"] // 3.5),
        "flower_1": int(weights["flower_1"] // 1.0),
    }
    packaged = sum(
        units[key] * weight for key, weight in {
            "flower_28": 28.0, "flower_14": 14.0, "flower_7": 7.0,
            "flower_35": 3.5, "flower_1": 1.0,
        }.items()
    )
    retail_percent = sum(normalized[key] for key in [
        "flower_28", "flower_14", "flower_7", "flower_35", "flower_1",
    ])
    usable_percent = max(100 - normalized["loss"], 0)
    return {
        "batch_weight_grams": batch,
        "percentage_total": sum(normalized.values()),
        "percentages": normalized,
        "allocated_grams": weights,
        "projected_units": units,
        "retail_flower_percent": retail_percent,
        "selection_intensity": (
            normalized["smalls"] / usable_percent * 100
            if usable_percent else 0.0
        ),
        "rounding_residual_grams": max(
            sum(weights[key] for key in units) - packaged, 0.0
        ),
    }


def calculate_single_output_yield(
    batch_weight_grams: float,
    unit_weight_grams: float,
    process_loss_percent: float = 0,
    overfill_percent: float = 0,
    qa_retention_grams: float = 0,
) -> dict[str, Any]:
    """Calculate whole-package yield for one finished SKU."""
    batch = max(float(batch_weight_grams or 0), 0.0)
    unit_weight = max(float(unit_weight_grams or 0), 0.0)
    loss = max(float(process_loss_percent or 0), 0.0)
    overfill = max(float(overfill_percent or 0), 0.0)
    retention = max(float(qa_retention_grams or 0), 0.0)
    loss_grams = batch * loss / 100
    packageable = max(batch - loss_grams - retention, 0.0)
    planned_unit_weight = unit_weight * (1 + overfill / 100)
    projected = int(packageable // planned_unit_weight) if planned_unit_weight else 0
    return {
        "process_loss_grams": loss_grams,
        "packageable_weight_grams": packageable,
        "planned_unit_weight_grams": planned_unit_weight,
        "projected_units": projected,
        "rounding_residual_grams": max(
            packageable - projected * planned_unit_weight, 0.0
        ),
    }


def _cursor_frame(cursor: Any) -> pd.DataFrame:
    if not cursor.description:
        return pd.DataFrame()
    return pd.DataFrame(
        cursor.fetchall(), columns=[column.name for column in cursor.description]
    )


def _ensure_clone_allocation_table(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_clone_allocations (
            allocation_id TEXT PRIMARY KEY,
            cycle_name TEXT NOT NULL,
            flower_room TEXT NOT NULL,
            flower_entry_date DATE NOT NULL,
            clone_cut_date DATE NOT NULL,
            veg_transfer_date DATE NOT NULL,
            harvest_date DATE NOT NULL,
            available_date DATE NOT NULL,
            overage_percent INTEGER NOT NULL,
            post_harvest_days INTEGER NOT NULL,
            bench_plans JSONB NOT NULL,
            strain_summary JSONB NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def create_clone_allocation(
    *,
    cycle_name: str,
    flower_room: str,
    flower_entry_date: str,
    clone_cut_date: str,
    veg_transfer_date: str,
    harvest_date: str,
    available_date: str,
    overage_percent: int,
    post_harvest_days: int,
    bench_plans: list[dict[str, Any]],
    strain_summary: list[dict[str, Any]],
    created_by: str = "QCC Reflex User",
) -> str:
    """Save a shared facility clone allocation in Supabase."""
    if psycopg is None or not database_url():
        raise RuntimeError(
            "A live Supabase connection is required to save clone allocations."
        )
    if not str(cycle_name or "").strip():
        raise ValueError("Enter a cycle or crop name before saving.")
    if not strain_summary:
        raise ValueError("Allocate at least one strain before saving.")
    now_text = datetime.now().astimezone().isoformat()
    seed = json.dumps(
        {
            "cycle": cycle_name,
            "room": flower_room,
            "flower_entry": flower_entry_date,
            "created": now_text,
        },
        sort_keys=True,
    )
    allocation_id = (
        "QCC-CA-"
        + datetime.now().strftime("%Y%m%d-")
        + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()
    )
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_clone_allocation_table(cursor)
            cursor.execute(
                "INSERT INTO qcc_clone_allocations ("
                "allocation_id, cycle_name, flower_room, flower_entry_date, "
                "clone_cut_date, veg_transfer_date, harvest_date, available_date, "
                "overage_percent, post_harvest_days, bench_plans, strain_summary, "
                "created_by, created_at) VALUES ("
                + ", ".join(["%s"] * 14)
                + ")",
                (
                    allocation_id,
                    str(cycle_name).strip(),
                    str(flower_room).strip(),
                    str(flower_entry_date),
                    str(clone_cut_date),
                    str(veg_transfer_date),
                    str(harvest_date),
                    str(available_date),
                    int(overage_percent),
                    int(post_harvest_days),
                    json.dumps(bench_plans, default=str),
                    json.dumps(strain_summary, default=str),
                    str(created_by or "QCC Reflex User"),
                    now_text,
                ),
            )
        connection.commit()
    return allocation_id


def load_clone_allocations(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent shared clone allocations, newest first."""
    if psycopg is None or not database_url():
        return []
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_clone_allocation_table(cursor)
            cursor.execute(
                "SELECT allocation_id, cycle_name, flower_room, "
                "flower_entry_date, clone_cut_date, veg_transfer_date, "
                "harvest_date, available_date, overage_percent, "
                "post_harvest_days, bench_plans, strain_summary, created_by, "
                "created_at FROM qcc_clone_allocations "
                "ORDER BY created_at DESC LIMIT %s",
                (max(1, min(250, int(limit))),),
            )
            frame = _cursor_frame(cursor)
        connection.commit()
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        bench_plans = record.get("bench_plans") or []
        strain_summary = record.get("strain_summary") or []
        if isinstance(bench_plans, str):
            bench_plans = json.loads(bench_plans)
        if isinstance(strain_summary, str):
            strain_summary = json.loads(strain_summary)
        target_plants = sum(
            int(row.get("target_plants", 0) or 0) for row in strain_summary
        )
        clone_cuts = sum(
            int(row.get("recommended_clones", 0) or 0) for row in strain_summary
        )
        rows.append(
            {
                "allocation_id": str(record.get("allocation_id", "")),
                "cycle_name": str(record.get("cycle_name", "")),
                "flower_room": str(record.get("flower_room", "")),
                "flower_entry_date": str(record.get("flower_entry_date", "")),
                "clone_cut_date": str(record.get("clone_cut_date", "")),
                "veg_transfer_date": str(record.get("veg_transfer_date", "")),
                "harvest_date": str(record.get("harvest_date", "")),
                "available_date": str(record.get("available_date", "")),
                "overage_percent": int(record.get("overage_percent", 30) or 30),
                "post_harvest_days": int(
                    record.get("post_harvest_days", 30) or 30
                ),
                "bench_plans": list(bench_plans),
                "strain_summary": list(strain_summary),
                "strains": ", ".join(
                    str(row.get("strain", "")) for row in strain_summary
                    if str(row.get("strain", "")).strip()
                ),
                "target_plants": target_plants,
                "clone_cuts": clone_cuts,
                "created_by": str(record.get("created_by", "")),
                "created_at": str(record.get("created_at", "")),
            }
        )
    return rows


def _ensure_clone_planning_table(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_clone_plans (
            plan_id TEXT PRIMARY KEY,
            crop TEXT NOT NULL,
            flower_room TEXT NOT NULL,
            clone_cut_date DATE NOT NULL,
            demand_model TEXT NOT NULL,
            status TEXT NOT NULL,
            allocations JSONB NOT NULL,
            bench_assignments JSONB NOT NULL,
            override_reason TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def _ensure_fresh_frozen_adjustments_table(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_crop_fresh_frozen_adjustments (
            adjustment_id TEXT PRIMARY KEY,
            crop TEXT NOT NULL,
            strain TEXT NOT NULL,
            planned_plants INTEGER NOT NULL DEFAULT 0,
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    cursor.execute(
        "ALTER TABLE qcc_crop_fresh_frozen_adjustments "
        "ADD COLUMN IF NOT EXISTS creative_use_lbs DOUBLE PRECISION NOT NULL DEFAULT 0"
    )


def save_fresh_frozen_adjustment(
    *,
    crop: str,
    strain: str,
    planned_plants: int,
    updated_by: str = "QCC Reflex User",
) -> str:
    """Create or replace the planned Fresh Frozen plant count for a crop."""
    if psycopg is None or not database_url():
        raise RuntimeError(
            "A live Supabase connection is required to save Fresh Frozen plans."
        )
    crop_text = str(crop or "").strip()
    strain_text = " ".join(str(strain or "").strip().split())
    if not crop_text or not strain_text:
        raise ValueError("Crop and strain are required for Fresh Frozen planning.")
    plants = max(0, int(planned_plants or 0))
    adjustment_id = "QCC-FF-" + re.sub(
        r"[^A-Za-z0-9]+", "-", f"{crop_text}-{strain_text}"
    ).strip("-").upper()
    now_text = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_fresh_frozen_adjustments_table(cursor)
            cursor.execute(
                "INSERT INTO qcc_crop_fresh_frozen_adjustments "
                "(adjustment_id, crop, strain, planned_plants, updated_by, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (adjustment_id) DO UPDATE SET "
                "planned_plants=EXCLUDED.planned_plants, "
                "updated_by=EXCLUDED.updated_by, updated_at=EXCLUDED.updated_at",
                (
                    adjustment_id, crop_text, strain_text, plants,
                    str(updated_by or "QCC Reflex User"), now_text,
                ),
            )
        connection.commit()
    return adjustment_id


def load_fresh_frozen_adjustments() -> list[dict[str, Any]]:
    """Return every saved planned Fresh Frozen crop/strain adjustment."""
    if psycopg is None or not database_url():
        return []
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_fresh_frozen_adjustments_table(cursor)
            cursor.execute(
                "SELECT adjustment_id, crop, strain, planned_plants, creative_use_lbs, "
                "updated_by, updated_at FROM qcc_crop_fresh_frozen_adjustments "
                "ORDER BY crop, strain"
            )
            frame = _cursor_frame(cursor)
        connection.commit()
    if frame.empty:
        return []
    return [
        {
            "adjustment_id": str(row.get("adjustment_id", "")),
            "crop": str(row.get("crop", "")),
            "strain": str(row.get("strain", "")),
            "planned_plants": int(row.get("planned_plants", 0) or 0),
            "creative_use_lbs": float(row.get("creative_use_lbs", 0) or 0),
            "updated_by": str(row.get("updated_by", "")),
            "updated_at": str(row.get("updated_at", "")),
        }
        for row in frame.to_dict("records")
    ]


def _ensure_metrc_plant_snapshots_table(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_metrc_plant_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            imported_at TIMESTAMPTZ NOT NULL,
            imported_by TEXT NOT NULL,
            source_files JSONB NOT NULL,
            flowering_count INTEGER NOT NULL DEFAULT 0,
            vegetative_count INTEGER NOT NULL DEFAULT 0,
            planting_count INTEGER NOT NULL DEFAULT 0,
            harvest_count INTEGER NOT NULL DEFAULT 0,
            payload_gzip BYTEA NOT NULL
        )
        """
    )


def save_metrc_plant_snapshot(
    snapshot: dict[str, Any], *, imported_by: str = "QCC Reflex User"
) -> str:
    """Persist one normalized four-file Metrc plant snapshot."""
    if psycopg is None or not database_url():
        raise RuntimeError(
            "A live Supabase connection is required to save Metrc plant snapshots."
        )
    snapshot_id = str(snapshot.get("snapshot_id", "")).strip()
    if not snapshot_id:
        raise ValueError("The Metrc plant snapshot has no snapshot ID.")
    summary = dict(snapshot.get("summary") or {})
    payload = gzip.compress(
        json.dumps(snapshot, separators=(",", ":"), default=str).encode("utf-8"),
        compresslevel=6,
    )
    with psycopg.connect(database_url(), connect_timeout=20) as connection:
        with connection.cursor() as cursor:
            _ensure_metrc_plant_snapshots_table(cursor)
            cursor.execute(
                "INSERT INTO qcc_metrc_plant_snapshots "
                "(snapshot_id, imported_at, imported_by, source_files, "
                "flowering_count, vegetative_count, planting_count, harvest_count, "
                "payload_gzip) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s) "
                "ON CONFLICT (snapshot_id) DO UPDATE SET "
                "imported_at=EXCLUDED.imported_at, imported_by=EXCLUDED.imported_by, "
                "source_files=EXCLUDED.source_files, "
                "flowering_count=EXCLUDED.flowering_count, "
                "vegetative_count=EXCLUDED.vegetative_count, "
                "planting_count=EXCLUDED.planting_count, "
                "harvest_count=EXCLUDED.harvest_count, payload_gzip=EXCLUDED.payload_gzip",
                (
                    snapshot_id,
                    snapshot.get("imported_at") or datetime.now().astimezone().isoformat(),
                    str(imported_by or "QCC Reflex User"),
                    json.dumps(snapshot.get("source_files") or {}),
                    int(summary.get("flowering_plants", 0) or 0),
                    int(summary.get("vegetative_plants", 0) or 0),
                    int(summary.get("active_plantings", 0) or 0),
                    int(summary.get("harvest_batches", 0) or 0),
                    payload,
                ),
            )
        connection.commit()
    return snapshot_id


def load_latest_metrc_plant_snapshot() -> dict[str, Any]:
    """Return the newest normalized Metrc plant snapshot from Supabase."""
    if psycopg is None or not database_url():
        return {}
    with psycopg.connect(database_url(), connect_timeout=20) as connection:
        with connection.cursor() as cursor:
            _ensure_metrc_plant_snapshots_table(cursor)
            cursor.execute(
                "SELECT payload_gzip FROM qcc_metrc_plant_snapshots "
                "ORDER BY imported_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
        connection.commit()
    if not row or not row[0]:
        return {}
    raw = bytes(row[0])
    return dict(json.loads(gzip.decompress(raw).decode("utf-8")))


def save_creative_use_adjustment(
    *,
    crop: str,
    strain: str,
    reduction_lbs: float,
    updated_by: str = "QCC Reflex User",
) -> str:
    """Create or replace a dry-pound reduction for blends, co-packing, or other use."""
    if psycopg is None or not database_url():
        raise RuntimeError(
            "A live Supabase connection is required to save Creative Use plans."
        )
    crop_text = str(crop or "").strip()
    strain_text = " ".join(str(strain or "").strip().split())
    if not crop_text or not strain_text:
        raise ValueError("Crop and strain are required for Creative Use planning.")
    pounds = max(0.0, float(reduction_lbs or 0))
    adjustment_id = "QCC-FF-" + re.sub(
        r"[^A-Za-z0-9]+", "-", f"{crop_text}-{strain_text}"
    ).strip("-").upper()
    now_text = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_fresh_frozen_adjustments_table(cursor)
            cursor.execute(
                "INSERT INTO qcc_crop_fresh_frozen_adjustments "
                "(adjustment_id, crop, strain, planned_plants, creative_use_lbs, "
                "updated_by, updated_at) VALUES (%s, %s, %s, 0, %s, %s, %s) "
                "ON CONFLICT (adjustment_id) DO UPDATE SET "
                "creative_use_lbs=EXCLUDED.creative_use_lbs, "
                "updated_by=EXCLUDED.updated_by, updated_at=EXCLUDED.updated_at",
                (
                    adjustment_id, crop_text, strain_text, pounds,
                    str(updated_by or "QCC Reflex User"), now_text,
                ),
            )
        connection.commit()
    return adjustment_id


def save_clone_plan(
    *,
    crop: str,
    flower_room: str,
    clone_cut_date: str,
    demand_model: str,
    status: str,
    allocations: dict[str, float],
    bench_assignments: list[dict[str, Any]] | None = None,
    override_reason: str = "",
    updated_by: str = "QCC Reflex User",
) -> str:
    """Create or update the shared planning record for one crop."""
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save clone plans.")
    crop_text = str(crop or "").strip()
    if not crop_text:
        raise ValueError("A crop is required before saving a clone plan.")
    status_text = str(status or "Draft").strip().title()
    if status_text not in {"Draft", "Approved"}:
        raise ValueError("Clone-plan status must be Draft or Approved.")
    plan_id = "QCC-CP-" + re.sub(r"[^A-Za-z0-9]+", "-", crop_text).strip("-").upper()
    now_text = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_clone_planning_table(cursor)
            cursor.execute(
                "INSERT INTO qcc_clone_plans (plan_id, crop, flower_room, "
                "clone_cut_date, demand_model, status, allocations, "
                "bench_assignments, override_reason, updated_by, updated_at) "
                "VALUES (" + ", ".join(["%s"] * 11) + ") "
                "ON CONFLICT (plan_id) DO UPDATE SET flower_room=EXCLUDED.flower_room, "
                "clone_cut_date=EXCLUDED.clone_cut_date, demand_model=EXCLUDED.demand_model, "
                "status=EXCLUDED.status, allocations=EXCLUDED.allocations, "
                "bench_assignments=EXCLUDED.bench_assignments, "
                "override_reason=EXCLUDED.override_reason, updated_by=EXCLUDED.updated_by, "
                "updated_at=EXCLUDED.updated_at",
                (
                    plan_id, crop_text, str(flower_room), str(clone_cut_date),
                    str(demand_model), status_text,
                    json.dumps(allocations, default=str),
                    json.dumps(bench_assignments or [], default=str),
                    str(override_reason or ""), str(updated_by or "QCC Reflex User"),
                    now_text,
                ),
            )
        connection.commit()
    return plan_id


def load_clone_plans() -> list[dict[str, Any]]:
    """Return every saved clone plan, newest first, for unrestricted lookback."""
    if psycopg is None or not database_url():
        return []
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_clone_planning_table(cursor)
            cursor.execute(
                "SELECT plan_id, crop, flower_room, clone_cut_date, demand_model, "
                "status, allocations, bench_assignments, override_reason, "
                "updated_by, updated_at FROM qcc_clone_plans "
                "ORDER BY clone_cut_date DESC, updated_at DESC"
            )
            frame = _cursor_frame(cursor)
        connection.commit()
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        allocations = record.get("allocations") or {}
        bench_assignments = record.get("bench_assignments") or []
        if isinstance(allocations, str):
            allocations = json.loads(allocations)
        if isinstance(bench_assignments, str):
            bench_assignments = json.loads(bench_assignments)
        rows.append(
            {
                "plan_id": str(record.get("plan_id", "")),
                "crop": str(record.get("crop", "")),
                "flower_room": str(record.get("flower_room", "")),
                "clone_cut_date": str(record.get("clone_cut_date", "")),
                "demand_model": str(record.get("demand_model", "")),
                "status": str(record.get("status", "Draft")),
                "allocations": dict(allocations),
                "bench_assignments": list(bench_assignments),
                "bench_equivalents": round(
                    sum(float(value or 0) for value in allocations.values()), 1
                ),
                "strains": len(
                    [value for value in allocations.values() if float(value or 0) > 0]
                ),
                "allocation_benches": ", ".join(
                    f"{strain}: {float(value):.1f}"
                    for strain, value in allocations.items()
                    if float(value or 0) > 0
                ) or "No benches assigned",
                "override_reason": str(record.get("override_reason", "")),
                "updated_by": str(record.get("updated_by", "")),
                "updated_at": str(record.get("updated_at", "")),
            }
        )
    return rows


def create_reflex_production_plan(
    *,
    plan_name: str,
    output_brand: str,
    strain: str,
    target_sku_type: str,
    recipe_type: str,
    target_packaging_date: str,
    status: str,
    batch_weight_grams: float,
    selected_tags: list[str],
    outputs: list[dict[str, Any]],
    process_loss_percent: float,
    overfill_percent: float,
    qa_retention_grams: float,
    formulation_details: dict[str, Any],
    notes: str,
    created_by: str = "QCC Reflex User",
    plan_id: str = "",
    assigned_department: str = "Production",
    production_line: str = "Flower Line 1",
) -> str:
    """Atomically validate and create or replace a production plan."""
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save plans.")
    editing = bool(str(plan_id or "").strip())
    if status not in (
        PRODUCTION_PLAN_STATUSES if editing else {"Planned", "Committed"}
    ):
        raise ValueError(
            "Unknown production-plan status." if editing
            else "New plans must begin as Planned or Committed."
        )
    if not str(plan_name).strip():
        raise ValueError("Enter a production plan name.")
    if production_line not in PRODUCTION_LINE_OPTIONS:
        raise ValueError("Select one of the four approved production lines.")
    batch = float(batch_weight_grams or 0)
    if batch <= 0:
        raise ValueError("Batch weight must be greater than zero.")
    tags = list(dict.fromkeys(str(tag).strip() for tag in selected_tags if str(tag).strip()))
    if not tags:
        raise ValueError("Select at least one compatible source lot.")
    valid_outputs = [
        dict(output) for output in outputs
        if float(output.get("allocated_weight_grams", 0) or 0) > 0
    ]
    if not valid_outputs:
        raise ValueError("The plan must contain at least one finished output.")

    now_text = datetime.now().astimezone().isoformat()
    seed = json.dumps({
        "name": plan_name, "strain": strain, "sku": target_sku_type,
        "created": now_text, "by": created_by,
    }, sort_keys=True)
    plan_id = str(plan_id or "").strip() or (
        "QCC-PP-" + datetime.now().strftime("%Y%m%d-")
        + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()
    )

    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            # Serialize the short validation-and-insert section so two online
            # planners cannot commit the same package weight simultaneously.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("qcc-production-planning",),
            )
            cursor.execute(
                "ALTER TABLE production_plans ADD COLUMN IF NOT EXISTS "
                "production_line TEXT NOT NULL DEFAULT 'Unassigned'"
            )
            cursor.execute(
                "SELECT snapshot_id FROM inventory_snapshots "
                "WHERE status = 'Published' ORDER BY published_at DESC LIMIT 1"
            )
            latest = cursor.fetchone()
            if not latest:
                raise ValueError("No published inventory snapshot is available.")
            cursor.execute(
                "SELECT * FROM inventory_snapshot_packages "
                "WHERE snapshot_id = %s AND package_tag = ANY(%s)",
                (latest[0], tags),
            )
            packages = _cursor_frame(cursor)
            cursor.execute(
                "SELECT plan_id, status FROM production_plans "
                "WHERE status = ANY(%s) OR plan_id = %s",
                (list(ACTIVE_PRODUCTION_STATUSES), plan_id),
            )
            plans = _cursor_frame(cursor)
            cursor.execute(
                "SELECT plan_id, package_tag, allocated_weight_grams "
                "FROM production_plan_sources WHERE package_tag = ANY(%s)",
                (tags,),
            )
            sources = _cursor_frame(cursor)
            existing_plan = pd.DataFrame()
            if editing:
                cursor.execute(
                    "SELECT * FROM production_plans WHERE plan_id = %s LIMIT 1",
                    (plan_id,),
                )
                existing_plan = _cursor_frame(cursor)
                if existing_plan.empty:
                    raise ValueError("The production plan was not found.")
                validation_plans = plans[
                    ~plans["plan_id"].astype(str).eq(plan_id)
                ].copy()
                validation_sources = sources[
                    ~sources["plan_id"].astype(str).eq(plan_id)
                ].copy()
            else:
                validation_plans = plans
                validation_sources = sources
            available = available_wip_inventory(
                packages, validation_plans, validation_sources
            )
            eligible = potential_wip_for_sku(
                available, output_brand, strain, target_sku_type
            )
            selected = eligible[
                eligible["package_tag"].astype(str).isin(tags)
            ].copy()
            selected["available_weight_grams"] = pd.to_numeric(
                selected["available_weight_grams"], errors="coerce"
            ).fillna(0)
            source_commitments: list[tuple[dict[str, Any], float]] = []
            remaining = batch
            order = {tag: position for position, tag in enumerate(tags)}
            selected["tag_order"] = selected["package_tag"].astype(str).map(order)
            selected = selected.sort_values("tag_order")
            for source in selected.to_dict("records"):
                committed = min(
                    float(source.get("available_weight_grams", 0) or 0), remaining
                )
                if committed > 0:
                    source_commitments.append((source, committed))
                    remaining -= committed
                if remaining <= 0.001:
                    break
            if remaining > 0.001:
                raise ValueError(
                    "The selected tags no longer contain enough available WIP. "
                    "Refresh and reduce the batch weight."
                )

            percentage_by_sku = {
                str(output.get("sku_type", "")): float(
                    output.get("allocation_percent", 0) or 0
                ) for output in valid_outputs
            }
            units_by_sku = {
                str(output.get("sku_type", "")): int(
                    output.get("projected_units", 0) or 0
                ) for output in valid_outputs
            }
            plan_columns = [
                "plan_id", "plan_name", "output_brand", "strain",
                "target_packaging_date", "status", "batch_weight_grams",
                "flower_35_percent", "flower_7_percent", "flower_1_percent",
                "smalls_shake_percent", "loss_percent", "projected_35_units",
                "projected_7_units", "projected_1_units",
                "projected_smalls_grams", "projected_loss_grams", "notes",
                "created_by", "created_at", "updated_at", "target_sku_type",
                "recipe_type", "projected_output_units",
                "unit_fill_weight_grams", "process_loss_percent",
                "overfill_percent", "qa_retention_grams",
                "formulation_details", "assigned_department",
                "production_line",
            ]
            values = [
                plan_id, str(plan_name).strip(), str(output_brand).strip(),
                str(strain).strip(), str(target_packaging_date), status, batch,
                percentage_by_sku.get("3.5g Flower", 0),
                percentage_by_sku.get("7g Flower", 0),
                percentage_by_sku.get("1g Flower", 0),
                float(formulation_details.get("smalls_shake_percent", 0) or 0),
                float(process_loss_percent or 0),
                units_by_sku.get("3.5g Flower", 0),
                units_by_sku.get("7g Flower", 0),
                units_by_sku.get("1g Flower", 0),
                batch * float(formulation_details.get("smalls_shake_percent", 0) or 0) / 100,
                batch * float(process_loss_percent or 0) / 100,
                str(notes or "").strip(), str(created_by or "QCC Reflex User"),
                now_text, now_text, str(target_sku_type).strip(), recipe_type,
                sum(int(output.get("projected_units", 0) or 0) for output in valid_outputs),
                float(valid_outputs[0].get("unit_weight_grams", 0) or 0),
                float(process_loss_percent or 0), float(overfill_percent or 0),
                float(qa_retention_grams or 0),
                json.dumps(formulation_details or {}, sort_keys=True),
                str(assigned_department or "Production").strip() or "Production",
                production_line,
            ]
            if editing:
                previous_values = existing_plan.iloc[0].to_dict()
                old_created_by = previous_values.get("created_by", created_by)
                old_created_at = previous_values.get("created_at", now_text)
                values[18] = old_created_by
                values[19] = old_created_at
                update_columns = [
                    column for column in plan_columns
                    if column not in {"plan_id", "created_by", "created_at"}
                ]
                value_map = dict(zip(plan_columns, values))
                cursor.execute(
                    "UPDATE production_plans SET "
                    + ", ".join(f"{column} = %s" for column in update_columns)
                    + " WHERE plan_id = %s",
                    [value_map[column] for column in update_columns] + [plan_id],
                )
                cursor.execute(
                    "DELETE FROM production_plan_sources WHERE plan_id = %s",
                    (plan_id,),
                )
                cursor.execute(
                    "DELETE FROM production_plan_outputs WHERE plan_id = %s",
                    (plan_id,),
                )
            else:
                cursor.execute(
                    "INSERT INTO production_plans (" + ", ".join(plan_columns)
                    + ") VALUES (" + ", ".join(["%s"] * len(plan_columns)) + ")",
                    values,
                )
            source_rows = []
            for source, committed in source_commitments:
                allocation_seed = f"{plan_id}|{source['package_tag']}"
                source_rows.append((
                    "QCC-PPA-" + hashlib.sha256(
                        allocation_seed.encode("utf-8")
                    ).hexdigest()[:14].upper(),
                    plan_id, str(source.get("package_tag", "")),
                    str(source.get("item", "") or ""),
                    str(source.get("source_harvest", "") or ""),
                    str(source.get("location", "") or ""),
                    str(source.get("qa_status", "") or ""),
                    float(source.get("calculated_weight_grams", 0) or 0),
                    committed, now_text,
                    str(source.get("wip_component", "Primary") or "Primary"),
                ))
            cursor.executemany(
                "INSERT INTO production_plan_sources ("
                "source_allocation_id, plan_id, package_tag, item, "
                "source_harvest, location, qa_status, starting_weight_grams, "
                "allocated_weight_grams, created_at, wip_component) "
                "VALUES (" + ", ".join(["%s"] * 11) + ")",
                source_rows,
            )
            output_rows = []
            for position, output in enumerate(valid_outputs):
                output_seed = (
                    f"{plan_id}|{position}|{output.get('brand')}|"
                    f"{output.get('sku_type')}"
                )
                output_rows.append((
                    "QCC-PPO-" + hashlib.sha256(
                        output_seed.encode("utf-8")
                    ).hexdigest()[:14].upper(),
                    plan_id, str(output.get("brand", output_brand)),
                    str(output.get("strain", strain)),
                    str(output.get("sku_type", target_sku_type)),
                    float(output.get("allocation_percent", 0) or 0),
                    float(output.get("allocated_weight_grams", 0) or 0),
                    int(output.get("projected_units", 0) or 0),
                    float(output.get("unit_weight_grams", 0) or 0), now_text,
                ))
            cursor.executemany(
                "INSERT INTO production_plan_outputs ("
                "output_id, plan_id, brand, strain, sku_type, allocation_percent, "
                "allocated_weight_grams, projected_units, unit_weight_grams, "
                "created_at) VALUES (" + ", ".join(["%s"] * 10) + ")",
                output_rows,
            )
            if editing:
                audit_seed = f"{plan_id}|Plan Rebuilt|{now_text}|{created_by}"
                audit_values = (
                    "QCC-PPAUD-" + hashlib.sha256(
                        audit_seed.encode("utf-8")
                    ).hexdigest()[:14].upper(),
                    plan_id,
                    "Plan Rebuilt",
                    json.dumps(previous_values, default=str, sort_keys=True),
                    json.dumps({
                        "plan_name": str(plan_name).strip(),
                        "status": status,
                        "target_packaging_date": str(target_packaging_date),
                        "batch_weight_grams": batch,
                        "source_tags": tags,
                        "outputs": valid_outputs,
                    }, default=str, sort_keys=True),
                    str(created_by or "QCC Reflex User"),
                    now_text,
                )
                cursor.execute(
                    "INSERT INTO production_plan_audit (audit_id, plan_id, "
                    "action, previous_values, new_values, changed_by, changed_at) "
                    "VALUES (" + ", ".join(["%s"] * 7) + ")",
                    audit_values,
                )
        connection.commit()
    _invalidate_dashboard_caches()
    return plan_id


def delete_reflex_production_plans(
    plan_ids: list[str], deleted_by: str = "QCC Reflex User"
) -> list[str]:
    """Delete selected internal plans in one transaction and release their WIP."""
    del deleted_by  # Reserved for a future non-blocking deletion audit record.
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to delete plans.")
    clean_ids = list(dict.fromkeys(
        str(plan_id or "").strip() for plan_id in plan_ids
        if str(plan_id or "").strip()
    ))
    if not clean_ids:
        raise ValueError("Select at least one production plan to delete.")
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("qcc-production-planning",),
            )
            cursor.execute(
                "DELETE FROM production_plan_audit WHERE plan_id = ANY(%s)",
                (clean_ids,),
            )
            cursor.execute(
                "DELETE FROM production_plan_sources WHERE plan_id = ANY(%s)",
                (clean_ids,),
            )
            cursor.execute(
                "DELETE FROM production_plan_outputs WHERE plan_id = ANY(%s)",
                (clean_ids,),
            )
            cursor.execute(
                "DELETE FROM production_plans WHERE plan_id = ANY(%s) "
                "RETURNING plan_id",
                (clean_ids,),
            )
            existing_ids = [str(row[0]) for row in cursor.fetchall()]
            if not existing_ids:
                raise ValueError("The selected production plans were not found.")
        connection.commit()
    _remove_deleted_plans_from_caches(existing_ids)
    return existing_ids


def delete_reflex_production_plan(
    plan_id: str, deleted_by: str = "QCC Reflex User"
) -> None:
    """Backward-compatible single-plan delete."""
    delete_reflex_production_plans([plan_id], deleted_by)


def create_reflex_production_template(
    plan_id: str,
    template_name: str,
    created_by: str = "QCC Reflex User",
) -> str:
    """Create a reusable formulation template without source commitments."""
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save templates.")
    clean_id = str(plan_id or "").strip()
    clean_name = str(template_name or "").strip()
    if not clean_id or not clean_name:
        raise ValueError("A source plan and template name are required.")
    now_text = datetime.now().astimezone().isoformat()
    template_id = "QCC-PPT-" + hashlib.sha256(
        f"{clean_id}|{clean_name}|{now_text}".encode("utf-8")
    ).hexdigest()[:14].upper()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS reflex_production_templates (
                    template_id TEXT PRIMARY KEY,
                    template_name TEXT NOT NULL,
                    source_plan_id TEXT NOT NULL,
                    target_brand TEXT NOT NULL,
                    strain TEXT NOT NULL,
                    target_sku_type TEXT NOT NULL,
                    recipe_type TEXT NOT NULL,
                    template_details TEXT NOT NULL,
                    outputs_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                "SELECT * FROM production_plans WHERE plan_id = %s",
                (clean_id,),
            )
            plan_frame = _cursor_frame(cursor)
            if plan_frame.empty:
                raise ValueError("The source production plan was not found.")
            cursor.execute(
                "SELECT brand, strain, sku_type, allocation_percent, "
                "unit_weight_grams FROM production_plan_outputs "
                "WHERE plan_id = %s ORDER BY unit_weight_grams DESC",
                (clean_id,),
            )
            output_frame = _cursor_frame(cursor)
            plan = plan_frame.iloc[0].to_dict()
            details = {
                "batch_weight_grams": float(plan.get("batch_weight_grams", 0) or 0),
                "smalls_shake_percent": float(plan.get("smalls_shake_percent", 0) or 0),
                "loss_percent": float(plan.get("loss_percent", 0) or 0),
                "process_loss_percent": float(plan.get("process_loss_percent", 0) or 0),
                "overfill_percent": float(plan.get("overfill_percent", 0) or 0),
                "qa_retention_grams": float(plan.get("qa_retention_grams", 0) or 0),
                "unit_fill_weight_grams": float(plan.get("unit_fill_weight_grams", 0) or 0),
                "assigned_department": str(
                    plan.get("assigned_department", "Production") or "Production"
                ),
                "formulation_details": plan.get("formulation_details", "{}") or "{}",
            }
            cursor.execute(
                "INSERT INTO reflex_production_templates (template_id, "
                "template_name, source_plan_id, target_brand, strain, "
                "target_sku_type, recipe_type, template_details, outputs_json, "
                "created_by, created_at, updated_at) VALUES ("
                + ", ".join(["%s"] * 12) + ")",
                (
                    template_id, clean_name, clean_id,
                    str(plan.get("output_brand", "") or ""),
                    str(plan.get("strain", "") or ""),
                    str(plan.get("target_sku_type", "") or ""),
                    str(plan.get("recipe_type", "") or ""),
                    json.dumps(details, default=str, sort_keys=True),
                    json.dumps(output_frame.to_dict("records"), default=str),
                    str(created_by or "QCC Reflex User"), now_text, now_text,
                ),
            )
        connection.commit()
    _invalidate_dashboard_caches()
    return template_id


def load_reflex_production_templates() -> pd.DataFrame:
    """Load reusable Reflex production templates when the table exists."""
    return safe_query_frame(
        "SELECT * FROM reflex_production_templates ORDER BY template_name"
    )


def demand_status(current_units: float, average_weekly_units: float) -> str:
    if average_weekly_units <= 0:
        return "Insufficient Demand History"
    if current_units <= 0:
        return "Current Stockout"
    weeks = current_units / average_weekly_units
    if weeks < 2:
        return "Immediate Production Needed"
    if weeks < 4:
        return "Stockout Risk Within 4 Weeks"
    if weeks < 8:
        return "Replenishment Watch"
    return "Adequately Stocked"


def format_weight(grams: Any) -> str:
    value = native_number(grams, 1)
    if value >= 453.59237:
        return f"{value / 453.59237:,.2f} lb"
    return f"{value:,.1f} g"


def apply_inventory_master(
    analysis: pd.DataFrame, inventory_skus: pd.DataFrame
) -> pd.DataFrame:
    """Use unambiguous current SKU masters to unify historical attribution."""
    if analysis.empty or inventory_skus.empty:
        return analysis
    # Compact Sales snapshots already contain mapped attribution. Remove any
    # temporary mapping columns before applying the current inventory master
    # so pandas cannot create mapped_brand_x / mapped_brand_y collisions.
    result = analysis.copy().drop(
        columns=[
            "strain_key", "sku_key", "mapped_brand", "mapped_strain",
            "mapped_sku", "mapped_sku_type",
        ],
        errors="ignore",
    )
    master = inventory_skus[["brand", "strain", "sku_type"]].copy()
    master["strain_key"] = (
        master["strain"].fillna("").astype(str).str.lower().str.strip()
    )
    master["sku_key"] = (
        master["sku_type"].fillna("").astype(str).str.lower().str.strip()
    )
    unique = (
        master.groupby(["strain_key", "sku_key"], dropna=False)
        .agg(
            brand_count=("brand", "nunique"),
            mapped_brand=("brand", "first"),
            mapped_strain=("strain", "first"),
            mapped_sku=("sku_type", "first"),
        )
        .reset_index()
    )
    unique = unique[unique["brand_count"].eq(1)]
    result["strain_key"] = (
        result["strain"].fillna("").astype(str).str.lower().str.strip()
    )
    result["sku_key"] = (
        result["sku_type"].fillna("").astype(str).str.lower().str.strip()
    )
    result = result.merge(
        unique[[
            "strain_key", "sku_key", "mapped_brand", "mapped_strain",
            "mapped_sku",
        ]],
        on=["strain_key", "sku_key"],
        how="left",
    )
    mapped = result["mapped_brand"].notna()
    result.loc[mapped, "brand"] = result.loc[mapped, "mapped_brand"]
    result.loc[mapped, "strain"] = result.loc[mapped, "mapped_strain"]
    result.loc[mapped, "sku_type"] = result.loc[mapped, "mapped_sku"]
    result.loc[
        mapped, "brand_attribution_reason"
    ] = "Matched to latest inventory SKU master"
    return result.drop(
        columns=[
            "strain_key", "sku_key", "mapped_brand", "mapped_strain",
            "mapped_sku",
        ],
        errors="ignore",
    )


def build_committed_wip_summary(
    plans: pd.DataFrame, outputs: pd.DataFrame, sources: pd.DataFrame
) -> pd.DataFrame:
    columns = ["brand", "strain", "sku_type", "committed_weight_grams"]
    if plans.empty or sources.empty:
        return pd.DataFrame(columns=columns)
    active = plans[
        plans.get("status", pd.Series(dtype=str)).isin(
            ["Planned", "Committed", "In Production"]
        )
    ].copy()
    if active.empty:
        return pd.DataFrame(columns=columns)
    source_totals = (
        sources.groupby("plan_id", dropna=False)["allocated_weight_grams"]
        .sum()
        .to_dict()
    )
    rows: list[dict[str, Any]] = []
    output_groups = {
        str(plan_id): group
        for plan_id, group in outputs.groupby("plan_id")
    } if not outputs.empty else {}
    for plan in active.to_dict("records"):
        plan_id = str(plan.get("plan_id", ""))
        plan_source_weight = native_number(source_totals.get(plan_id), 3)
        plan_outputs = output_groups.get(plan_id)
        if plan_outputs is not None and not plan_outputs.empty:
            batch_weight = native_number(plan.get("batch_weight_grams"), 3)
            for output in plan_outputs.to_dict("records"):
                output_weight = native_number(
                    output.get("allocated_weight_grams"), 3
                )
                committed = (
                    plan_source_weight * output_weight / batch_weight
                    if batch_weight > 0 else output_weight
                )
                rows.append({
                    "brand": output.get("brand", plan.get("output_brand", "")),
                    "strain": output.get("strain", plan.get("strain", "")),
                    "sku_type": output.get(
                        "sku_type", plan.get("target_sku_type", "")
                    ),
                    "committed_weight_grams": committed,
                })
            continue
        for sku_type, field in [
            ("3.5g Flower", "flower_35_percent"),
            ("7g Flower", "flower_7_percent"),
            ("1g Flower", "flower_1_percent"),
        ]:
            percent = native_number(plan.get(field), 3)
            if percent > 0:
                rows.append({
                    "brand": plan.get("output_brand", ""),
                    "strain": plan.get("strain", ""),
                    "sku_type": sku_type,
                    "committed_weight_grams": plan_source_weight * percent / 100,
                })
    if not rows:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(rows)
        .groupby(["brand", "strain", "sku_type"], dropna=False)
        ["committed_weight_grams"].sum()
        .reset_index()
    )


def native_number(value: Any, decimals: int = 2) -> float:
    """Return one finite Python number, treating blank/NaN/Infinity as zero."""
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return 0.0
    try:
        finite_number = float(number)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(finite_number):
        return 0.0
    return round(finite_number, decimals)


def iso_date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.strftime("%Y-%m-%d") if pd.notna(parsed) else ""


def _batch_date_candidates(value: Any, as_of: date) -> list[pd.Timestamp]:
    """Extract plausible manufacturing dates embedded in Metrc batch text."""
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    earliest = pd.Timestamp("2020-01-01")
    latest = pd.Timestamp(as_of) + pd.Timedelta(days=366)
    parsed: list[pd.Timestamp] = []

    def add(year: int, month: int, day: int) -> bool:
        try:
            candidate = pd.Timestamp(year=year, month=month, day=day)
        except ValueError:
            return False
        if not earliest <= candidate <= latest:
            return False
        parsed.append(candidate)
        return True

    for month, day, year in re.findall(
        r"(0?[1-9]|1[0-2])[._/-](0?[1-9]|[12]\d|3[01])"
        r"[._/-]((?:19|20)\d{2})(?!\d)",
        text,
    ):
        full_year = int(year) + (2000 if len(year) == 2 else 0)
        add(full_year, int(month), int(day))

    for compact in re.findall(r"(?<!\d)(\d{6})(?!\d)", text):
        first_two = int(compact[:2])
        yymmdd = (2000 + first_two, int(compact[2:4]), int(compact[4:6]))
        mmddyy = (2000 + int(compact[4:6]), first_two, int(compact[2:4]))
        candidates = [yymmdd, mmddyy] if 20 <= first_two <= 39 else [mmddyy, yymmdd]
        for year, month, day in candidates:
            if add(year, month, day):
                break

    return sorted(set(parsed))


def repair_manufacturing_inventory_ages(
    packages: pd.DataFrame, as_of: date | None = None
) -> pd.DataFrame:
    """Validate manufactured-product age against the two Metrc batch fields."""
    if packages.empty or "source_license_type" not in packages.columns:
        return packages
    result = packages.copy()
    as_of = as_of or date.today()
    for column in [
        "source_production_batch", "production_batch_number",
        "production_date_source", "production_date", "aging_start_date",
        "inventory_age_days", "days_remaining_in_sale_window",
    ]:
        if column not in result.columns:
            result[column] = pd.NA

    manufacturing = result["source_license_type"].fillna("").astype(str).str.contains(
        "manufactur", case=False, regex=False
    )
    for index, row in result.loc[manufacturing].iterrows():
        batch_date = pd.NaT
        date_source = "Date Needs Review"
        for column, label in [
            ("source_production_batch", "Source Production Batch"),
            ("production_batch_number", "Production Batch Number"),
        ]:
            candidates = _batch_date_candidates(row.get(column), as_of)
            if candidates:
                batch_date = candidates[-1]
                date_source = label
                break

        if pd.isna(batch_date):
            stored_date = pd.to_datetime(row.get("production_date"), errors="coerce")
            if pd.notna(stored_date):
                batch_date = stored_date.normalize()
                date_source = str(
                    row.get("production_date_source") or "Published Production Date"
                )

        if pd.isna(batch_date):
            result.at[index, "inventory_age_days"] = None
            result.at[index, "days_remaining_in_sale_window"] = None
            result.at[index, "production_date_source"] = "Date Needs Review"
            continue

        age_days = (pd.Timestamp(as_of) - batch_date.normalize()).days
        batch_date_text = batch_date.strftime("%Y-%m-%d")
        result.at[index, "production_date"] = batch_date_text
        result.at[index, "aging_start_date"] = batch_date_text
        result.at[index, "inventory_age_days"] = age_days
        result.at[index, "production_date_source"] = date_source
        if "180 Days" in str(row.get("aging_policy", "")) or str(
            row.get("production_stage", "")
        ) in {"Packaged Goods", "Failed - On Hold"}:
            result.at[index, "days_remaining_in_sale_window"] = 180 - age_days

        # A published snapshot can retain the original missing-date review
        # flag even after one of the two Metrc batch fields supplies a valid
        # production date. Clear only that stale reason, preserving every
        # independent review issue on the package.
        if "review_reason" in result.columns and "needs_review" in result.columns:
            reasons = [
                reason.strip()
                for reason in str(row.get("review_reason", "")).split(";")
                if reason.strip()
            ]
            stale_reason = "Manufacturing production date needs review"
            if stale_reason in reasons:
                remaining_reasons = [
                    reason for reason in reasons if reason != stale_reason
                ]
                result.at[index, "review_reason"] = "; ".join(remaining_reasons)
                if not remaining_reasons:
                    result.at[index, "needs_review"] = 0
    return result


def promote_legitimate_manufacturing_samples(
    packages: pd.DataFrame,
) -> pd.DataFrame:
    """Treat passed manufacturing samples as CPG unless another issue exists."""
    if packages.empty or "item" not in packages.columns:
        return packages
    result = packages.copy()
    for column, default in [
        ("review_reason", ""),
        ("needs_review", False),
        ("production_stage", ""),
        ("qa_status", ""),
        ("aging_start_date", pd.NA),
        ("inventory_age_days", pd.NA),
        ("is_finished_retail_sku", False),
        ("include_in_cpg", False),
        ("is_retention_sample", False),
    ]:
        if column not in result.columns:
            result[column] = default

    if "source_license_type" in result.columns:
        license_type = result["source_license_type"].fillna("").astype(str)
    elif "license_type" in result.columns:
        license_type = result["license_type"].fillna("").astype(str)
    else:
        return packages

    sample_rows = (
        license_type.str.contains("manufactur", case=False, regex=False)
        & result["item"].fillna("").astype(str).str.contains(
            r"\bsamples?\b", case=False, regex=True
        )
    )
    for index, row in result.loc[sample_rows].iterrows():
        if str(row.get("qa_status", "")).strip() != "Test Passed":
            continue

        reasons = [
            reason.strip()
            for reason in str(row.get("review_reason", "")).split(";")
            if reason.strip()
        ]
        blocking_reasons = []
        for reason in reasons:
            if reason == "Production stage unclear":
                continue
            if (
                reason == "Manufacturing production date needs review"
                and pd.notna(pd.to_datetime(
                    row.get("aging_start_date"), errors="coerce"
                ))
            ):
                continue
            blocking_reasons.append(reason)

        age = pd.to_numeric(row.get("inventory_age_days"), errors="coerce")
        if pd.isna(pd.to_datetime(row.get("aging_start_date"), errors="coerce")):
            blocking_reasons.append("Manufacturing production date needs review")
        if pd.notna(age) and age < 0:
            blocking_reasons.append("Negative inventory age")
        if bool(row.get("needs_review")) and not reasons:
            blocking_reasons.append("Unspecified review issue")
        if blocking_reasons:
            continue

        result.at[index, "production_stage"] = "Packaged Goods"
        # Supabase returns these eligibility flags as integer 0/1 columns.
        # Preserve that dtype so pandas does not reject boolean assignments.
        result.at[index, "is_finished_retail_sku"] = 1
        result.at[index, "include_in_cpg"] = 1
        result.at[index, "is_retention_sample"] = 0
        result.at[index, "needs_review"] = 0
        result.at[index, "review_reason"] = ""
    return result


def record_list(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.copy().replace({pd.NA: None})
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[column]):
            clean[column] = clean[column].dt.strftime("%Y-%m-%d")
    return clean.astype(object).where(pd.notna(clean), None).to_dict("records")


def build_velocity(
    demand: pd.DataFrame,
    inventory_skus: pd.DataFrame,
    inventory_packages: pd.DataFrame,
    plans: pd.DataFrame,
    outputs: pd.DataFrame,
    sources: pd.DataFrame,
    all_history_demand: pd.DataFrame | None = None,
    period_days: int | None = None,
    include_potential_wip: bool = True,
) -> pd.DataFrame:
    columns = [
        "Brand", "Strain", "SKU Type", "Units Shipped",
        "Avg Weekly Units", "Avg Weekly Units - Last 30 Days", "Packages",
        "Current Units", "Weeks of Supply", "Potential Matching WIP",
        "Potential WIP Summary", "Committed WIP", "Matching Pre-WIP Weight", "Customers",
        "Demand Status", "Last Shipped", "Days Since Last Shipment",
        "Lifecycle Status", "Lifecycle Reason",
    ]
    history = all_history_demand if all_history_demand is not None else demand
    if history.empty:
        return pd.DataFrame(columns=columns)
    history_span = history.groupby(
        ["brand", "strain", "sku_type"], dropna=False
    ).agg(
        first_shipped=("created_at", "min"),
        history_last_shipped=("created_at", "max"),
    ).reset_index()
    grouped = demand.groupby(
        ["brand", "strain", "sku_type"], dropna=False
    ).agg(
        units_shipped=("shipped_units", "sum"),
        customers=("destination_license", "nunique"),
        last_shipped=("created_at", "max"),
    ).reset_index()
    grouped = history_span.merge(
        grouped, on=["brand", "strain", "sku_type"], how="left"
    )
    grouped["units_shipped"] = pd.to_numeric(
        grouped["units_shipped"], errors="coerce"
    ).fillna(0)
    grouped["customers"] = pd.to_numeric(
        grouped["customers"], errors="coerce"
    ).fillna(0)
    history_end = pd.Timestamp(history["created_at"].max()).date()
    period_start = (
        history_end - timedelta(days=period_days - 1)
        if period_days else None
    )
    grouped["velocity_days"] = grouped["first_shipped"].apply(
        lambda start: max(
            (
                history_end
                - max(
                    pd.Timestamp(start).date(),
                    period_start or pd.Timestamp(start).date(),
                )
            ).days + 1,
            1,
        )
    )
    grouped["velocity_weeks"] = grouped["velocity_days"] / 7
    grouped["average_weekly_units"] = (
        grouped["units_shipped"] / grouped["velocity_weeks"]
    )
    grouped["average_weekly_units_last_30"] = (
        grouped["average_weekly_units"] if period_days == 30 else 0.0
    )
    if inventory_skus.empty:
        grouped["current_units"] = 0.0
    else:
        inventory = inventory_skus.copy()
        inventory["on_hand_units"] = pd.to_numeric(
            inventory["on_hand_units"], errors="coerce"
        ).fillna(0)
        inventory["package_count"] = pd.to_numeric(
            inventory.get("package_count", 0), errors="coerce"
        ).fillna(0)
        inventory = inventory.groupby(
            ["brand", "strain", "sku_type"], dropna=False
        ).agg(
            current_units=("on_hand_units", "sum"),
            packages=("package_count", "sum"),
        ).reset_index()
        grouped = grouped.merge(
            inventory, on=["brand", "strain", "sku_type"], how="left"
        )
        grouped["current_units"] = grouped["current_units"].fillna(0)
        grouped["packages"] = grouped["packages"].fillna(0)
    if "packages" not in grouped:
        grouped["packages"] = 0
    grouped["weeks_of_supply"] = (
        grouped["current_units"]
        / grouped["average_weekly_units"].replace(0, pd.NA)
    )
    grouped["demand_status"] = grouped.apply(
        lambda row: demand_status(
            float(row["current_units"]), float(row["average_weekly_units"])
        ),
        axis=1,
    )
    committed = build_committed_wip_summary(plans, outputs, sources)
    grouped = grouped.merge(
        committed,
        on=["brand", "strain", "sku_type"],
        how="left",
    )
    grouped["committed_weight_grams"] = grouped[
        "committed_weight_grams"
    ].fillna(0)
    grouped["days_since_last_shipment"] = grouped["history_last_shipped"].apply(
        lambda shipped: (
            max((history_end - pd.Timestamp(shipped).date()).days, 0)
            if pd.notna(shipped) else 99999
        )
    )

    def lifecycle(row: pd.Series) -> tuple[str, str]:
        key = tuple(
            str(row.get(column, "") or "").strip().lower()
            for column in ("brand", "strain", "sku_type")
        )
        override = PRODUCT_LIFECYCLE_OVERRIDES.get(key)
        if override:
            return override, "Manual Sales lifecycle override"
        if str(row.get("strain", "") or "").strip().lower() in RETIRED_OR_ON_HOLD_STRAINS:
            return "Retired", "Manually retired or placed on hold"
        if (
            float(row.get("current_units", 0) or 0) > 0
            or float(row.get("committed_weight_grams", 0) or 0) > 0
        ):
            return "Active", "Current inventory or an active production commitment exists"
        raw_days = row.get("days_since_last_shipment", 99999)
        days = int(raw_days) if pd.notna(raw_days) else 99999
        if days <= 90:
            return "Active", "Shipped to a customer within 90 days"
        if days <= 180:
            return "Dormant", "Last customer shipment was 91-180 days ago"
        return "Retirement Candidate", "No inventory or plan and no customer shipment for more than 180 days"

    lifecycle_values = grouped.apply(lifecycle, axis=1)
    grouped["lifecycle_status"] = lifecycle_values.map(lambda value: value[0])
    grouped["lifecycle_reason"] = lifecycle_values.map(lambda value: value[1])
    if not include_potential_wip:
        grouped["potential_matching_wip"] = ""
        grouped["potential_wip_summary"] = ""
        grouped["committed_wip"] = grouped[
            "committed_weight_grams"
        ].apply(format_weight)
        grouped["matching_pre_wip_weight"] = ""
    available_wip = (
        available_wip_inventory(inventory_packages, plans, sources)
        if include_potential_wip else pd.DataFrame()
    )
    pre_wip = (
        inventory_packages[
            inventory_packages.get(
                "production_stage", pd.Series(dtype=str)
            ).eq("Pre-WIP")
        ].copy()
        if include_potential_wip and not inventory_packages.empty
        else pd.DataFrame()
    )
    potential_labels = []
    potential_summaries = []
    pre_wip_labels = []
    for row in grouped.itertuples(index=False) if include_potential_wip else []:
        matches = potential_wip_for_sku(
            available_wip, row.brand, row.strain, row.sku_type
        )
        if "infused pre-roll" in str(row.sku_type).lower():
            components = set(matches.get("wip_component", pd.Series(dtype=str)))
            potential_labels.append(
                f"{int('Flower' in components) + int('Infusion' in components)}/2 Inputs Available"
            )
        else:
            potential_labels.append(format_weight(matches.get(
                "available_weight_grams", pd.Series(dtype=float)
            ).sum()))
        if matches.empty:
            potential_summaries.append("No compatible uncommitted WIP packages")
        else:
            ages = pd.to_numeric(
                matches.get("inventory_age_days", pd.Series(dtype=float)),
                errors="coerce",
            ).dropna()
            sizes = pd.to_numeric(
                matches.get("available_weight_grams", pd.Series(dtype=float)),
                errors="coerce",
            ).dropna()
            age_label = (
                f"{int(ages.min())}-{int(ages.max())} days"
                if not ages.empty else "age unavailable"
            )
            size_label = (
                f"{format_weight(sizes.min())}-{format_weight(sizes.max())} per lot"
                if not sizes.empty else "size unavailable"
            )
            potential_summaries.append(
                f"{len(matches):,} packages | Ages {age_label} | Sizes {size_label}"
            )
        if pre_wip.empty:
            pre_wip_labels.append("0.0 g")
        else:
            pre_match = pre_wip[
                pre_wip["strain"].apply(normalize_strain_name).str.lower().eq(
                    normalize_strain_name(row.strain).lower()
                )
            ]
            pre_wip_labels.append(format_weight(pd.to_numeric(
                pre_match.get("calculated_weight_grams", pd.Series(dtype=float)),
                errors="coerce",
            ).fillna(0).sum()))
    if include_potential_wip:
        grouped["potential_matching_wip"] = potential_labels
        grouped["potential_wip_summary"] = potential_summaries
        grouped["committed_wip"] = grouped[
            "committed_weight_grams"
        ].apply(format_weight)
        grouped["matching_pre_wip_weight"] = pre_wip_labels
    result = grouped.rename(columns={
        "brand": "Brand", "strain": "Strain", "sku_type": "SKU Type",
        "units_shipped": "Units Shipped",
        "average_weekly_units": "Avg Weekly Units",
        "average_weekly_units_last_30": "Avg Weekly Units - Last 30 Days",
        "packages": "Packages",
        "current_units": "Current Units",
        "weeks_of_supply": "Weeks of Supply",
        "customers": "Customers", "demand_status": "Demand Status",
        "potential_matching_wip": "Potential Matching WIP",
        "potential_wip_summary": "Potential WIP Summary",
        "committed_wip": "Committed WIP",
        "matching_pre_wip_weight": "Matching Pre-WIP Weight",
        "history_last_shipped": "Last Shipped",
        "days_since_last_shipment": "Days Since Last Shipment",
        "lifecycle_status": "Lifecycle Status",
        "lifecycle_reason": "Lifecycle Reason",
    })[columns]
    result["Last Shipped"] = pd.to_datetime(
        result["Last Shipped"], errors="coerce"
    ).dt.strftime("%Y-%m-%d").fillna("")
    for column in [
        "Units Shipped", "Avg Weekly Units", "Avg Weekly Units - Last 30 Days", "Current Units",
        "Weeks of Supply",
    ]:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0).round(2)
    return result.sort_values(
        ["Units Shipped", "Brand", "Strain", "SKU Type"],
        ascending=[False, True, True, True],
    ).reset_index(drop=True)


def apply_availability_adjusted_velocity(
    velocity: pd.DataFrame,
    availability_summary: list[dict[str, Any]],
) -> pd.DataFrame:
    """Apply one availability shadow model without altering base velocity.

    Flower and pre-roll series with experimental evidence receive adjusted
    weekly velocity, weeks of supply, and demand status. Other SKU types retain
    their current calculation so the SKU Planning table remains complete.
    """
    if velocity.empty:
        return velocity.copy()
    result = velocity.copy()
    result["Likely OOS Weeks"] = 0
    result["Recent Gap Weeks"] = 0
    result["Availability Weeks"] = 0.0
    result["Velocity Model"] = "Current SKU Velocity — no adjusted evidence"
    if not availability_summary:
        return result

    evidence_columns = [
        "Brand", "Strain", "SKU Type", "Experimental Adjusted Velocity",
        "Likely Constrained Weeks", "Recent Gap Weeks", "Availability Weeks",
    ]
    evidence = pd.DataFrame(availability_summary)
    if evidence.empty or not set(evidence_columns).issubset(evidence.columns):
        return result
    evidence = evidence[evidence_columns].rename(columns={
        "Likely Constrained Weeks": "Adjusted Likely OOS Weeks",
        "Recent Gap Weeks": "Adjusted Recent Gap Weeks",
        "Availability Weeks": "Adjusted Availability Weeks",
    })
    result = result.merge(
        evidence,
        on=["Brand", "Strain", "SKU Type"],
        how="left",
    )
    adjusted = pd.to_numeric(
        result["Experimental Adjusted Velocity"], errors="coerce"
    )
    matched = adjusted.gt(0)
    result.loc[matched, "Avg Weekly Units"] = adjusted[matched]
    result.loc[matched, "Likely OOS Weeks"] = pd.to_numeric(
        result.loc[matched, "Adjusted Likely OOS Weeks"], errors="coerce"
    ).fillna(0).astype(int)
    result.loc[matched, "Recent Gap Weeks"] = pd.to_numeric(
        result.loc[matched, "Adjusted Recent Gap Weeks"], errors="coerce"
    ).fillna(0).astype(int)
    result.loc[matched, "Availability Weeks"] = pd.to_numeric(
        result.loc[matched, "Adjusted Availability Weeks"], errors="coerce"
    ).fillna(0)
    result.loc[matched, "Velocity Model"] = "Availability-Adjusted"
    current_units = pd.to_numeric(
        result["Current Units"], errors="coerce"
    ).fillna(0)
    result.loc[matched, "Weeks of Supply"] = (
        current_units[matched] / adjusted[matched]
    )
    result.loc[matched, "Demand Status"] = result.loc[matched].apply(
        lambda row: demand_status(
            float(row.get("Current Units", 0) or 0),
            float(row.get("Avg Weekly Units", 0) or 0),
        ),
        axis=1,
    )
    result["Avg Weekly Units"] = pd.to_numeric(
        result["Avg Weekly Units"], errors="coerce"
    ).fillna(0).round(2)
    result["Weeks of Supply"] = pd.to_numeric(
        result["Weeks of Supply"], errors="coerce"
    ).fillna(0).round(2)
    return result.drop(columns=[
        "Experimental Adjusted Velocity", "Adjusted Likely OOS Weeks",
        "Adjusted Recent Gap Weeks", "Adjusted Availability Weeks",
    ])


def build_saved_plan_rows(
    plans: pd.DataFrame, outputs: pd.DataFrame, sources: pd.DataFrame
) -> pd.DataFrame:
    columns = [
        "Plan ID", "Plan Name", "Status", "Target Date",
        "Production Line", "Department", "Brand", "Strain", "SKU Type", "Allocation %",
        "Projected Units", "Batch Weight (g)", "Source Tags", "Created By",
    ]
    if plans.empty:
        return pd.DataFrame(columns=columns)
    source_map: dict[str, str] = {}
    if not sources.empty:
        source_map = sources.groupby("plan_id")["package_tag"].apply(
            lambda values: ", ".join(sorted(set(map(str, values))))
        ).to_dict()
    outputs_by_plan = {
        str(plan_id): group.to_dict("records")
        for plan_id, group in outputs.groupby("plan_id")
    } if not outputs.empty else {}
    rows: list[dict[str, Any]] = []
    for plan in plans.to_dict("records"):
        plan_id = str(plan.get("plan_id", ""))
        plan_outputs = outputs_by_plan.get(plan_id, [])
        if not plan_outputs:
            legacy = [
                ("7g Flower", "flower_7_percent", "projected_7_units"),
                ("3.5g Flower", "flower_35_percent", "projected_35_units"),
                ("1g Flower", "flower_1_percent", "projected_1_units"),
            ]
            plan_outputs = [
                {
                    "brand": plan.get("output_brand", ""),
                    "strain": plan.get("strain", ""),
                    "sku_type": sku,
                    "allocation_percent": plan.get(percent, 0),
                    "projected_units": plan.get(units, 0),
                }
                for sku, percent, units in legacy
                if native_number(plan.get(percent, 0)) > 0
            ]
        if not plan_outputs:
            plan_outputs = [{
                "brand": plan.get("output_brand", ""),
                "strain": plan.get("strain", ""),
                "sku_type": plan.get("target_sku_type", ""),
                "allocation_percent": None,
                "projected_units": plan.get("projected_output_units", 0),
            }]
        for output in plan_outputs:
            rows.append({
                "Plan ID": plan_id,
                "Plan Name": plan.get("plan_name", ""),
                "Status": plan.get("status", ""),
                "Target Date": iso_date(plan.get("target_packaging_date")),
                "Production Line": normalized_production_line(
                    plan.get("production_line", "Unassigned")
                ),
                "Department": plan.get("assigned_department", "Production"),
                "Brand": output.get("brand", plan.get("output_brand", "")),
                "Strain": output.get("strain", plan.get("strain", "")),
                "SKU Type": output.get("sku_type", ""),
                "Allocation %": native_number(output.get("allocation_percent")),
                "Projected Units": int(native_number(output.get("projected_units"), 0)),
                "Batch Weight (g)": native_number(plan.get("batch_weight_grams"), 1),
                "Source Tags": source_map.get(plan_id, ""),
                "Created By": plan.get("created_by", ""),
            })
    return pd.DataFrame(rows, columns=columns)


def build_saved_plan_cards(
    plans: pd.DataFrame, outputs: pd.DataFrame, sources: pd.DataFrame
) -> list[dict[str, Any]]:
    """Return one expandable UI record per plan with nested detail rows."""
    if plans.empty:
        return []
    output_groups = {
        str(plan_id): group
        for plan_id, group in outputs.groupby("plan_id")
    } if not outputs.empty else {}
    source_groups = {
        str(plan_id): group
        for plan_id, group in sources.groupby("plan_id")
    } if not sources.empty else {}
    cards: list[dict[str, Any]] = []
    for plan in plans.to_dict("records"):
        plan_id = str(plan.get("plan_id", ""))
        output_rows: list[list[Any]] = []
        plan_outputs = output_groups.get(plan_id)
        if plan_outputs is not None:
            for output in plan_outputs.to_dict("records"):
                output_rows.append([
                    str(output.get("brand", "") or ""),
                    str(output.get("strain", "") or ""),
                    str(output.get("sku_type", "") or ""),
                    native_number(output.get("allocation_percent"), 2),
                    int(native_number(output.get("projected_units"), 0)),
                    native_number(output.get("allocated_weight_grams"), 1),
                ])
        if not output_rows:
            for sku_type, percent_field, unit_field in [
                ("3.5g Flower", "flower_35_percent", "projected_35_units"),
                ("7g Flower", "flower_7_percent", "projected_7_units"),
                ("1g Flower", "flower_1_percent", "projected_1_units"),
            ]:
                percent = native_number(plan.get(percent_field), 2)
                if percent > 0:
                    output_rows.append([
                        str(plan.get("output_brand", "") or ""),
                        str(plan.get("strain", "") or ""),
                        sku_type,
                        percent,
                        int(native_number(plan.get(unit_field), 0)),
                        native_number(
                            native_number(plan.get("batch_weight_grams"), 2)
                            * percent / 100,
                            1,
                        ),
                    ])
        source_rows: list[list[Any]] = []
        plan_sources = source_groups.get(plan_id)
        if plan_sources is not None:
            for source in plan_sources.to_dict("records"):
                source_rows.append([
                    str(source.get("package_tag", "") or ""),
                    native_number(source.get("allocated_weight_grams"), 1),
                ])
        output_label = ", ".join(
            f"{row[0]} {row[1]} {row[2]} ({row[4]:,.0f})"
            for row in output_rows
        ) or str(plan.get("target_sku_type", "") or "No outputs")
        production_line = normalized_production_line(
            plan.get("production_line", "Unassigned")
        )
        line_color, line_background = PRODUCTION_LINE_STYLES.get(
            production_line, PRODUCTION_LINE_STYLES["Unassigned"]
        )
        cards.append({
            "Plan ID": plan_id,
            "Plan Name": str(plan.get("plan_name", "") or ""),
            "Status": str(plan.get("status", "") or ""),
            "Target Date": iso_date(plan.get("target_packaging_date")),
            "Production Line": production_line,
            "Line Color": line_color,
            "Line Background": line_background,
            "Department": str(
                plan.get("assigned_department", "Production") or "Production"
            ),
            "Batch Weight (g)": native_number(
                plan.get("batch_weight_grams"), 1
            ),
            "Created By": str(plan.get("created_by", "") or ""),
            "Target Brand": str(plan.get("output_brand", "") or ""),
            "Target Strain": str(plan.get("strain", "") or ""),
            "Target SKU Type": str(plan.get("target_sku_type", "") or ""),
            "Recipe Type": str(plan.get("recipe_type", "") or ""),
            "Notes": str(plan.get("notes", "") or ""),
            "Process Loss %": native_number(
                plan.get("process_loss_percent", plan.get("loss_percent", 0)), 2
            ),
            "Overfill %": native_number(plan.get("overfill_percent"), 2),
            "QA Retention (g)": native_number(plan.get("qa_retention_grams"), 2),
            "Smalls/Shake %": native_number(plan.get("smalls_shake_percent"), 2),
            "Unit Fill Weight (g)": native_number(
                plan.get("unit_fill_weight_grams"), 3
            ),
            "Formulation Details": str(
                plan.get("formulation_details", "{}") or "{}"
            ),
            "Brand": ", ".join(sorted({str(row[0]) for row in output_rows})),
            "Strain": ", ".join(sorted({str(row[1]) for row in output_rows})),
            "SKU Type": ", ".join(sorted({str(row[2]) for row in output_rows})),
            "Output Summary": output_label,
            "Outputs": output_rows,
            "Sources": source_rows,
            "Source Count": len(source_rows),
        })
    return cards


def build_customer_summary(analysis: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Destination License", "Customer", "Units Shipped",
        "Shipment Value", "Manifests", "SKUs Purchased", "First Shipment",
        "Last Shipment", "Median Receipt Hours", "Average Manifest Value",
    ]
    demand = analysis[analysis["is_demand"]].copy()
    if demand.empty:
        return pd.DataFrame(columns=columns)
    summary = demand.groupby(
        ["destination_license", "destination_facility"], dropna=False
    ).agg(
        units_shipped=("shipped_units", "sum"),
        shipment_value=("shipper_dollar_amount", "sum"),
        manifests=("manifest", "nunique"),
        skus=("item_key", "nunique"),
        first_shipment=("created_at", "min"),
        last_shipment=("created_at", "max"),
        median_receipt_hours=("transit_hours", "median"),
    ).reset_index()
    summary["average_manifest_value"] = (
        summary["shipment_value"]
        / summary["manifests"].replace(0, pd.NA)
    )
    summary = summary.rename(columns={
        "destination_license": "Destination License",
        "destination_facility": "Customer",
        "units_shipped": "Units Shipped",
        "shipment_value": "Shipment Value",
        "manifests": "Manifests",
        "skus": "SKUs Purchased",
        "first_shipment": "First Shipment",
        "last_shipment": "Last Shipment",
        "median_receipt_hours": "Median Receipt Hours",
        "average_manifest_value": "Average Manifest Value",
    })
    for column in ["First Shipment", "Last Shipment"]:
        summary[column] = summary[column].apply(iso_date)
    for column in [
        "Units Shipped", "Shipment Value", "Median Receipt Hours",
        "Average Manifest Value",
    ]:
        summary[column] = pd.to_numeric(
            summary[column], errors="coerce"
        ).fillna(0).round(2)
    return summary[columns].sort_values(
        "Shipment Value", ascending=False
    ).reset_index(drop=True)


def build_retail_delivery_history(analysis: pd.DataFrame) -> pd.DataFrame:
    """Build compact four-week accepted and pending retailer activity.

    The browser receives daily SKU/customer aggregates rather than raw package
    rows. This keeps the new map workspace responsive while still allowing the
    user to switch between one, two, three, and four-week windows instantly.

    Accepted transfers are dated by the Metrc ``received_at`` timestamp. Open
    outbound transfers are dated by ``created_at`` and remain separate so the
    interface never presents an unaccepted shipment as a completed delivery.
    """
    columns = [
        "Activity Date", "Date Type", "Transfer Status",
        "Destination License", "Customer", "Brand", "Strain", "SKU Type",
        "Units Shipped", "Packages", "Manifests",
    ]
    if analysis.empty:
        return pd.DataFrame(columns=columns)

    activity_frames: list[pd.DataFrame] = []
    activity_specs = (
        ("is_demand", "received_at", "Received At", "Accepted"),
        (
            "is_open_shipment", "created_at", "Sent At",
            "Awaiting Acceptance",
        ),
    )
    for flag, timestamp_column, date_type, status in activity_specs:
        if flag not in analysis or timestamp_column not in analysis:
            continue
        rows = analysis[analysis[flag].fillna(False).astype(bool)].copy()
        if rows.empty:
            continue
        rows["activity_at"] = pd.to_datetime(
            rows[timestamp_column], errors="coerce"
        )
        rows = rows[rows["activity_at"].notna()].copy()
        if rows.empty:
            continue
        anchor = rows["activity_at"].max().normalize()
        rows = rows[rows["activity_at"].ge(
            anchor - pd.Timedelta(days=27)
        )].copy()
        rows["activity_date"] = rows["activity_at"].dt.normalize()
        rows["date_type"] = date_type
        rows["transfer_status"] = status
        activity_frames.append(rows)

    if not activity_frames:
        return pd.DataFrame(columns=columns)

    activity = pd.concat(activity_frames, ignore_index=True)
    history = activity.groupby(
        [
            "activity_date", "date_type", "transfer_status",
            "destination_license", "destination_facility", "brand", "strain",
            "sku_type",
        ],
        dropna=False,
    ).agg(
        units_shipped=("shipped_units", "sum"),
        packages=("package_tag", "nunique"),
        manifests=("manifest", "nunique"),
    ).reset_index().rename(columns={
        "activity_date": "Activity Date", "date_type": "Date Type",
        "transfer_status": "Transfer Status",
        "destination_license": "Destination License",
        "destination_facility": "Customer",
        "brand": "Brand", "strain": "Strain", "sku_type": "SKU Type",
        "units_shipped": "Units Shipped", "packages": "Packages",
        "manifests": "Manifests",
    })
    history["Activity Date"] = history["Activity Date"].apply(iso_date)
    history["Units Shipped"] = pd.to_numeric(
        history["Units Shipped"], errors="coerce"
    ).fillna(0).round(2)
    return history[columns].sort_values(
        ["Activity Date", "Units Shipped"], ascending=[False, False]
    ).reset_index(drop=True)


def build_shipment_exceptions(analysis: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Manifest", "State", "Destination License", "Customer", "Created",
        "Received", "Packages", "Items", "Shipper Value",
    ]
    if analysis.empty:
        return pd.DataFrame(columns=columns)
    exceptions = analysis[
        analysis["is_open_shipment"] | analysis["is_shipment_exception"]
    ].copy()
    if exceptions.empty:
        return pd.DataFrame(columns=columns)
    summary = exceptions.groupby(
        [
            "manifest", "state", "destination_license",
            "destination_facility",
        ],
        dropna=False,
    ).agg(
        created_at=("created_at", "min"),
        received_at=("received_at", "max"),
        packages=("package_tag", "nunique"),
        items=("item_key", "nunique"),
        shipper_value=("shipper_dollar_amount", "sum"),
    ).reset_index().rename(columns={
        "manifest": "Manifest", "state": "State",
        "destination_license": "Destination License",
        "destination_facility": "Customer", "created_at": "Created",
        "received_at": "Received", "packages": "Packages",
        "items": "Items", "shipper_value": "Shipper Value",
    })
    summary["Created"] = summary["Created"].apply(iso_date)
    summary["Received"] = summary["Received"].apply(iso_date)
    summary["Shipper Value"] = pd.to_numeric(
        summary["Shipper Value"], errors="coerce"
    ).fillna(0).round(2)
    return summary[columns].sort_values(
        "Created", ascending=False
    ).reset_index(drop=True)


def build_shipment_exception_packages(analysis: pd.DataFrame) -> pd.DataFrame:
    """Preserve package detail for open, rejected, and returned transfers."""
    if analysis.empty:
        return build_transfer_display(analysis)
    exception_packages = analysis[
        analysis["is_open_shipment"] | analysis["is_shipment_exception"]
    ].copy()
    return build_transfer_display(exception_packages)


def build_transfer_display(analysis: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Manifest", "Invoice Number", "Created", "Received", "State",
        "Destination License", "Customer", "Package Tag", "Metrc Item",
        "Brand", "Strain", "SKU Type", "Shipped Units", "Shipper Value",
        "Demand Record",
    ]
    if analysis.empty:
        return pd.DataFrame(columns=columns)
    display = analysis[[
        "manifest", "invoice_number", "created_at", "received_at", "state",
        "destination_license", "destination_facility", "package_tag", "item",
        "brand", "strain", "sku_type", "shipped_units",
        "shipper_dollar_amount", "is_demand",
    ]].rename(columns={
        "manifest": "Manifest", "invoice_number": "Invoice Number",
        "created_at": "Created", "received_at": "Received",
        "state": "State", "destination_license": "Destination License",
        "destination_facility": "Customer", "package_tag": "Package Tag",
        "item": "Metrc Item", "brand": "Brand", "strain": "Strain",
        "sku_type": "SKU Type", "shipped_units": "Shipped Units",
        "shipper_dollar_amount": "Shipper Value",
        "is_demand": "Demand Record",
    })
    display["Created"] = display["Created"].apply(iso_date)
    display["Received"] = display["Received"].apply(iso_date)
    display["Shipped Units"] = pd.to_numeric(
        display["Shipped Units"], errors="coerce"
    ).fillna(0).round(2)
    display["Shipper Value"] = pd.to_numeric(
        display["Shipper Value"], errors="coerce"
    ).fillna(0).round(2)
    return display[columns].sort_values(
        "Created", ascending=False
    ).reset_index(drop=True)


def build_inventory_views(
    packages: pd.DataFrame, plans: pd.DataFrame, sources: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Create the package-level inventory views used by the Reflex workspace."""
    if packages.empty:
        return {key: pd.DataFrame() for key in [
            "cpg_inventory", "bulk_inventory", "wip_inventory",
            "potential_wip_inventory", "aging_cpg", "aging_bulk",
            "all_inventory", "needs_review",
        ]}
    data = packages.copy()
    numeric = [
        "quantity", "calculated_weight_grams", "inventory_age_days",
        "days_remaining_in_sale_window",
    ]
    for column in numeric:
        data[column] = pd.to_numeric(data.get(column), errors="coerce")
    data["qcc_owned"] = data["qcc_owned"].fillna(0).astype(bool)
    data["needs_review"] = data["needs_review"].fillna(0).astype(bool)
    for column in [
        "is_finished_retail_sku", "include_in_cpg", "is_retention_sample",
    ]:
        if column not in data.columns:
            data[column] = False
        data[column] = data[column].fillna(0).astype(bool)

    def display(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame()
        result = frame[[
            "brand", "strain", "sku_type", "quantity", "unit",
            "calculated_weight_grams", "inventory_age_days",
            "days_remaining_in_sale_window", "production_stage", "qa_status",
            "category", "location", "current_facility", "facility",
            "source_harvest", "ownership_status",
            "package_tag", "item", "source_license_type",
        ]].copy()
        return result.rename(columns={
            "brand": "Brand", "strain": "Strain", "sku_type": "SKU Type",
            "quantity": "Quantity", "unit": "Unit",
            "calculated_weight_grams": "Calculated Weight (g)",
            "inventory_age_days": "Age", "days_remaining_in_sale_window": "Days to Spoil",
            "production_stage": "Production Stage", "qa_status": "QA Status",
            "category": "Category", "location": "Location",
            "current_facility": "Current Facility", "facility": "Facility",
            "source_harvest": "Source Harvest", "ownership_status": "Ownership Status",
            "package_tag": "Metrc Tag", "item": "Item",
            "source_license_type": "License",
        })

    # Streamlit publishes the authoritative finished-retail decision. Keeping
    # that decision in one place prevents Reflex from inflating CPG counts by
    # treating every packaged or retention-stage record as a sellable SKU.
    cpg_mask = data["include_in_cpg"] & data["quantity"].ge(0)
    bulk_mask = ~data["production_stage"].isin(
        ["Packaged Goods", "Retention Storage", "Needs Review", "Secure Waste"]
    )
    physical_wip = wip_inventory_status(data, plans, sources)
    available_wip = physical_wip[
        physical_wip["available_weight_grams"].gt(0)
    ].copy() if not physical_wip.empty else physical_wip.copy()
    wip_and_pre_wip = pd.concat([
        physical_wip,
        data[data["production_stage"].eq("Pre-WIP")],
    ], ignore_index=True, sort=False)
    potential_wip_display = display(available_wip)
    if not potential_wip_display.empty:
        potential_wip_display["Available Weight (g)"] = pd.to_numeric(
            available_wip.get("available_weight_grams"), errors="coerce"
        ).fillna(0).round(2).tolist()
    # Aging workspaces now show the complete positive-quantity distribution.
    # Risk bands and user selection in Reflex determine the visible subset.
    aging_cpg_mask = (
        data["include_in_cpg"]
        & data["production_stage"].eq("Packaged Goods")
        & data["quantity"].gt(0)
    )
    aging_bulk_mask = (
        data["production_stage"].isin([
            "Sellable Bulk", "WIP-Cultivation", "WIP-Manufacturing",
            "Pre-WIP",
        ])
        & data["quantity"].gt(0)
    )
    review_source = data[data["needs_review"]].copy()
    needs_review_display = display(review_source)
    if not needs_review_display.empty:
        needs_review_display["Material Type"] = (
            review_source["material_type"].fillna("").astype(str).tolist()
        )
        needs_review_display["Calculated Weight"] = (
            review_source["calculated_weight_grams"].apply(format_weight).tolist()
        )
        needs_review_display["Review Reason"] = (
            review_source["review_reason"].fillna("").astype(str).tolist()
        )
    cpg_inventory = display(data[cpg_mask & data["qcc_owned"]])
    bulk_inventory = display(data[bulk_mask])
    wip_inventory = display(wip_and_pre_wip)
    aging_cpg = display(data[aging_cpg_mask])
    aging_bulk = display(data[aging_bulk_mask])
    all_inventory = display(data)

    # Keep one authoritative package collection in each Reflex user session.
    # Earlier pilot versions copied seven overlapping inventory views into
    # every session. Two users could therefore hold many copies of the same
    # 2,000+ packages and overwhelm the event WebSocket. Membership flags let
    # the UI derive every existing view from this single collection without
    # changing the classification rules.
    def tag_set(frame: pd.DataFrame) -> set[str]:
        if frame.empty or "Metrc Tag" not in frame.columns:
            return set()
        return set(frame["Metrc Tag"].fillna("").astype(str))

    view_frames = {
        "View CPG": cpg_inventory,
        "View Bulk": bulk_inventory,
        "View WIP": wip_inventory,
        "View Potential WIP": potential_wip_display,
        "View Aging CPG": aging_cpg,
        "View Aging Bulk": aging_bulk,
        "View Needs Review": needs_review_display,
    }
    if not all_inventory.empty:
        package_tags = all_inventory["Metrc Tag"].fillna("").astype(str)
        for flag, frame in view_frames.items():
            all_inventory[flag] = package_tags.isin(tag_set(frame))

        available_by_tag: dict[str, float] = {}
        if (
            not potential_wip_display.empty
            and "Available Weight (g)" in potential_wip_display.columns
        ):
            available = potential_wip_display[[
                "Metrc Tag", "Available Weight (g)"
            ]].copy()
            available["Available Weight (g)"] = pd.to_numeric(
                available["Available Weight (g)"], errors="coerce"
            ).fillna(0)
            available_by_tag = (
                available.groupby("Metrc Tag")["Available Weight (g)"]
                .max().to_dict()
            )
        all_inventory["Available Weight (g)"] = (
            package_tags.map(available_by_tag).fillna(0).round(2)
        )

    return {
        "cpg_inventory": cpg_inventory,
        "bulk_inventory": bulk_inventory,
        "wip_inventory": wip_inventory,
        "potential_wip_inventory": potential_wip_display,
        "aging_cpg": aging_cpg,
        "aging_bulk": aging_bulk,
        "all_inventory": all_inventory,
        "needs_review": needs_review_display,
    }


def load_operational_context() -> dict[str, Any]:
    """Build the shared Inventory/Production context at most once at a time."""
    with _OPERATIONAL_BUILD_LOCK:
        with _OPERATIONAL_CONTEXT_LOCK:
            context_age = time.monotonic() - float(
                _OPERATIONAL_CONTEXT.get("loaded_at", 0.0)
            )
            cached = (
                _OPERATIONAL_CONTEXT.get("payload")
                if context_age < OPERATIONAL_CACHE_SECONDS else None
            )
        if cached:
            return cached

        # Inventory and Production are independent. Each worker now reuses a
        # single connection for its related reads, avoiding repeated remote
        # DNS/TLS setup during the first user's cold start.
        with ThreadPoolExecutor(max_workers=3) as executor:
            inventory_future = executor.submit(load_latest_inventory_bundle)
            production_future = executor.submit(load_production_data)
            templates_future = executor.submit(load_reflex_production_templates)
            snapshot, inventory_skus, inventory_packages = inventory_future.result()
            plans, outputs, sources = production_future.result()
            production_templates = templates_future.result()
        payload = {
            "snapshot": snapshot,
            "inventory_skus": inventory_skus,
            "inventory_packages": inventory_packages,
            "plans": plans,
            "outputs": outputs,
            "sources": sources,
            "production_templates": production_templates,
        }
        with _OPERATIONAL_CONTEXT_LOCK:
            _OPERATIONAL_CONTEXT["payload"] = payload
            _OPERATIONAL_CONTEXT["loaded_at"] = time.monotonic()
        return payload


def build_dashboard_data(include_sales: bool = True) -> dict[str, Any]:
    """Build either the fast operational shell or the complete Sales payload."""
    sales_snapshot: dict[str, Any] = {}
    sales_error = ""
    analysis = empty_sales_analysis()
    if include_sales:
        try:
            sales_snapshot, analysis = load_published_sales_snapshot()
            if not sales_snapshot:
                sales_error = (
                    "Sales data is waiting for a published snapshot. Use the "
                    "Streamlit 81.5 Sales Snapshot publisher, then refresh."
                )
        except Exception as error:
            sales_error = f"The published Sales snapshot could not be read: {error}"
            analysis = empty_sales_analysis()

        allow_raw_fallback = os.getenv(
            "QCC_ALLOW_RAW_TRANSFER_FALLBACK", ""
        ).strip().lower() in {"1", "true", "yes"}
        if not sales_snapshot and allow_raw_fallback:
            try:
                transfers = load_transfer_rows()
                analysis = prepare_transfer_analysis(transfers)
                sales_error = ""
            except Exception as error:
                sales_error = f"Sales transfer fallback could not be read: {error}"
    operational_context = load_operational_context()
    snapshot = operational_context["snapshot"]
    inventory_skus = operational_context["inventory_skus"]
    inventory_packages = operational_context["inventory_packages"]
    plans = operational_context["plans"]
    outputs = operational_context["outputs"]
    sources = operational_context["sources"]
    production_templates = operational_context["production_templates"]
    authoritative_cpg_ready = {
        "is_finished_retail_sku", "include_in_cpg", "is_retention_sample",
        "classification_rule_version",
    }.issubset(inventory_packages.columns)
    classification_rule_version = ""
    authoritative_cpg_count = 0
    if authoritative_cpg_ready and not inventory_packages.empty:
        versions = inventory_packages["classification_rule_version"].fillna("")
        versions = versions.astype(str).str.strip()
        versions = versions[versions.ne("")]
        if not versions.empty:
            classification_rule_version = versions.mode().iloc[0]
        authoritative_cpg_count = int(
            (
                inventory_packages["include_in_cpg"].fillna(0).astype(bool)
                & inventory_packages["qcc_owned"].fillna(0).astype(bool)
                & pd.to_numeric(
                    inventory_packages["quantity"], errors="coerce"
                ).ge(0)
            ).sum()
        )
    if not include_sales:
        inventory_views = build_inventory_views(inventory_packages, plans, sources)
        saved_plans = build_saved_plan_rows(plans, outputs, sources)
        saved_plan_cards = build_saved_plan_cards(plans, outputs, sources)
        calendar = [
            {
                "Target Date": card["Target Date"],
                "Plan ID": card["Plan ID"],
                "Plan Name": card["Plan Name"],
                "Status": card["Status"],
                "Department": card["Department"],
                "Production Line": card["Production Line"],
                "Line Color": card["Line Color"],
                "Line Background": card["Line Background"],
                "Brand": card["Brand"],
                "Strain": card["Strain"],
                "SKU Type": card["SKU Type"],
                "Output Summary": card["Output Summary"],
            }
            for card in saved_plan_cards if card["Target Date"]
        ]
        all_inventory = inventory_views.get("all_inventory", pd.DataFrame())
        def options(column: str) -> list[str]:
            values = (
                all_inventory.get(column, pd.Series(dtype=str))
                .dropna().astype(str).str.strip()
            )
            return sorted(value for value in set(values) if value)
        return {
            "metrics": {
                "units": 0, "value": 0, "customers": 0, "manifests": 0,
                "weighted_price": 0, "stockouts": 0, "latest_shipment": "",
                "open_manifests": 0, "exception_manifests": 0,
                "exception_rows": 0, "transfer_rows": 0,
            },
            "snapshot": {
                "business_date": iso_date(snapshot.get("business_date")),
                "published_at": str(snapshot.get("published_at", "")),
                "package_count": int(native_number(snapshot.get("package_count"), 0)),
                "sku_count": int(native_number(snapshot.get("sku_count"), 0)),
                "detail_count": int(len(inventory_packages)),
                "authoritative_cpg_count": authoritative_cpg_count,
                "classification_rule_version": classification_rule_version,
            },
            "brands": options("Brand"), "strains": options("Strain"),
            "sku_types": options("SKU Type"),
            "monthly": [], "top_skus": [], "business_pulse": [],
            "velocity": [], "velocity_windows": {"All Time": []},
            "availability_adjusted_velocity_windows": {"All Time": []},
            "availability_demand_summary": [],
            "availability_demand_weekly": [],
            "stockouts": [], "customers": [], "exceptions": [],
            "retail_delivery_history": [],
            "retailer_locations": [],
            "transfer_data": [], "transfer_import_log": [],
            "saved_plans": record_list(saved_plans),
            "saved_plan_cards": saved_plan_cards,
            "production_templates": record_list(production_templates),
            "calendar": sorted(calendar, key=lambda row: row["Target Date"]),
            "sales_ready": False, "sales_error": "", "sales_snapshot": {},
            "inventory_ready": not inventory_packages.empty,
            "authoritative_cpg_ready": authoritative_cpg_ready,
            "all_inventory": record_list(all_inventory.round(2)),
            "loaded_at": datetime.now().astimezone().strftime(
                "%Y-%m-%d %I:%M %p %Z"
            ),
            "rule_version": (
                f"QCC Control Tower {classification_rule_version} shared inventory rules"
                if classification_rule_version else
                "Publish a Version 81.4 inventory snapshot for authoritative CPG rules"
            ),
        }
    if not analysis.empty:
        analysis = apply_inventory_master(analysis, inventory_skus)
    demand = analysis[analysis["is_demand"]].copy()
    velocity = build_velocity(
        demand, inventory_skus, inventory_packages, plans, outputs, sources
    )
    velocity_windows: dict[str, list[dict[str, Any]]] = {}
    if not demand.empty:
        velocity_end = pd.Timestamp(demand["created_at"].max()).normalize()
        period_frames: dict[str, pd.DataFrame] = {}
        for label, days in [
            ("1 Week", 7), ("30 Days", 30), ("60 Days", 60),
            ("90 Days", 90), ("120 Days", 120),
        ]:
            window_start = velocity_end - pd.Timedelta(days=days - 1)
            window_demand = demand[
                pd.to_datetime(demand["created_at"], errors="coerce").ge(window_start)
            ].copy()
            period_frames[label] = build_velocity(
                window_demand,
                inventory_skus,
                inventory_packages,
                plans,
                outputs,
                sources,
                all_history_demand=demand,
                period_days=days,
                include_potential_wip=False,
            )
        keys = ["Brand", "Strain", "SKU Type"]
        last_30 = period_frames["30 Days"][
            keys + ["Avg Weekly Units"]
        ].rename(columns={
            "Avg Weekly Units": "Avg Weekly Units - Last 30 Days"
        })
        velocity = velocity.drop(
            columns=["Avg Weekly Units - Last 30 Days"]
        ).merge(last_30, on=keys, how="left")
        velocity["Avg Weekly Units - Last 30 Days"] = pd.to_numeric(
            velocity["Avg Weekly Units - Last 30 Days"], errors="coerce"
        ).fillna(0).round(2)
        velocity_windows["All Time"] = record_list(velocity)
        for label in ["1 Week", "30 Days", "60 Days", "90 Days", "120 Days"]:
            frame = period_frames[label].drop(columns=[
                "Potential Matching WIP", "Potential WIP Summary",
                "Matching Pre-WIP Weight", "Avg Weekly Units - Last 30 Days",
            ]).merge(
                velocity[[
                    *keys,
                    "Potential Matching WIP", "Potential WIP Summary",
                    "Matching Pre-WIP Weight", "Avg Weekly Units - Last 30 Days",
                ]],
                on=keys,
                how="left",
            )
            velocity_windows[label] = record_list(frame)
    else:
        velocity_windows["All Time"] = record_list(velocity)
        velocity_windows.update({
            "1 Week": [], "30 Days": [], "60 Days": [], "90 Days": [],
            "120 Days": [],
        })
    availability_period_days = {
        "1 Week": 7,
        "30 Days": 30,
        "60 Days": 60,
        "90 Days": 90,
        "120 Days": 120,
        "All Time": None,
    }
    availability_demand_windows: dict[str, dict[str, list[dict[str, Any]]]] = {}
    availability_adjusted_velocity_windows: dict[
        str, list[dict[str, Any]]
    ] = {}
    for label, days in availability_period_days.items():
        base_velocity = pd.DataFrame(velocity_windows.get(label, []))
        window_analysis = build_availability_demand_analysis(
            demand,
            base_velocity,
            period_days=days,
        )
        availability_demand_windows[label] = window_analysis
        adjusted_velocity = apply_availability_adjusted_velocity(
            base_velocity,
            window_analysis["summary"],
        )
        availability_adjusted_velocity_windows[label] = record_list(
            adjusted_velocity
        )
    availability_demand = availability_demand_windows["All Time"]
    # The Sales background load reuses the inventory context and must not
    # rebuild every inventory table a second time during the same login.
    inventory_views = (
        {} if include_sales
        else build_inventory_views(inventory_packages, plans, sources)
    )
    stockouts = velocity[
        velocity["Demand Status"].eq("Current Stockout")
    ].copy()
    monthly = pd.DataFrame(columns=["Month", "Units", "Value", "Customers"])
    top_skus = pd.DataFrame(
        columns=["Brand", "Strain", "SKU Type", "Units", "Value", "Customers"]
    )
    if not demand.empty:
        monthly = demand.groupby("shipment_month", dropna=False).agg(
            Units=("shipped_units", "sum"),
            Value=("shipper_dollar_amount", "sum"),
            Customers=("destination_license", "nunique"),
        ).reset_index().rename(columns={"shipment_month": "Month"})
        top_skus = demand.groupby(
            ["brand", "strain", "sku_type"], dropna=False
        ).agg(
            Units=("shipped_units", "sum"),
            Value=("shipper_dollar_amount", "sum"),
            Customers=("destination_license", "nunique"),
        ).reset_index().rename(columns={
            "brand": "Brand", "strain": "Strain", "sku_type": "SKU Type",
        }).sort_values("Units", ascending=False)
    business_pulse = pd.DataFrame(columns=[
        "Brand", "Strain", "SKU Type", "Customer License", "Manifest",
        "Units", "Value",
    ])
    if not demand.empty:
        pulse_end = pd.Timestamp(demand["created_at"].max())
        pulse_start = pulse_end.normalize() - pd.Timedelta(days=29)
        recent_demand = demand[
            pd.to_datetime(demand["created_at"], errors="coerce").ge(pulse_start)
        ].copy()
        if not recent_demand.empty:
            business_pulse = recent_demand.groupby(
                [
                    "brand", "strain", "sku_type", "destination_license",
                    "manifest",
                ],
                dropna=False,
            ).agg(
                Units=("shipped_units", "sum"),
                Value=("shipper_dollar_amount", "sum"),
            ).reset_index().rename(columns={
                "brand": "Brand", "strain": "Strain", "sku_type": "SKU Type",
                "destination_license": "Customer License",
                "manifest": "Manifest",
            })
    saved_plans = build_saved_plan_rows(plans, outputs, sources)
    saved_plan_cards = build_saved_plan_cards(plans, outputs, sources)
    calendar = [
        {
            "Target Date": card["Target Date"],
            "Plan ID": card["Plan ID"],
            "Plan Name": card["Plan Name"],
                "Status": card["Status"],
                "Department": card["Department"],
                "Production Line": card["Production Line"],
                "Line Color": card["Line Color"],
                "Line Background": card["Line Background"],
                "Brand": card["Brand"],
            "Strain": card["Strain"],
            "SKU Type": card["SKU Type"],
            "Output Summary": card["Output Summary"],
        }
        for card in saved_plan_cards
        if card["Target Date"]
    ]
    customers = build_customer_summary(analysis)
    retail_delivery_history = build_retail_delivery_history(analysis)
    retailer_locations = load_retailer_locations()
    exceptions = build_shipment_exceptions(analysis)
    exception_packages = build_shipment_exception_packages(analysis)
    transfer_display = build_transfer_display(analysis)
    # Keep the complete derived transfer views on the server. Distribution
    # tabs request only their selected state and visible page, rather than
    # serializing thousands of rows into every browser session.
    with _DISTRIBUTION_CACHE_LOCK:
        _DISTRIBUTION_CACHE.update({
            "loaded_at": time.monotonic(),
            "transfers": transfer_display,
            "exceptions": exceptions,
            "exception_packages": exception_packages,
        })
    transfer_import_log = load_transfer_import_log()
    total_units = native_number(demand["shipped_units"].sum()) if not demand.empty else 0
    total_value = native_number(demand["shipper_dollar_amount"].sum()) if not demand.empty else 0
    latest_date = iso_date(demand["created_at"].max()) if not demand.empty else ""
    manifests = int(demand["manifest"].nunique()) if not demand.empty else 0
    weighted_price = total_value / total_units if total_units else 0
    brands = sorted(set(
        velocity["Brand"].dropna().astype(str).tolist()
        + saved_plans.get("Brand", pd.Series(dtype=str)).dropna().astype(str).tolist()
    ))
    strains = sorted(set(
        velocity["Strain"].dropna().astype(str).tolist()
        + saved_plans.get("Strain", pd.Series(dtype=str)).dropna().astype(str).tolist()
    ))
    sku_types = sorted(set(
        velocity["SKU Type"].dropna().astype(str).tolist()
        + saved_plans.get("SKU Type", pd.Series(dtype=str)).dropna().astype(str).tolist()
    ))
    return {
        "metrics": {
            "units": total_units,
            "value": total_value,
            "customers": int(demand["destination_license"].nunique()) if not demand.empty else 0,
            "manifests": manifests,
            "weighted_price": weighted_price,
            "stockouts": int(len(stockouts)),
            "latest_shipment": latest_date,
            "open_manifests": int(
                analysis.loc[analysis["is_open_shipment"], "manifest"].nunique()
            ) if not analysis.empty else 0,
            "exception_manifests": int(
                analysis.loc[
                    analysis["is_shipment_exception"], "manifest"
                ].nunique()
            ) if not analysis.empty else 0,
            "exception_rows": int(len(exceptions)),
            "transfer_rows": int(len(analysis)),
        },
        "snapshot": {
            "business_date": iso_date(snapshot.get("business_date")),
            "published_at": str(snapshot.get("published_at", "")),
            "package_count": int(native_number(snapshot.get("package_count"), 0)),
            "sku_count": int(native_number(snapshot.get("sku_count"), 0)),
            "detail_count": int(len(inventory_packages)),
            "authoritative_cpg_count": authoritative_cpg_count,
            "classification_rule_version": classification_rule_version,
        },
        "brands": brands,
        "strains": strains,
        "sku_types": sku_types,
        "monthly": record_list(monthly.round(2)),
        "top_skus": record_list(top_skus.round(2)),
        "business_pulse": record_list(business_pulse.round(2)),
        "velocity": record_list(velocity),
        "velocity_windows": velocity_windows,
        "availability_adjusted_velocity_windows": (
            availability_adjusted_velocity_windows
        ),
        "availability_demand_summary": availability_demand["summary"],
        "availability_demand_weekly": availability_demand["weekly"],
        "stockouts": record_list(stockouts),
        "saved_plans": record_list(saved_plans),
        "saved_plan_cards": saved_plan_cards,
        "production_templates": record_list(production_templates),
        "calendar": sorted(calendar, key=lambda row: row["Target Date"]),
        "customers": record_list(customers),
        "retail_delivery_history": record_list(retail_delivery_history),
        "retailer_locations": retailer_locations,
        "exceptions": record_list(exceptions),
        "exception_packages": record_list(exception_packages),
        "transfer_data": record_list(transfer_display.head(2000)),
        "transfer_import_log": record_list(transfer_import_log),
        "sales_ready": bool(sales_snapshot) and not analysis.empty,
        "sales_error": sales_error,
        "sales_snapshot": {
            "snapshot_id": str(sales_snapshot.get("snapshot_id", "")),
            "published_at": str(sales_snapshot.get("published_at", "")),
            "published_by": str(sales_snapshot.get("published_by", "")),
            "source_row_count": int(native_number(
                sales_snapshot.get("source_row_count"), 0
            )),
        },
        "inventory_ready": not inventory_packages.empty,
        "authoritative_cpg_ready": authoritative_cpg_ready,
        # All inventory workspaces are derived from one flagged package list
        # in Reflex state. This removes six overlapping per-user copies while
        # preserving the exact Streamlit-published classifications.
        "all_inventory": record_list(
            inventory_views.get("all_inventory", pd.DataFrame()).round(2)
        ),
        "loaded_at": datetime.now().astimezone().strftime("%Y-%m-%d %I:%M %p %Z"),
        "rule_version": (
            f"QCC Control Tower {classification_rule_version} shared inventory rules"
            if classification_rule_version
            else "Publish a Version 81.4 inventory snapshot for authoritative CPG rules"
        ),
    }


def get_dashboard_data(force_refresh: bool = False) -> dict[str, Any]:
    """Load the fast operational shell without waiting for transfer history.

    Callers treat the returned collections as read-only. Returning the shared
    payload avoids a full deep copy for every login and tab change.
    """
    now = time.monotonic()
    with _DASHBOARD_CACHE_LOCK:
        payload = _DASHBOARD_CACHE.get("payload")
        age = now - float(_DASHBOARD_CACHE.get("loaded_at", 0.0))
        if payload is not None and not force_refresh and age < OPERATIONAL_CACHE_SECONDS:
            return payload
        payload = build_dashboard_data(include_sales=False)
        _DASHBOARD_CACHE["payload"] = payload
        _DASHBOARD_CACHE["loaded_at"] = time.monotonic()
        return payload


def get_sales_dashboard_data(force_refresh: bool = False) -> dict[str, Any]:
    """Load and cache transfer-dependent Sales data independently."""
    now = time.monotonic()
    with _SALES_DASHBOARD_CACHE_LOCK:
        payload = _SALES_DASHBOARD_CACHE.get("payload")
        age = now - float(_SALES_DASHBOARD_CACHE.get("loaded_at", 0.0))
        if payload is not None and not force_refresh and age < SALES_CACHE_SECONDS:
            return payload
        payload = build_dashboard_data(include_sales=True)
        _SALES_DASHBOARD_CACHE["payload"] = payload
        _SALES_DASHBOARD_CACHE["loaded_at"] = time.monotonic()
        return payload


def get_distribution_operations_data(
    view: str,
    *,
    exception_state: str = "Shipped",
    page: int = 1,
    page_size: int = 50,
    brand_filter: str = "All Brands",
    strain_filter: str = "All Strains",
    sku_filter: str = "All SKU Types",
    search_text: str = "",
) -> dict[str, Any]:
    """Return only the selected Distribution Operations records.

    The complete, analyzed transfer history stays in a shared server cache.
    Each browser receives just one transfer page or one exception state, which
    materially reduces tab-switch latency and websocket payload size.
    """
    sales_payload = get_sales_dashboard_data()
    with _DISTRIBUTION_CACHE_LOCK:
        transfers = _DISTRIBUTION_CACHE.get("transfers")
        exceptions = _DISTRIBUTION_CACHE.get("exceptions")
        exception_packages = _DISTRIBUTION_CACHE.get("exception_packages")
        transfers = (
            transfers.copy()
            if isinstance(transfers, pd.DataFrame)
            else pd.DataFrame(sales_payload.get("transfer_data", []))
        )
        exceptions = (
            exceptions.copy()
            if isinstance(exceptions, pd.DataFrame)
            else pd.DataFrame(sales_payload.get("exceptions", []))
        )
        exception_packages = (
            exception_packages.copy()
            if isinstance(exception_packages, pd.DataFrame)
            else pd.DataFrame(sales_payload.get("exception_packages", []))
        )

    def filtered(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        result = frame
        if brand_filter != "All Brands" and "Brand" in result:
            result = result[result["Brand"].astype(str).eq(brand_filter)]
        if strain_filter != "All Strains" and "Strain" in result:
            needle = strain_filter.strip().lower()
            result = result[
                result["Strain"].astype(str).str.lower().str.contains(
                    re.escape(needle), na=False
                )
            ]
        if sku_filter != "All SKU Types" and "SKU Type" in result:
            result = result[result["SKU Type"].astype(str).apply(
                lambda value: sku_filter in {
                    part.strip() for part in value.split(",")
                }
            )]
        search = search_text.strip().lower()
        if search:
            searchable = result.fillna("").astype(str).agg(" ".join, axis=1)
            result = result[
                searchable.str.lower().str.contains(re.escape(search), na=False)
            ]
        return result

    if view == "exceptions":
        if not exception_packages.empty and "State" in exception_packages:
            exception_packages = exception_packages[
                exception_packages["State"].astype(str).eq(exception_state)
            ]
        exception_packages = filtered(exception_packages)
        package_records = record_list(exception_packages)
        keys = {
            (str(row.get("Manifest", "")), str(row.get("State", "")))
            for row in package_records
        }
        if not exceptions.empty:
            exceptions = exceptions[exceptions.apply(
                lambda row: (
                    str(row.get("Manifest", "")),
                    str(row.get("State", "")),
                ) in keys,
                axis=1,
            )]
        total = len(exception_packages)
        safe_size = max(int(page_size or 50), 1)
        total_pages = max((total + safe_size - 1) // safe_size, 1)
        safe_page = min(max(int(page or 1), 1), total_pages)
        start = (safe_page - 1) * safe_size
        return {
            "exceptions": record_list(exceptions),
            "exception_packages": record_list(
                exception_packages.iloc[start:start + safe_size]
            ),
            "exception_total": total,
            "exception_manifests": len({manifest for manifest, _ in keys}),
            "exception_value": round(sum(
                float(row.get("Shipper Value", 0) or 0)
                for row in package_records
            ), 2),
            "exception_page": safe_page,
        }

    transfers = filtered(transfers)
    total = len(transfers)
    safe_size = max(int(page_size or 50), 1)
    total_pages = max((total + safe_size - 1) // safe_size, 1)
    safe_page = min(max(int(page or 1), 1), total_pages)
    start = (safe_page - 1) * safe_size
    return {
        "transfer_data": record_list(transfers.iloc[start:start + safe_size]),
        "transfer_total": total,
        "transfer_page": safe_page,
        "transfer_import_log": sales_payload.get("transfer_import_log", []),
    }


def demo_dashboard_data() -> dict[str, Any]:
    """Allow the interface to start before a Supabase secret is configured."""
    today = date.today()
    velocity = [
        {
            "Brand": "Clade9", "Strain": "Diamond Bar",
            "SKU Type": "3.5g Flower", "Units Shipped": 12500,
            "Avg Weekly Units": 525.0,
            "Avg Weekly Units - Last 30 Days": 560.0,
            "Packages": 4, "Current Units": 1410,
            "Weeks of Supply": 2.69,
            "Potential Matching WIP": "12.5 lb",
            "Potential WIP Summary": "6 packages | Ages 18-74 days | Sizes 1.1-3.4 lb per lot",
            "Committed WIP": "0.0 g",
            "Matching Pre-WIP Weight": "3.2 lb",
            "Customers": 42,
            "Demand Status": "Stockout Risk Within 4 Weeks",
            "Last Shipped": str(today), "Days Since Last Shipment": 0,
            "Lifecycle Status": "Active",
            "Lifecycle Reason": "Current inventory exists",
        },
        {
            "Brand": "Craft Kings", "Strain": "Hybrid Blend",
            "SKU Type": "1g Pre-Roll", "Units Shipped": 9300,
            "Avg Weekly Units": 410.0,
            "Avg Weekly Units - Last 30 Days": 430.0,
            "Packages": 0, "Current Units": 0,
            "Weeks of Supply": 0.0,
            "Potential Matching WIP": "8.4 lb",
            "Potential WIP Summary": "14 packages | Ages 9-103 days | Sizes 98.0 g-1.6 lb per lot",
            "Committed WIP": "0.0 g",
            "Matching Pre-WIP Weight": "1.1 lb",
            "Customers": 35,
            "Demand Status": "Current Stockout",
            "Last Shipped": str(today), "Days Since Last Shipment": 0,
            "Lifecycle Status": "Active",
            "Lifecycle Reason": "Shipped to a customer within 90 days",
        },
    ]
    adjusted_demo_velocity = [
        {
            **row,
            "Likely OOS Weeks": 0,
            "Recent Gap Weeks": 0,
            "Availability Weeks": 0.0,
            "Velocity Model": "Current SKU Velocity — demo fallback",
        }
        for row in velocity
    ]
    return {
        "metrics": {
            "units": 21800, "value": 192500, "customers": 48,
            "manifests": 84, "weighted_price": 8.83, "stockouts": 1,
            "latest_shipment": str(today), "open_manifests": 0,
            "exception_manifests": 0, "exception_rows": 0,
            "transfer_rows": 2,
        },
        "snapshot": {"business_date": str(today), "published_at": "Demo", "package_count": 2516, "sku_count": 187, "detail_count": 0, "authoritative_cpg_count": 0, "classification_rule_version": ""},
        "brands": ["Clade9", "Craft Kings"],
        "strains": ["Diamond Bar", "Hybrid Blend"],
        "sku_types": ["1g Pre-Roll", "3.5g Flower"],
        "monthly": [
            {"Month": (today - timedelta(days=30)).strftime("%Y-%m"), "Units": 9500, "Value": 84000, "Customers": 31},
            {"Month": today.strftime("%Y-%m"), "Units": 12300, "Value": 108500, "Customers": 38},
        ],
        "top_skus": [
            {"Brand": row["Brand"], "Strain": row["Strain"], "SKU Type": row["SKU Type"], "Units": row["Units Shipped"], "Value": 0, "Customers": row["Customers"]}
            for row in velocity
        ],
        "business_pulse": [
            {
                "Brand": row["Brand"], "Strain": row["Strain"],
                "SKU Type": row["SKU Type"], "Customer License": "Demo",
                "Manifest": f"DEMO-{index + 1}",
                "Units": row["Units Shipped"], "Value": 0,
            }
            for index, row in enumerate(velocity)
        ],
        "velocity": velocity,
        "velocity_windows": {
            "1 Week": velocity, "30 Days": velocity, "60 Days": velocity,
            "90 Days": velocity, "120 Days": velocity, "All Time": velocity,
        },
        "availability_adjusted_velocity_windows": {
            "1 Week": adjusted_demo_velocity,
            "30 Days": adjusted_demo_velocity,
            "60 Days": adjusted_demo_velocity,
            "90 Days": adjusted_demo_velocity,
            "120 Days": adjusted_demo_velocity,
            "All Time": adjusted_demo_velocity,
        },
        "stockouts": [velocity[1]],
        "saved_plans": [],
        "saved_plan_cards": [],
        "production_templates": [],
        "calendar": [],
        "customers": [],
        "retail_delivery_history": [],
        "retailer_locations": _directory_retailer_location_rows(),
        "exceptions": [],
        "exception_packages": [],
        "transfer_data": [],
        "transfer_import_log": [],
        "inventory_ready": False,
        "authoritative_cpg_ready": False,
        "cpg_inventory": [],
        "bulk_inventory": [],
        "wip_inventory": [],
        "aging_cpg": [],
        "aging_bulk": [],
        "all_inventory": [],
        "needs_review": [],
        "loaded_at": datetime.now().astimezone().strftime("%Y-%m-%d %I:%M %p %Z"),
        "rule_version": "Demo data - publish Streamlit 81.4 for authoritative CPG rules",
    }
