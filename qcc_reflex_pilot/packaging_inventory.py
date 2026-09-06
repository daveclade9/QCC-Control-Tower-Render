"""Workbook-seeded packaging inventory foundation for Materials & Procurement."""

from __future__ import annotations

import json
import re
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError:
    psycopg = None

from .data import database_url, safe_query_frame


SEED_PATH = Path(__file__).with_name("packaging_inventory_seed.json")
_PACKAGING_SCHEMA_LOCK = threading.Lock()
_PACKAGING_SCHEMA_READY = False


@lru_cache(maxsize=1)
def packaging_seed() -> dict[str, Any]:
    """Load the normalized, non-sensitive workbook seed bundled with staging."""
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def _seed_item_defaults(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row.update({
        "uom": row.get("uom", "Each"),
        "brand_scope": row.get("brand_scope", "Shared"),
        "length": float(row.get("length", 0) or 0),
        "width": float(row.get("width", 0) or 0),
        "height": float(row.get("height", 0) or 0),
        "dimensions_uom": row.get("dimensions_uom", "in"),
        "units_per_case": float(row.get("units_per_case", 1) or 1),
        "default_location": row.get("default_location", "Unassigned"),
        "lot_tracking": bool(row.get("lot_tracking", False)),
        "expiration_tracking": bool(row.get("expiration_tracking", False)),
        "reorder_point": float(row.get("reorder_point", 0) or 0),
        "safety_stock": float(row.get("safety_stock", 0) or 0),
        "unit_cost": float(row.get("unit_cost", 0) or 0),
        "allocated": float(row.get("allocated", 0) or 0),
        "on_order": float(row.get("on_order", 0) or 0),
        "in_transit": float(row.get("in_transit", 0) or 0),
        "status": row.get("status", "Active"),
        "source": row.get("source", "Workbook Seed"),
    })
    row["available"] = max(float(row.get("on_hand", 0) or 0) - row["allocated"], 0)
    return row


def _initialize_packaging_database() -> None:
    """Create the editable item master and auditable material ledger once."""
    global _PACKAGING_SCHEMA_READY
    if _PACKAGING_SCHEMA_READY:
        return
    if not database_url() or psycopg is None:
        raise RuntimeError("Supabase is required to edit packaging inventory.")
    with _PACKAGING_SCHEMA_LOCK:
        if _PACKAGING_SCHEMA_READY:
            return
        with psycopg.connect(database_url(), connect_timeout=15) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS qcc_packaging_items (
                    material_id TEXT PRIMARY KEY,
                    item_name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Other',
                    uom TEXT NOT NULL DEFAULT 'Each',
                    brand_scope TEXT NOT NULL DEFAULT 'Shared',
                    primary_vendor TEXT NOT NULL DEFAULT '',
                    ownership TEXT NOT NULL DEFAULT 'QCC Owned',
                    length DOUBLE PRECISION NOT NULL DEFAULT 0,
                    width DOUBLE PRECISION NOT NULL DEFAULT 0,
                    height DOUBLE PRECISION NOT NULL DEFAULT 0,
                    dimensions_uom TEXT NOT NULL DEFAULT 'in',
                    units_per_case DOUBLE PRECISION NOT NULL DEFAULT 1,
                    default_location TEXT NOT NULL DEFAULT 'Unassigned',
                    lot_tracking BOOLEAN NOT NULL DEFAULT FALSE,
                    expiration_tracking BOOLEAN NOT NULL DEFAULT FALSE,
                    reorder_point DOUBLE PRECISION NOT NULL DEFAULT 0,
                    safety_stock DOUBLE PRECISION NOT NULL DEFAULT 0,
                    unit_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    notes TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'Control Tower',
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS qcc_packaging_inventory_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    material_id TEXT NOT NULL REFERENCES qcc_packaging_items(material_id),
                    transaction_type TEXT NOT NULL,
                    quantity_delta DOUBLE PRECISION NOT NULL,
                    uom TEXT NOT NULL DEFAULT 'Each',
                    location TEXT NOT NULL DEFAULT 'Unassigned',
                    lot_number TEXT NOT NULL DEFAULT '',
                    expiration_date DATE,
                    unit_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    reference TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_qcc_packaging_tx_material "
                "ON qcc_packaging_inventory_transactions(material_id, occurred_at DESC)"
            )
        _PACKAGING_SCHEMA_READY = True


def packaging_seed_items() -> list[dict[str, Any]]:
    """Return the local workbook items without making a database request."""
    return [_seed_item_defaults(row) for row in packaging_seed().get("items", [])]


def packaging_items() -> list[dict[str, Any]]:
    """Return workbook items merged with editable Supabase item overrides."""
    seed_rows = packaging_seed_items()
    by_id = {str(row["material_id"]): row for row in seed_rows}
    if not database_url():
        return list(by_id.values())
    rows = safe_query_frame("""
        SELECT item.*,
               COALESCE((
                   SELECT SUM(tx.quantity_delta)
                   FROM qcc_packaging_inventory_transactions tx
                   WHERE tx.material_id = item.material_id
               ), 0) AS transaction_balance,
               COALESCE((
                   SELECT MAX(tx.occurred_at)::TEXT
                   FROM qcc_packaging_inventory_transactions tx
                   WHERE tx.material_id = item.material_id
               ), '') AS latest_transaction_at
        FROM qcc_packaging_items item
        ORDER BY item.material_id
    """)
    for db_row in rows.to_dict("records"):
        material_id = str(db_row.get("material_id", "") or "")
        base = dict(by_id.get(material_id, {"material_id": material_id, "on_hand": 0}))
        seed_balance = float(base.get("on_hand", 0) or 0)
        base.update({
            "item": str(db_row.get("item_name", "") or ""),
            "category": str(db_row.get("category", "") or "Other"),
            "uom": str(db_row.get("uom", "") or "Each"),
            "brand_scope": str(db_row.get("brand_scope", "") or "Shared"),
            "vendor": str(db_row.get("primary_vendor", "") or ""),
            "ownership": str(db_row.get("ownership", "") or "QCC Owned"),
            "length": float(db_row.get("length", 0) or 0),
            "width": float(db_row.get("width", 0) or 0),
            "height": float(db_row.get("height", 0) or 0),
            "dimensions_uom": str(db_row.get("dimensions_uom", "") or "in"),
            "units_per_case": float(db_row.get("units_per_case", 1) or 1),
            "default_location": str(db_row.get("default_location", "") or "Unassigned"),
            "lot_tracking": bool(db_row.get("lot_tracking", False)),
            "expiration_tracking": bool(db_row.get("expiration_tracking", False)),
            "reorder_point": float(db_row.get("reorder_point", 0) or 0),
            "safety_stock": float(db_row.get("safety_stock", 0) or 0),
            "unit_cost": float(db_row.get("unit_cost", 0) or 0),
            "status": "Active" if bool(db_row.get("is_active", True)) else "Inactive",
            "notes": str(db_row.get("notes", "") or ""),
            "source": str(db_row.get("source", "") or "Control Tower"),
            "on_hand": seed_balance + float(db_row.get("transaction_balance", 0) or 0),
        })
        latest = str(db_row.get("latest_transaction_at", "") or "")[:10]
        if latest:
            base["latest_count_date"] = latest
        base["allocated"] = 0.0
        base["on_order"] = 0.0
        base["in_transit"] = 0.0
        base["available"] = max(float(base["on_hand"]), 0)
        by_id[material_id] = _seed_item_defaults(base)
    return sorted(by_id.values(), key=lambda row: str(row.get("material_id", "")))


def next_packaging_material_id(rows: list[dict[str, Any]] | None = None) -> str:
    values = rows if rows is not None else packaging_items()
    numbers = [
        int(match.group(1))
        for row in values
        if (match := re.fullmatch(r"PKG-(\d+)", str(row.get("material_id", ""))))
    ]
    return f"PKG-{(max(numbers, default=0) + 1):04d}"


def save_packaging_item(
    record: dict[str, Any],
    initial_quantity: float = 0,
    updated_by: str = "QCC Control Tower",
    allow_update: bool = False,
) -> str:
    """Create or edit an item; opening stock is always written to the ledger."""
    _initialize_packaging_database()
    material_id = str(record.get("material_id", "") or "").strip().upper()
    if not material_id:
        material_id = next_packaging_material_id()
    item_name = str(record.get("item", "") or "").strip()
    if not item_name:
        raise ValueError("Item name is required.")
    if initial_quantity > 0 and (
        bool(record.get("lot_tracking", False))
        or bool(record.get("expiration_tracking", False))
    ):
        raise ValueError(
            "Create tracked items with zero opening quantity, then use Receive "
            "Inventory to capture the required lot and expiration details."
        )
    existing = safe_query_frame(
        "SELECT material_id FROM qcc_packaging_items WHERE material_id = %s",
        (material_id,),
    )
    seed_exists = any(
        str(row.get("material_id", "")) == material_id
        for row in packaging_seed().get("items", [])
    )
    if (not existing.empty or seed_exists) and not allow_update:
        raise ValueError(f"Material ID {material_id} already exists.")
    numeric = lambda key, default=0: float(record.get(key, default) or default)
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute("""
            INSERT INTO qcc_packaging_items (
                material_id, item_name, category, uom, brand_scope,
                primary_vendor, ownership, length, width, height,
                dimensions_uom, units_per_case, default_location,
                lot_tracking, expiration_tracking, reorder_point, safety_stock,
                unit_cost, is_active, notes, source, created_by, updated_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, 'Control Tower', %s, %s
            ) ON CONFLICT(material_id) DO UPDATE SET
                item_name=EXCLUDED.item_name, category=EXCLUDED.category,
                uom=EXCLUDED.uom, brand_scope=EXCLUDED.brand_scope,
                primary_vendor=EXCLUDED.primary_vendor, ownership=EXCLUDED.ownership,
                length=EXCLUDED.length, width=EXCLUDED.width, height=EXCLUDED.height,
                dimensions_uom=EXCLUDED.dimensions_uom,
                units_per_case=EXCLUDED.units_per_case,
                default_location=EXCLUDED.default_location,
                lot_tracking=EXCLUDED.lot_tracking,
                expiration_tracking=EXCLUDED.expiration_tracking,
                reorder_point=EXCLUDED.reorder_point,
                safety_stock=EXCLUDED.safety_stock, unit_cost=EXCLUDED.unit_cost,
                is_active=EXCLUDED.is_active, notes=EXCLUDED.notes,
                updated_by=EXCLUDED.updated_by, updated_at=NOW()
        """, (
            material_id, item_name, str(record.get("category", "Other") or "Other"),
            str(record.get("uom", "Each") or "Each"),
            str(record.get("brand_scope", "Shared") or "Shared"),
            str(record.get("vendor", "") or ""),
            str(record.get("ownership", "QCC Owned") or "QCC Owned"),
            numeric("length"), numeric("width"), numeric("height"),
            str(record.get("dimensions_uom", "in") or "in"),
            numeric("units_per_case", 1),
            str(record.get("default_location", "Unassigned") or "Unassigned"),
            bool(record.get("lot_tracking", False)),
            bool(record.get("expiration_tracking", False)),
            numeric("reorder_point"), numeric("safety_stock"), numeric("unit_cost"),
            str(record.get("status", "Active")) != "Inactive",
            str(record.get("notes", "") or ""), updated_by, updated_by,
        ))
        if initial_quantity > 0 and existing.empty and not seed_exists:
            connection.execute("""
                INSERT INTO qcc_packaging_inventory_transactions (
                    transaction_id, material_id, transaction_type, quantity_delta,
                    uom, location, unit_cost, reference, notes, created_by
                ) VALUES (%s, %s, 'Opening Balance', %s, %s, %s, %s,
                          'Item creation', 'Initial quantity', %s)
            """, (
                str(uuid.uuid4()), material_id, float(initial_quantity),
                str(record.get("uom", "Each") or "Each"),
                str(record.get("default_location", "Unassigned") or "Unassigned"),
                numeric("unit_cost"), updated_by,
            ))
    return material_id


def receive_packaging_inventory(
    material_id: str,
    quantity: float,
    location: str,
    lot_number: str = "",
    expiration_date: str = "",
    unit_cost: float = 0,
    reference: str = "",
    notes: str = "",
    created_by: str = "QCC Control Tower",
) -> str:
    """Record a positive receipt against an existing packaging item."""
    _initialize_packaging_database()
    quantity = float(quantity or 0)
    if quantity <= 0:
        raise ValueError("Received quantity must be greater than zero.")
    material_id = str(material_id or "").strip().upper()
    # A workbook item receives a lightweight master override before its first transaction.
    item = next((row for row in packaging_items() if row.get("material_id") == material_id), None)
    if not item:
        raise ValueError("Select a valid packaging item.")
    if bool(item.get("lot_tracking", False)) and not str(lot_number or "").strip():
        raise ValueError("A supplier lot is required for this item.")
    if bool(item.get("expiration_tracking", False)) and not str(expiration_date or "").strip():
        raise ValueError("An expiration date is required for this item.")
    save_packaging_item(item, updated_by=created_by, allow_update=True)
    transaction_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute("""
            INSERT INTO qcc_packaging_inventory_transactions (
                transaction_id, material_id, transaction_type, quantity_delta,
                uom, location, lot_number, expiration_date, unit_cost,
                reference, notes, created_by
            ) VALUES (%s, %s, 'Receipt', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            transaction_id, material_id, quantity, str(item.get("uom", "Each")),
            str(location or item.get("default_location", "Unassigned")),
            str(lot_number or ""), expiration_date or None, float(unit_cost or 0),
            str(reference or ""), str(notes or ""), created_by,
        ))
    return transaction_id


def deactivate_packaging_item(material_id: str, updated_by: str) -> None:
    _initialize_packaging_database()
    item = next((row for row in packaging_items() if row.get("material_id") == material_id), None)
    if not item:
        raise ValueError("Packaging item was not found.")
    item["status"] = "Inactive"
    save_packaging_item(item, updated_by=updated_by, allow_update=True)


def packaging_planning_rows() -> list[dict[str, Any]]:
    return list(packaging_seed().get("planning", []))


def packaging_bom_recipes() -> list[dict[str, Any]]:
    return list(packaging_seed().get("bom_recipes", []))


def packaging_suppliers() -> list[dict[str, Any]]:
    return list(packaging_seed().get("suppliers", []))


def packaging_snapshot_rows() -> list[dict[str, Any]]:
    """Summarize each historical count without combining unlike unit measures."""
    items = packaging_items()
    dates = packaging_seed().get("snapshot_dates", [])
    rows: list[dict[str, Any]] = []
    for count_date in reversed(dates):
        observations = [
            point
            for item in items
            for point in item.get("history", [])
            if point.get("date") == count_date
        ]
        rows.append(
            {
                "count_date": count_date,
                "items_counted": len(observations),
                "nonzero_items": sum(float(point.get("quantity", 0) or 0) > 0 for point in observations),
                "zero_items": sum(float(point.get("quantity", 0) or 0) == 0 for point in observations),
                "source": "Packaging Inventory workbook",
            }
        )
    return rows


def coverage_status_color(status: str) -> str:
    return {
        "Critical": "red",
        "Reorder Soon": "yellow",
        "Covered": "green",
        "No Demand / Review": "gray",
    }.get(status, "gray")
