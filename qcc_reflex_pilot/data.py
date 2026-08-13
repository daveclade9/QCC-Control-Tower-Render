"""Shared Supabase access and dashboard summaries for the Reflex pilot."""

from __future__ import annotations

import os
import hashlib
import json
import math
import threading
import time
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
    RETIRED_OR_ON_HOLD_STRAINS,
    gummy_variant,
    normalize_strain_name,
    prepare_transfer_analysis,
)


load_dotenv()

PILOT_CACHE_SECONDS = 300
_DASHBOARD_CACHE: dict[str, Any] = {"loaded_at": 0.0, "payload": None}
_DASHBOARD_CACHE_LOCK = threading.Lock()

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


def query_frame(query: str, parameters: tuple[Any, ...] = ()) -> pd.DataFrame:
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
    with psycopg.connect(url, connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, parameters)
            if not cursor.description:
                return pd.DataFrame()
            columns = [column.name for column in cursor.description]
            return pd.DataFrame(cursor.fetchall(), columns=columns)


def safe_query_frame(
    query: str, parameters: tuple[Any, ...] = ()
) -> pd.DataFrame:
    """Allow pilot sections to remain available when an optional table is absent."""
    try:
        return query_frame(query, parameters)
    except Exception as error:
        if psycopg is not None and isinstance(
            error, psycopg.errors.UndefinedTable
        ):
            return pd.DataFrame()
        raise


def load_transfer_rows() -> pd.DataFrame:
    selected = ", ".join(TRANSFER_COLUMNS)
    return query_frame(
        f"SELECT {selected} FROM transfer_records "
        "WHERE origin_facility = %s AND COALESCE(voided, 0) = 0 "
        "ORDER BY created_at",
        ("The QCC Group LLC",),
    )


def load_transfer_import_log() -> pd.DataFrame:
    return safe_query_frame(
        "SELECT source_filename, file_size_bytes, source_rows, stored_rows, "
        "inserted_rows, updated_rows, created_min, created_max, imported_at "
        "FROM transfer_import_log ORDER BY imported_at DESC"
    )


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


def load_production_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    plans = safe_query_frame(
        "SELECT * FROM production_plans ORDER BY created_at DESC"
    )
    outputs = safe_query_frame(
        "SELECT * FROM production_plan_outputs "
        "ORDER BY plan_id, unit_weight_grams DESC"
    )
    sources = safe_query_frame(
        "SELECT plan_id, package_tag, allocated_weight_grams "
        "FROM production_plan_sources ORDER BY plan_id, package_tag"
    )
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
                "SELECT snapshot_id FROM inventory_snapshots "
                "WHERE status = 'Published' ORDER BY published_at DESC LIMIT 1"
            )
            latest = cursor.fetchone()
            if not latest:
                raise ValueError("No published inventory snapshot is available.")
            cursor.execute(
                "SELECT * FROM inventory_snapshot_packages WHERE snapshot_id = %s",
                (latest[0],),
            )
            packages = _cursor_frame(cursor)
            cursor.execute("SELECT * FROM production_plans")
            plans = _cursor_frame(cursor)
            cursor.execute(
                "SELECT plan_id, package_tag, allocated_weight_grams "
                "FROM production_plan_sources"
            )
            sources = _cursor_frame(cursor)
            existing_plan = pd.DataFrame()
            if editing:
                existing_plan = plans[
                    plans["plan_id"].astype(str).eq(plan_id)
                ].copy()
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
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE["loaded_at"] = 0.0
        _DASHBOARD_CACHE["payload"] = None
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
                "SELECT plan_id FROM production_plans WHERE plan_id = ANY(%s)",
                (clean_ids,),
            )
            existing_ids = [str(row[0]) for row in cursor.fetchall()]
            if not existing_ids:
                raise ValueError("The selected production plans were not found.")
            cursor.execute(
                "DELETE FROM production_plan_audit WHERE plan_id = ANY(%s)",
                (existing_ids,),
            )
            cursor.execute(
                "DELETE FROM production_plan_sources WHERE plan_id = ANY(%s)",
                (existing_ids,),
            )
            cursor.execute(
                "DELETE FROM production_plan_outputs WHERE plan_id = ANY(%s)",
                (existing_ids,),
            )
            cursor.execute(
                "DELETE FROM production_plans WHERE plan_id = ANY(%s)",
                (existing_ids,),
            )
        connection.commit()
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE["loaded_at"] = 0.0
        _DASHBOARD_CACHE["payload"] = None
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
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE["loaded_at"] = 0.0
        _DASHBOARD_CACHE["payload"] = None
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
    result = analysis.copy()
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


