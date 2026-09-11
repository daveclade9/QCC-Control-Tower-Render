"""Workbook-seeded packaging inventory foundation for Materials & Procurement."""

from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import nullcontext
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

PACKAGING_BRAND_SCOPES = ["CLADE9", "CRAFT KINGS", "LOCALS ONLY", "SHARED USE"]

PACKAGING_CATEGORIES = [
    "BOXES", "CLOSURES", "CONTAINERS", "CARTONS", "CASES", "CAPS",
    "LABELS", "LIDS", "GLASS JARS", "DIVIDERS", "VAPE HARDWARE",
    "GLASS TUBES", "PLASTIC JARS", "PLASTIC TUBES", "MYLAR BAG",
    "PRE-ROLL CONES", "TINS", "SEALS", "VAPE BOXES",
    "VAPE INSTRUCTIONS", "OTHER",
]

PACKAGING_SIZE_FORMATS = [
    "NOT APPLICABLE", "GENERIC", "1G", "3.5G", "7G", "14G", "28G",
]

_LEGACY_CATEGORY_MAP = {
    "GLASS JAR": "GLASS JARS",
    "GLASS TUBE": "GLASS TUBES",
    "PLASTIC JAR": "PLASTIC JARS",
    "BAGS & POUCHES": "MYLAR BAG",
    "CARTONS & CASES": "CARTONS",
    "PRE-ROLL COMPONENTS": "PRE-ROLL CONES",
    "OTHER MATERIALS": "OTHER",
}


def _upper(value: Any, default: str = "") -> str:
    return str(value or default).strip().upper()


def _normalized_category(value: Any) -> str:
    category = _upper(value, "OTHER")
    return _LEGACY_CATEGORY_MAP.get(category, category)


def _normalized_brand_scope(value: Any) -> str:
    scope = _upper(value, "SHARED USE")
    return "SHARED USE" if scope in {"SHARED", "SHARED-USE"} else scope