def record_list(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.copy().replace({pd.NA: None})
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[column]):
            clean[column] = clean[column].dt.strftime("%Y-%m-%d")
    return clean.where(pd.notna(clean), None).to_dict("records")


def build_velocity(
    demand: pd.DataFrame,
    inventory_skus: pd.DataFrame,
    inventory_packages: pd.DataFrame,
    plans: pd.DataFrame,
    outputs: pd.DataFrame,
    sources: pd.DataFrame,
    all_history_demand: pd.DataFrame | None = None,
    period_days: int | None = None,
) -> pd.DataFrame:
    columns = [
        "Brand", "Strain", "SKU Type", "Units Shipped",
        "Avg Weekly Units", "Packages",
        "Current Units", "Weeks of Supply", "Potential Matching WIP",
        "Potential WIP Summary", "Committed WIP", "Matching Pre-WIP Weight", "Customers",
        "Demand Status",
    ]
    history = all_history_demand if all_history_demand is not None else demand
    if history.empty:
        return pd.DataFrame(columns=columns)
    history_first = history.groupby(
        ["brand", "strain", "sku_type"], dropna=False
    ).agg(first_shipped=("created_at", "min")).reset_index()
    grouped = demand.groupby(
        ["brand", "strain", "sku_type"], dropna=False
    ).agg(
        units_shipped=("shipped_units", "sum"),
        customers=("destination_license", "nunique"),
        last_shipped=("created_at", "max"),
    ).reset_index()
    grouped = history_first.merge(
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
    available_wip = available_wip_inventory(inventory_packages, plans, sources)
    pre_wip = inventory_packages[
        inventory_packages.get("production_stage", pd.Series(dtype=str)).eq("Pre-WIP")
    ].copy() if not inventory_packages.empty else pd.DataFrame()
    potential_labels = []
    potential_summaries = []
    pre_wip_labels = []
    for row in grouped.itertuples(index=False):
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
        "packages": "Packages",
        "current_units": "Current Units",
        "weeks_of_supply": "Weeks of Supply",
        "customers": "Customers", "demand_status": "Demand Status",
        "potential_matching_wip": "Potential Matching WIP",
        "potential_wip_summary": "Potential WIP Summary",
        "committed_wip": "Committed WIP",
        "matching_pre_wip_weight": "Matching Pre-WIP Weight",
    })[columns]
    for column in [
        "Units Shipped", "Avg Weekly Units", "Current Units",
        "Weeks of Supply",
    ]:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0).round(2)
    return result.sort_values(
        ["Units Shipped", "Brand", "Strain", "SKU Type"],
        ascending=[False, True, True, True],
    ).reset_index(drop=True)