def _supplier_defaults(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row.update({
        "supplier_id": str(row.get("supplier_id", "") or ""),
        "supplier": _upper(row.get("supplier")),
        "supplies": _upper(row.get("supplies")),
        "payment_terms": _upper(row.get("payment_terms")),
        "contact_name": _upper(row.get("contact_name")),
        "contact_email": str(row.get("contact_email", "") or "").strip().lower(),
        "contact_phone": str(row.get("contact_phone", "") or "").strip(),
        "address_line_1": _upper(row.get("address_line_1")),
        "address_line_2": _upper(row.get("address_line_2")),
        "city": _upper(row.get("city")),
        "state": _upper(row.get("state")),
        "postal_code": _upper(row.get("postal_code")),
        "country": _upper(row.get("country"), "US"),
        "website": str(row.get("website", "") or "").strip(),
        "status": "ACTIVE" if _upper(row.get("status"), "ACTIVE") != "INACTIVE" else "INACTIVE",
    })
    return row


@lru_cache(maxsize=1)
def packaging_seed() -> dict[str, Any]:
    """Load the normalized, non-sensitive workbook seed bundled with staging."""
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def _seed_item_defaults(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row.update({
        "item": _upper(row.get("item")),
        "category": _normalized_category(row.get("category")),
        "uom": _upper(row.get("uom"), "EACH"),
        "brand_scope": _normalized_brand_scope(row.get("brand_scope")),
        "vendor": _upper(row.get("vendor")),
        "secondary_vendor": _upper(row.get("secondary_vendor")),
        "primary_lead_time_days": int(row.get("primary_lead_time_days", 0) or 0),
        "secondary_lead_time_days": int(row.get("secondary_lead_time_days", 0) or 0),
        "size_format": _upper(row.get("size_format"), "NOT APPLICABLE"),
        "ownership": _upper(row.get("ownership"), "QCC OWNED"),
        "length": float(row.get("length", 0) or 0),
        "width": float(row.get("width", 0) or 0),
        "height": float(row.get("height", 0) or 0),
        "dimensions_uom": row.get("dimensions_uom", "in"),
        "units_per_case": float(row.get("units_per_case", 1) or 1),
        "default_location": _upper(row.get("default_location"), "UNASSIGNED"),
        "lot_tracking": bool(row.get("lot_tracking", False)),
        "expiration_tracking": bool(row.get("expiration_tracking", False)),
        "reorder_point": float(row.get("reorder_point", 0) or 0),
        "safety_stock": float(row.get("safety_stock", 0) or 0),
        "unit_cost": float(row.get("unit_cost", 0) or 0),
        "allocated": float(row.get("allocated", 0) or 0),
        "on_order": float(row.get("on_order", 0) or 0),
        "in_transit": float(row.get("in_transit", 0) or 0),
        "status": "ACTIVE" if _upper(row.get("status"), "ACTIVE") != "INACTIVE" else "INACTIVE",
        "source": _upper(row.get("source"), "WORKBOOK SEED"),
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
            connection.execute(
                "ALTER TABLE qcc_packaging_items "
                "ADD COLUMN IF NOT EXISTS size_format TEXT NOT NULL DEFAULT 'NOT APPLICABLE'"
            )
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
            connection.execute("""
                CREATE TABLE IF NOT EXISTS qcc_packaging_suppliers (
                    supplier_id TEXT PRIMARY KEY,
                    supplier_name TEXT NOT NULL UNIQUE,
                    supplies TEXT NOT NULL DEFAULT '',
                    payment_terms TEXT NOT NULL DEFAULT '',
                    contact_name TEXT NOT NULL DEFAULT '',
                    contact_email TEXT NOT NULL DEFAULT '',
                    contact_phone TEXT NOT NULL DEFAULT '',
                    address_line_1 TEXT NOT NULL DEFAULT '',
                    address_line_2 TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT '',
                    postal_code TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT 'US',
                    website TEXT NOT NULL DEFAULT '',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    source TEXT NOT NULL DEFAULT 'Control Tower',
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS qcc_packaging_item_suppliers (
                    material_id TEXT NOT NULL REFERENCES qcc_packaging_items(material_id) ON DELETE CASCADE,
                    supplier_id TEXT NOT NULL REFERENCES qcc_packaging_suppliers(supplier_id),
                    priority INTEGER NOT NULL,
                    lead_time_days INTEGER NOT NULL DEFAULT 0,
                    supplier_item_number TEXT NOT NULL DEFAULT '',
                    minimum_order_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                    case_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                    last_unit_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    is_approved BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(material_id, supplier_id),
                    UNIQUE(material_id, priority),
                    CHECK(priority > 0),
                    CHECK(lead_time_days >= 0)
                )
            """)
        _PACKAGING_SCHEMA_READY = True


def packaging_seed_items() -> list[dict[str, Any]]:
    """Return the local workbook items without making a database request."""
    return [_seed_item_defaults(row) for row in packaging_seed().get("items", [])]


def packaging_items(_connection: Any = None) -> list[dict[str, Any]]:
    """Return workbook items merged with editable Supabase item overrides."""
    seed_rows = packaging_seed_items()
    by_id = {str(row["material_id"]): row for row in seed_rows}
    if not database_url():
        return list(by_id.values())
    if _connection is None:
        _initialize_packaging_database()
    query = """
        SELECT item.*,
               COALESCE(primary_supplier.supplier_name, item.primary_vendor, '') AS resolved_primary_vendor,
               COALESCE(primary_link.lead_time_days, 0) AS primary_lead_time_days,
               COALESCE(secondary_supplier.supplier_name, '') AS secondary_vendor,
               COALESCE(secondary_link.lead_time_days, 0) AS secondary_lead_time_days,
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
        LEFT JOIN qcc_packaging_item_suppliers primary_link
          ON primary_link.material_id = item.material_id AND primary_link.priority = 1
        LEFT JOIN qcc_packaging_suppliers primary_supplier
          ON primary_supplier.supplier_id = primary_link.supplier_id
        LEFT JOIN qcc_packaging_item_suppliers secondary_link
          ON secondary_link.material_id = item.material_id AND secondary_link.priority = 2
        LEFT JOIN qcc_packaging_suppliers secondary_supplier
          ON secondary_supplier.supplier_id = secondary_link.supplier_id
        ORDER BY item.material_id
    """
    if _connection is None:
        records = safe_query_frame(query).to_dict("records")
    else:
        from psycopg.rows import dict_row
        with _connection.cursor(row_factory=dict_row) as cursor:
            records = cursor.execute(query).fetchall()
    for db_row in records:
        material_id = str(db_row.get("material_id", "") or "")
        base = dict(by_id.get(material_id, {"material_id": material_id, "on_hand": 0}))
        seed_balance = float(base.get("on_hand", 0) or 0)
        base.update({
            "item": _upper(db_row.get("item_name")),
            "category": _normalized_category(db_row.get("category")),
            "uom": _upper(db_row.get("uom"), "EACH"),
            "brand_scope": _normalized_brand_scope(db_row.get("brand_scope")),
            "vendor": _upper(db_row.get("resolved_primary_vendor")),
            "secondary_vendor": _upper(db_row.get("secondary_vendor")),
            "primary_lead_time_days": int(db_row.get("primary_lead_time_days", 0) or 0),
            "secondary_lead_time_days": int(db_row.get("secondary_lead_time_days", 0) or 0),
            "size_format": _upper(db_row.get("size_format"), "NOT APPLICABLE"),
            "ownership": _upper(db_row.get("ownership"), "QCC OWNED"),
            "length": float(db_row.get("length", 0) or 0),
            "width": float(db_row.get("width", 0) or 0),
            "height": float(db_row.get("height", 0) or 0),
            "dimensions_uom": str(db_row.get("dimensions_uom", "") or "in"),
            "units_per_case": float(db_row.get("units_per_case", 1) or 1),
            "default_location": _upper(db_row.get("default_location"), "UNASSIGNED"),
            "lot_tracking": bool(db_row.get("lot_tracking", False)),
            "expiration_tracking": bool(db_row.get("expiration_tracking", False)),
            "reorder_point": float(db_row.get("reorder_point", 0) or 0),
            "safety_stock": float(db_row.get("safety_stock", 0) or 0),
            "unit_cost": float(db_row.get("unit_cost", 0) or 0),
            "status": "ACTIVE" if bool(db_row.get("is_active", True)) else "INACTIVE",
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


def _ensure_supplier(connection: Any, supplier_name: str, updated_by: str) -> str:
    supplier_name = _upper(supplier_name)
    existing = connection.execute(
        "SELECT supplier_id FROM qcc_packaging_suppliers WHERE supplier_name = %s",
        (supplier_name,),
    ).fetchone()
    if existing:
        return str(existing[0])
    supplier_id = str(uuid.uuid4())
    connection.execute("""
        INSERT INTO qcc_packaging_suppliers (
            supplier_id, supplier_name, created_by, updated_by
        ) VALUES (%s, %s, %s, %s)
    """, (supplier_id, supplier_name, updated_by, updated_by))
    return supplier_id


def save_packaging_item(
    record: dict[str, Any],
    initial_quantity: float = 0,
    updated_by: str = "QCC Control Tower",
    allow_update: bool = False,
    _connection: Any = None,
) -> str:
    """Create or edit an item; opening stock is always written to the ledger."""
    if _connection is None:
        _initialize_packaging_database()
    material_id = str(record.get("material_id", "") or "").strip().upper()
    if not material_id:
        material_id = next_packaging_material_id()
    item_name = _upper(record.get("item"))
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
    if _connection is None:
        exists = not safe_query_frame(
            "SELECT material_id FROM qcc_packaging_items WHERE material_id = %s",
            (material_id,),
        ).empty
    else:
        exists = bool(_connection.execute(
            "SELECT material_id FROM qcc_packaging_items WHERE material_id = %s",
            (material_id,),
        ).fetchone())
    seed_exists = any(
        str(row.get("material_id", "")) == material_id
        for row in packaging_seed().get("items", [])
    )
    if (exists or seed_exists) and not allow_update:
        raise ValueError(f"Material ID {material_id} already exists.")
    numeric = lambda key, default=0: float(record.get(key, default) or default)
    with (nullcontext(_connection) if _connection is not None else
          psycopg.connect(database_url(), connect_timeout=15)) as connection:
        connection.execute("""
            INSERT INTO qcc_packaging_items (
                material_id, item_name, category, uom, brand_scope,
                primary_vendor, ownership, size_format, length, width, height,
                dimensions_uom, units_per_case, default_location,
                lot_tracking, expiration_tracking, reorder_point, safety_stock,
                unit_cost, is_active, notes, source, created_by, updated_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, 'Control Tower', %s, %s
            ) ON CONFLICT(material_id) DO UPDATE SET
                item_name=EXCLUDED.item_name, category=EXCLUDED.category,
                uom=EXCLUDED.uom, brand_scope=EXCLUDED.brand_scope,
                primary_vendor=EXCLUDED.primary_vendor, ownership=EXCLUDED.ownership,
                size_format=EXCLUDED.size_format,
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
            material_id, item_name, _normalized_category(record.get("category")),
            _upper(record.get("uom"), "EACH"),
            _normalized_brand_scope(record.get("brand_scope")),
            _upper(record.get("vendor")),
            _upper(record.get("ownership"), "QCC OWNED"),
            _upper(record.get("size_format"), "NOT APPLICABLE"),
            numeric("length"), numeric("width"), numeric("height"),
            str(record.get("dimensions_uom", "in") or "in"),
            numeric("units_per_case", 1),
            _upper(record.get("default_location"), "UNASSIGNED"),
            bool(record.get("lot_tracking", False)),
            bool(record.get("expiration_tracking", False)),
            numeric("reorder_point"), numeric("safety_stock"), numeric("unit_cost"),
            _upper(record.get("status"), "ACTIVE") != "INACTIVE",
            _upper(record.get("notes")), updated_by, updated_by,
        ))
        connection.execute(
            "DELETE FROM qcc_packaging_item_suppliers "
            "WHERE material_id = %s AND priority IN (1, 2)",
            (material_id,),
        )
        for priority, vendor_key, lead_key in (
            (1, "vendor", "primary_lead_time_days"),
            (2, "secondary_vendor", "secondary_lead_time_days"),
        ):
            supplier_name = _upper(record.get(vendor_key))
            if not supplier_name:
                continue
            supplier_id = _ensure_supplier(connection, supplier_name, updated_by)
            connection.execute("""
                INSERT INTO qcc_packaging_item_suppliers (
                    material_id, supplier_id, priority, lead_time_days, updated_by
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT(material_id, supplier_id) DO UPDATE SET
                    priority=EXCLUDED.priority,
                    lead_time_days=EXCLUDED.lead_time_days,
                    updated_by=EXCLUDED.updated_by,
                    updated_at=NOW()
            """, (
                material_id, supplier_id, priority,
                max(0, int(record.get(lead_key, 0) or 0)), updated_by,
            ))
        if initial_quantity > 0 and not exists and not seed_exists:
            connection.execute("""
                INSERT INTO qcc_packaging_inventory_transactions (
                    transaction_id, material_id, transaction_type, quantity_delta,
                    uom, location, unit_cost, reference, notes, created_by
                ) VALUES (%s, %s, 'Opening Balance', %s, %s, %s, %s,
                          'Item creation', 'Initial quantity', %s)
            """, (
                str(uuid.uuid4()), material_id, float(initial_quantity),
                _upper(record.get("uom"), "EACH"),
                _upper(record.get("default_location"), "UNASSIGNED"),
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
    """Legacy receipt button uses the same atomic warehouse ledger."""
    from datetime import date
    from .warehouse import post_activity
    return post_activity({
        "material_id": material_id, "action": "Receive", "quantity": quantity,
        "location": location or "UNASSIGNED", "lot": lot_number,
        "expiration": expiration_date, "unit_cost": unit_cost,
        "reference": reference, "reason": notes or "Inventory receipt",
        "date": date.today().isoformat(),
    }, created_by, str(uuid.uuid4()))


def deactivate_packaging_item(material_id: str, updated_by: str) -> None:
    _initialize_packaging_database()
    item = next((row for row in packaging_items() if row.get("material_id") == material_id), None)
    if not item:
        raise ValueError("Packaging item was not found.")
    item["status"] = "INACTIVE"
    save_packaging_item(item, updated_by=updated_by, allow_update=True)


def packaging_planning_rows() -> list[dict[str, Any]]:
    return list(packaging_seed().get("planning", []))


def packaging_bom_recipes() -> list[dict[str, Any]]:
    return list(packaging_seed().get("bom_recipes", []))


def save_packaging_supplier(
    record: dict[str, Any],
    updated_by: str = "QCC Control Tower",
) -> str:
    """Create or update a secured supplier master record."""
    _initialize_packaging_database()
    supplier_name = _upper(record.get("supplier"))
    if not supplier_name:
        raise ValueError("Supplier name is required.")
    supplier_id = str(record.get("supplier_id", "") or "").strip()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        if not supplier_id:
            existing = connection.execute(
                "SELECT supplier_id FROM qcc_packaging_suppliers WHERE supplier_name = %s",
                (supplier_name,),
            ).fetchone()
            supplier_id = str(existing[0]) if existing else str(uuid.uuid4())
        connection.execute("""
            INSERT INTO qcc_packaging_suppliers (
                supplier_id, supplier_name, supplies, payment_terms,
                contact_name, contact_email, contact_phone,
                address_line_1, address_line_2, city, state, postal_code,
                country, website, is_active, source, created_by, updated_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, 'Control Tower', %s, %s
            ) ON CONFLICT(supplier_id) DO UPDATE SET
                supplier_name=EXCLUDED.supplier_name,
                supplies=EXCLUDED.supplies,
                payment_terms=EXCLUDED.payment_terms,
                contact_name=EXCLUDED.contact_name,
                contact_email=EXCLUDED.contact_email,
                contact_phone=EXCLUDED.contact_phone,
                address_line_1=EXCLUDED.address_line_1,
                address_line_2=EXCLUDED.address_line_2,
                city=EXCLUDED.city,
                state=EXCLUDED.state,
                postal_code=EXCLUDED.postal_code,
                country=EXCLUDED.country,
                website=EXCLUDED.website,
                is_active=EXCLUDED.is_active,
                updated_by=EXCLUDED.updated_by,
                updated_at=NOW()
        """, (
            supplier_id, supplier_name, _upper(record.get("supplies")),
            _upper(record.get("payment_terms")), _upper(record.get("contact_name")),
            str(record.get("contact_email", "") or "").strip().lower(),
            str(record.get("contact_phone", "") or "").strip(),
            _upper(record.get("address_line_1")), _upper(record.get("address_line_2")),
            _upper(record.get("city")), _upper(record.get("state")),
            _upper(record.get("postal_code")), _upper(record.get("country"), "US"),
            str(record.get("website", "") or "").strip(),
            _upper(record.get("status"), "ACTIVE") != "INACTIVE",
            updated_by, updated_by,
        ))
    return supplier_id


def packaging_suppliers() -> list[dict[str, Any]]:
    """Return workbook suppliers merged with editable secured overrides."""
    by_name = {
        row["supplier"]: row
        for row in (_supplier_defaults(value) for value in packaging_seed().get("suppliers", []))
        if row["supplier"]
    }
    if not database_url():
        return sorted(by_name.values(), key=lambda row: row["supplier"])
    _initialize_packaging_database()
    rows = safe_query_frame("""
        SELECT supplier_id, supplier_name AS supplier, supplies, payment_terms,
               contact_name, contact_email, contact_phone, address_line_1,
               address_line_2, city, state, postal_code, country, website,
               CASE WHEN is_active THEN 'ACTIVE' ELSE 'INACTIVE' END AS status
        FROM qcc_packaging_suppliers
        ORDER BY supplier_name
    """)
    for db_row in rows.to_dict("records"):
        normalized = _supplier_defaults(db_row)
        by_name[normalized["supplier"]] = {
            **by_name.get(normalized["supplier"], {}),
            **normalized,
        }
    return sorted(by_name.values(), key=lambda row: row["supplier"])


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