def build_saved_plan_rows(
    plans: pd.DataFrame, outputs: pd.DataFrame, sources: pd.DataFrame
) -> pd.DataFrame:
    columns = [
        "Plan ID", "Plan Name", "Status", "Target Date",
        "Department", "Brand", "Strain", "SKU Type", "Allocation %",
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
        cards.append({
            "Plan ID": plan_id,
            "Plan Name": str(plan.get("plan_name", "") or ""),
            "Status": str(plan.get("status", "") or ""),
            "Target Date": iso_date(plan.get("target_packaging_date")),
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
        & ~data["brand"].isin([
            "Craft Kings", "Royal Smalls", "Clade9", "Locals Only",
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


def build_dashboard_data() -> dict[str, Any]:
    transfers = load_transfer_rows()
    snapshot, inventory_skus = load_latest_inventory_skus()
    inventory_packages = load_latest_inventory_packages(
        str(snapshot.get("snapshot_id", ""))
    )
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
    plans, outputs, sources = load_production_data()
    production_templates = load_reflex_production_templates()
    analysis = prepare_transfer_analysis(transfers)
    analysis = apply_inventory_master(analysis, inventory_skus)
    demand = analysis[analysis["is_demand"]].copy()
    velocity = build_velocity(
        demand, inventory_skus, inventory_packages, plans, outputs, sources
    )
    velocity_windows: dict[str, list[dict[str, Any]]] = {
        "All Time": record_list(velocity),
    }
    if not demand.empty:
        velocity_end = pd.Timestamp(demand["created_at"].max()).normalize()
        for label, days in [("1 Week", 7), ("30 Days", 30), ("90 Days", 90)]:
            window_start = velocity_end - pd.Timedelta(days=days - 1)
            window_demand = demand[
                pd.to_datetime(demand["created_at"], errors="coerce").ge(window_start)
            ].copy()
            velocity_windows[label] = record_list(build_velocity(
                window_demand,
                inventory_skus,
                inventory_packages,
                plans,
                outputs,
                sources,
                all_history_demand=demand,
                period_days=days,
            ))
    else:
        velocity_windows.update({
            "1 Week": [], "30 Days": [], "90 Days": [],
        })
    inventory_views = build_inventory_views(inventory_packages, plans, sources)
    stockouts = velocity[
        velocity["Demand Status"].eq("Current Stockout")
        & ~velocity["Strain"].fillna("").str.lower().isin(
            RETIRED_OR_ON_HOLD_STRAINS
        )
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
            "Brand": card["Brand"],
            "Strain": card["Strain"],
            "SKU Type": card["SKU Type"],
            "Output Summary": card["Output Summary"],
        }
        for card in saved_plan_cards
        if card["Target Date"]
    ]
    customers = build_customer_summary(analysis)
    exceptions = build_shipment_exceptions(analysis)
    transfer_display = build_transfer_display(analysis)
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
        "stockouts": record_list(stockouts),
        "saved_plans": record_list(saved_plans),
        "saved_plan_cards": saved_plan_cards,
        "production_templates": record_list(production_templates),
        "calendar": sorted(calendar, key=lambda row: row["Target Date"]),
        "customers": record_list(customers),
        "exceptions": record_list(exceptions),
        "transfer_data": record_list(transfer_display.head(2000)),
        "transfer_import_log": record_list(transfer_import_log),
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
    """Reuse one immutable five-minute payload across users.

    Callers treat the returned collections as read-only. Returning the shared
    payload avoids a full deep copy for every login and tab change.
    """
    now = time.monotonic()
    with _DASHBOARD_CACHE_LOCK:
        payload = _DASHBOARD_CACHE.get("payload")
        age = now - float(_DASHBOARD_CACHE.get("loaded_at", 0.0))
        if payload is not None and not force_refresh and age < PILOT_CACHE_SECONDS:
            return payload
        payload = build_dashboard_data()
        _DASHBOARD_CACHE["payload"] = payload
        _DASHBOARD_CACHE["loaded_at"] = time.monotonic()
        return payload


def demo_dashboard_data() -> dict[str, Any]:
    """Allow the interface to start before a Supabase secret is configured."""
    today = date.today()
    velocity = [
        {
            "Brand": "Clade9", "Strain": "Diamond Bar",
            "SKU Type": "3.5g Flower", "Units Shipped": 12500,
            "Avg Weekly Units": 525.0, "Packages": 4, "Current Units": 1410,
            "Weeks of Supply": 2.69,
            "Potential Matching WIP": "12.5 lb",
            "Potential WIP Summary": "6 packages | Ages 18-74 days | Sizes 1.1-3.4 lb per lot",
            "Committed WIP": "0.0 g",
            "Matching Pre-WIP Weight": "3.2 lb",
            "Customers": 42,
            "Demand Status": "Stockout Risk Within 4 Weeks",
        },
        {
            "Brand": "Craft Kings", "Strain": "Hybrid Blend",
            "SKU Type": "1g Pre-Roll", "Units Shipped": 9300,
            "Avg Weekly Units": 410.0, "Packages": 0, "Current Units": 0,
            "Weeks of Supply": 0.0,
            "Potential Matching WIP": "8.4 lb",
            "Potential WIP Summary": "14 packages | Ages 9-103 days | Sizes 98.0 g-1.6 lb per lot",
            "Committed WIP": "0.0 g",
            "Matching Pre-WIP Weight": "1.1 lb",
            "Customers": 35,
            "Demand Status": "Current Stockout",
        },
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
            "1 Week": velocity, "30 Days": velocity,
            "90 Days": velocity, "All Time": velocity,
        },
        "stockouts": [velocity[1]],
        "saved_plans": [],
        "saved_plan_cards": [],
        "production_templates": [],
        "calendar": [],
        "customers": [],
        "exceptions": [],
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
