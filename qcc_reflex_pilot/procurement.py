"""Materials, supply counts, purchasing, receiving, and warehouse labels.

The QCC deployment supplies the tenant and facility context.  A SaaS repository
adapter can replace these defaults without trusting tenant identifiers submitted
by a browser.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - local preview without database extras
    psycopg = None
    dict_row = None

from .data import database_url
from .packaging_inventory import packaging_items, packaging_suppliers


SUPPLY_SEED_PATH = Path(__file__).with_name("supply_inventory_seed.json")
DEFAULT_TENANT_ID = "QCC"
DEFAULT_FACILITY_ID = "BUILDING-33"
SUPPLY_CATEGORIES = [
    "PPE", "SANITATION", "OFFICE", "SHIPPING", "MAINTENANCE",
    "PRODUCTION CONSUMABLES", "LABORATORY/QA", "OTHER",
]
COUNT_DOMAINS = ["PACKAGING", "SUPPLY"]
PO_STATUSES = ["DRAFT", "SENT", "PARTIALLY RECEIVED", "RECEIVED", "CLOSED", "CANCELLED"]
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def _upper(value: Any, default: str = "") -> str:
    return str(value or default).strip().upper()


def whole_number(value: Any, label: str = "Quantity") -> int:
    """Accept zero and whole nonnegative numbers; reject scanner text/decimals."""
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"{label} must contain digits only. Scan or enter a whole number, including zero.")
    return int(text)


def nonnegative_number(value: Any, label: str = "Value") -> float:
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a nonnegative number.") from error
    if result < 0 or result != result or result in {float("inf"), float("-inf")}:
        raise ValueError(f"{label} must be a nonnegative number.")
    return result


@lru_cache(maxsize=1)
def supply_seed() -> dict[str, Any]:
    return json.loads(SUPPLY_SEED_PATH.read_text(encoding="utf-8"))


def supply_seed_items() -> list[dict[str, Any]]:
    return [dict(row) for row in supply_seed().get("items", [])]


def reorder_quantity(on_hand: int, safety_stock: int, order_multiple: int) -> int:
    """Preserve Henry's workbook formula using whole order units."""
    return max(safety_stock - on_hand, 0) * max(order_multiple, 1)


def _require_database() -> None:
    if not database_url() or psycopg is None:
        raise RuntimeError("Supabase is required for Materials & Procurement transactions.")


def initialize() -> None:
    """Create tenant-ready operational tables and seed the 88 supply items."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    _require_database()
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with psycopg.connect(database_url(), connect_timeout=15) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qcc_supply_items (
                    tenant_id TEXT NOT NULL, facility_id TEXT NOT NULL,
                    item_id TEXT NOT NULL, description TEXT NOT NULL,
                    category TEXT NOT NULL, purchasing_channel TEXT NOT NULL DEFAULT '',
                    supplier TEXT NOT NULL DEFAULT '', pack_description TEXT NOT NULL DEFAULT '',
                    uom TEXT NOT NULL DEFAULT 'EACH', order_multiple INTEGER NOT NULL DEFAULT 1,
                    safety_stock INTEGER NOT NULL DEFAULT 0, opening_on_hand INTEGER NOT NULL DEFAULT 0,
                    comments TEXT NOT NULL DEFAULT '', is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    source TEXT NOT NULL DEFAULT 'CONTROL TOWER', created_by TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (tenant_id, facility_id, item_id),
                    CHECK (order_multiple > 0), CHECK (safety_stock >= 0), CHECK (opening_on_hand >= 0)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qcc_supply_inventory_transactions (
                    transaction_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    facility_id TEXT NOT NULL, item_id TEXT NOT NULL,
                    transaction_type TEXT NOT NULL, quantity_delta INTEGER NOT NULL,
                    reference TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                    occurred_on DATE NOT NULL DEFAULT CURRENT_DATE, created_by TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qcc_inventory_count_sessions (
                    session_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    facility_id TEXT NOT NULL, inventory_domain TEXT NOT NULL,
                    cadence_days INTEGER NOT NULL DEFAULT 14, status TEXT NOT NULL DEFAULT 'OPEN',
                    started_by TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_by TEXT NOT NULL DEFAULT '', completed_at TIMESTAMPTZ,
                    uncounted_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
                    notes TEXT NOT NULL DEFAULT '', CHECK (inventory_domain IN ('PACKAGING','SUPPLY'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qcc_inventory_count_lines (
                    line_id TEXT PRIMARY KEY, session_id TEXT NOT NULL
                        REFERENCES qcc_inventory_count_sessions(session_id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL, item_description TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '', lot_number TEXT NOT NULL DEFAULT '',
                    expiration_date DATE,
                    expected_quantity INTEGER NOT NULL DEFAULT 0, counted_quantity INTEGER,
                    counted_by TEXT NOT NULL DEFAULT '', counted_at TIMESTAMPTZ,
                    notes TEXT NOT NULL DEFAULT '',
                    UNIQUE(session_id, item_id, location, lot_number)
                )
            """)
            conn.execute(
                "ALTER TABLE qcc_inventory_count_lines ADD COLUMN IF NOT EXISTS expiration_date DATE"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qcc_purchase_orders (
                    po_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, facility_id TEXT NOT NULL,
                    po_number TEXT NOT NULL, supplier TEXT NOT NULL, purchasing_channel TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'DRAFT', order_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    required_date DATE, contact_name TEXT NOT NULL DEFAULT '', contact_email TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_by TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(tenant_id, po_number)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qcc_purchase_order_lines (
                    line_id TEXT PRIMARY KEY, po_id TEXT NOT NULL
                        REFERENCES qcc_purchase_orders(po_id) ON DELETE CASCADE,
                    inventory_domain TEXT NOT NULL, item_id TEXT NOT NULL,
                    description TEXT NOT NULL, ordered_quantity INTEGER NOT NULL,
                    received_quantity INTEGER NOT NULL DEFAULT 0, uom TEXT NOT NULL,
                    unit_cost NUMERIC(14,4) NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
                    CHECK (inventory_domain IN ('PACKAGING','SUPPLY')),
                    CHECK (ordered_quantity > 0), CHECK (received_quantity >= 0)
                )
            """)
            for column in ("standard_shipping", "expedited_shipping", "sales_tax"):
                conn.execute(
                    f"ALTER TABLE qcc_purchase_orders ADD COLUMN IF NOT EXISTS {column} "
                    "NUMERIC(14,2) NOT NULL DEFAULT 0"
                )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qcc_purchase_receipts (
                    receipt_id TEXT PRIMARY KEY, po_id TEXT NOT NULL,
                    line_id TEXT NOT NULL REFERENCES qcc_purchase_order_lines(line_id),
                    quantity INTEGER NOT NULL, location TEXT NOT NULL DEFAULT '',
                    lot_number TEXT NOT NULL DEFAULT '', received_by TEXT NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), notes TEXT NOT NULL DEFAULT '',
                    CHECK (quantity > 0)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qcc_count_sessions ON qcc_inventory_count_sessions(tenant_id, facility_id, started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qcc_po ON qcc_purchase_orders(tenant_id, facility_id, created_at DESC)")
            for row in supply_seed_items():
                conn.execute("""
                    INSERT INTO qcc_supply_items (
                        tenant_id, facility_id, item_id, description, category,
                        purchasing_channel, supplier, pack_description, uom,
                        order_multiple, safety_stock, opening_on_hand, comments,
                        source, created_by, updated_by
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'WORKBOOK SEED','WORKBOOK SEED')
                    ON CONFLICT (tenant_id, facility_id, item_id) DO NOTHING
                """, (
                    DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, row["item_id"],
                    row["description"], row["category"], row["purchasing_channel"],
                    row["supplier"], row["pack_description"], row["uom"],
                    row["order_multiple"], row["safety_stock"], row["on_hand"],
                    row["comments"], "PRODUCTION COMPONENT INVENTORY TEMPLATE",
                ))
            for table in (
                "qcc_supply_items", "qcc_supply_inventory_transactions",
                "qcc_inventory_count_sessions", "qcc_inventory_count_lines",
                "qcc_purchase_orders", "qcc_purchase_order_lines", "qcc_purchase_receipts",
            ):
                conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        _SCHEMA_READY = True


def supply_items() -> list[dict[str, Any]]:
    if not database_url():
        return [
            dict(row, reorder_quantity=reorder_quantity(
                int(row["on_hand"]), int(row["safety_stock"]), int(row["order_multiple"])
            ))
            for row in supply_seed_items()
        ]
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT item.*,
                item.opening_on_hand + COALESCE((SELECT SUM(tx.quantity_delta)
                    FROM qcc_supply_inventory_transactions tx
                    WHERE tx.tenant_id=item.tenant_id AND tx.facility_id=item.facility_id
                      AND tx.item_id=item.item_id),0) AS on_hand
            FROM qcc_supply_items item
            WHERE tenant_id=%s AND facility_id=%s
            ORDER BY item_id
        """, (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID)).fetchall()
    result = []
    for row in rows:
        record = dict(row)
        record["on_hand"] = int(record.get("on_hand", 0) or 0)
        record["reorder_quantity"] = reorder_quantity(
            record["on_hand"], int(record.get("safety_stock", 0) or 0),
            int(record.get("order_multiple", 1) or 1),
        )
        record["status"] = "ACTIVE" if record.get("is_active", True) else "INACTIVE"
        result.append(record)
    return result


def adjust_supply_item(item_id: str, quantity_delta: Any, actor: str, notes: str) -> str:
    """Post a traceable between-count supply adjustment without scanner workflow."""
    initialize()
    item_id = _upper(item_id)
    note = str(notes or "").strip()
    try:
        delta = int(str(quantity_delta).strip())
    except (TypeError, ValueError) as error:
        raise ValueError("Adjustment must be a whole number, such as 12 or -3.") from error
    if delta == 0:
        raise ValueError("Adjustment cannot be zero.")
    if not note:
        raise ValueError("Enter a reason for the supply adjustment.")
    transaction_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        item = conn.execute(
            "SELECT opening_on_hand FROM qcc_supply_items WHERE tenant_id=%s AND facility_id=%s AND item_id=%s",
            (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, item_id),
        ).fetchone()
        if not item:
            raise ValueError("Select a registered supply item.")
        current_delta = conn.execute(
            "SELECT COALESCE(SUM(quantity_delta),0) FROM qcc_supply_inventory_transactions "
            "WHERE tenant_id=%s AND facility_id=%s AND item_id=%s",
            (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, item_id),
        ).fetchone()[0]
        if int(item[0]) + int(current_delta or 0) + delta < 0:
            raise ValueError("This adjustment would make supply inventory negative.")
        conn.execute("""
            INSERT INTO qcc_supply_inventory_transactions (
                transaction_id,tenant_id,facility_id,item_id,transaction_type,
                quantity_delta,reference,notes,occurred_on,created_by
            ) VALUES (%s,%s,%s,%s,'MANUAL ADJUSTMENT',%s,'',%s,CURRENT_DATE,%s)
        """, (transaction_id, DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID,
              item_id, delta, note, actor))
    return transaction_id


def set_supply_on_hand(item_id: str, on_hand: Any, actor: str) -> str:
    """Set the visible supply balance and record the calculated difference."""
    initialize()
    item_id = _upper(item_id)
    target = whole_number(on_hand, "On Hand")
    transaction_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        item = conn.execute(
            "SELECT opening_on_hand FROM qcc_supply_items "
            "WHERE tenant_id=%s AND facility_id=%s AND item_id=%s",
            (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, item_id),
        ).fetchone()
        if not item:
            raise ValueError("Select a registered supply item.")
        current_delta = conn.execute(
            "SELECT COALESCE(SUM(quantity_delta),0) "
            "FROM qcc_supply_inventory_transactions "
            "WHERE tenant_id=%s AND facility_id=%s AND item_id=%s",
            (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, item_id),
        ).fetchone()[0]
        delta = target - (int(item[0]) + int(current_delta or 0))
        if delta == 0:
            return ""
        conn.execute("""
            INSERT INTO qcc_supply_inventory_transactions (
                transaction_id,tenant_id,facility_id,item_id,transaction_type,
                quantity_delta,reference,notes,occurred_on,created_by
            ) VALUES (%s,%s,%s,%s,'DIRECT ON HAND EDIT',%s,'',
                      'On Hand edited in Supply Inventory',CURRENT_DATE,%s)
        """, (
            transaction_id, DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID,
            item_id, delta, actor,
        ))
    return transaction_id


def _packaging_count_rows() -> list[dict[str, Any]]:
    from .warehouse import warehouse_snapshot
    items = [row for row in packaging_items() if row.get("status") == "ACTIVE"]
    snapshot = warehouse_snapshot()
    balances_by_item: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("balances", []):
        balances_by_item.setdefault(str(row["material_id"]), []).append(row)
    result = []
    for item in items:
        balances = balances_by_item.get(str(item["material_id"])) or [{
            "location": item.get("default_location", "UNASSIGNED"), "lot": "", "quantity": 0,
        }]
        for balance in balances:
            result.append({
                "item_id": item["material_id"], "description": item["item"],
                "location": _upper(balance.get("location"), "UNASSIGNED"),
                "lot_number": str(balance.get("lot", "") or ""),
                "expiration_date": str(balance.get("expiration", "") or ""),
                "expected_quantity": int(round(float(balance.get("quantity", 0) or 0))),
            })
    return result


def start_count_session(domain: str, actor: str, notes: str = "", scope: str = "") -> str:
    domain = _upper(domain)
    if domain not in COUNT_DOMAINS:
        raise ValueError("Count type must be Packaging or Supply.")
    initialize()
    session_id = str(uuid.uuid4())
    rows = _packaging_count_rows() if domain == "PACKAGING" else [
        {
            "item_id": row["item_id"], "description": row["description"],
            "location": "FACILITY-WIDE", "lot_number": "",
            "expiration_date": "",
            "expected_quantity": int(row["on_hand"]),
        }
        for row in supply_items() if row.get("status") == "ACTIVE"
    ]
    scope_text = str(scope or "").strip().casefold()
    if scope_text:
        rows = [row for row in rows if scope_text in (
            f"{row.get('item_id', '')} {row.get('description', '')}"
        ).casefold()]
    if not rows:
        raise ValueError("The selected count scope contains no active items.")
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        conn.execute("""
            INSERT INTO qcc_inventory_count_sessions (
                session_id,tenant_id,facility_id,inventory_domain,cadence_days,
                started_by,notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (session_id, DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, domain,
              14 if domain == "SUPPLY" else 0, actor, notes))
        for row in rows:
            conn.execute("""
                INSERT INTO qcc_inventory_count_lines (
                    line_id,session_id,item_id,item_description,location,lot_number,
                    expiration_date,expected_quantity
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (str(uuid.uuid4()), session_id, row["item_id"], row["description"],
                  row["location"], row["lot_number"], row.get("expiration_date") or None,
                  row["expected_quantity"]))
    return session_id


def record_count(
    session_id: str, item_id: str, quantity: Any, actor: str,
    location: str = "", lot_number: str = "", expiration_date: str = "", notes: str = "",
) -> str:
    counted = whole_number(quantity, "Counted quantity")
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        session = conn.execute(
            "SELECT inventory_domain,status FROM qcc_inventory_count_sessions WHERE session_id=%s",
            (session_id,),
        ).fetchone()
        if not session or session[1] != "OPEN":
            raise ValueError("Select an open count session.")
        query = """SELECT line_id FROM qcc_inventory_count_lines
            WHERE session_id=%s AND item_id=%s"""
        params: list[Any] = [session_id, _upper(item_id)]
        if session[0] == "PACKAGING":
            expiration = str(expiration_date or "").strip()
            if expiration:
                date.fromisoformat(expiration)
            query += " AND location=%s AND lot_number=%s AND COALESCE(expiration_date::text,'')=%s"
            params.extend([_upper(location, "UNASSIGNED"), str(lot_number or "").strip(), expiration])
        rows = conn.execute(query, tuple(params)).fetchall()
        if len(rows) != 1:
            raise ValueError("The item/location/lot did not identify exactly one expected count line.")
        line_id = str(rows[0][0])
        conn.execute("""
            UPDATE qcc_inventory_count_lines SET counted_quantity=%s,counted_by=%s,
                counted_at=NOW(),notes=%s WHERE line_id=%s
        """, (counted, actor, notes, line_id))
    return line_id


def count_sessions(limit: int = 100) -> list[dict[str, Any]]:
    if not database_url():
        return []
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT session.*,
                COUNT(line.line_id) AS expected_items,
                COUNT(line.counted_quantity) AS counted_items,
                COUNT(line.line_id)-COUNT(line.counted_quantity) AS uncounted_items
            FROM qcc_inventory_count_sessions session
            LEFT JOIN qcc_inventory_count_lines line ON line.session_id=session.session_id
            WHERE session.tenant_id=%s AND session.facility_id=%s
            GROUP BY session.session_id ORDER BY session.started_at DESC LIMIT %s
        """, (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, limit)).fetchall()
    return [dict(row) for row in rows]


def count_lines(session_id: str) -> list[dict[str, Any]]:
    if not session_id or not database_url():
        return []
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT * FROM qcc_inventory_count_lines WHERE session_id=%s
            ORDER BY item_id,location,lot_number
        """, (session_id,)).fetchall()
    return [dict(row) for row in rows]


def complete_count_session(session_id: str, actor: str, acknowledge_uncounted: bool) -> dict[str, int]:
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        session = conn.execute(
            "SELECT * FROM qcc_inventory_count_sessions WHERE session_id=%s FOR UPDATE",
            (session_id,),
        ).fetchone()
        if not session or session["status"] != "OPEN":
            raise ValueError("Select an open count session.")
        lines = conn.execute(
            "SELECT * FROM qcc_inventory_count_lines WHERE session_id=%s ORDER BY item_id",
            (session_id,),
        ).fetchall()
        uncounted = [row for row in lines if row["counted_quantity"] is None]
        if uncounted and not acknowledge_uncounted:
            raise ValueError(
                f"{len(uncounted)} expected items were not counted. Review them and acknowledge before completing."
            )
        if session["inventory_domain"] == "SUPPLY":
            for row in lines:
                if row["counted_quantity"] is None:
                    continue
                delta = int(row["counted_quantity"]) - int(row["expected_quantity"])
                if delta:
                    conn.execute("""
                        INSERT INTO qcc_supply_inventory_transactions (
                            transaction_id,tenant_id,facility_id,item_id,transaction_type,
                            quantity_delta,reference,notes,occurred_on,created_by
                        ) VALUES (%s,%s,%s,%s,'PHYSICAL COUNT',%s,%s,%s,CURRENT_DATE,%s)
                    """, (str(uuid.uuid4()), DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID,
                          row["item_id"], delta, session_id, row["notes"], actor))
    if session["inventory_domain"] == "PACKAGING":
        from .warehouse import post_activity
        for row in lines:
            if row["counted_quantity"] is None:
                continue
            post_activity({
                "material_id": row["item_id"], "action": "Physical Count",
                "quantity": str(row["counted_quantity"]), "expected": str(row["expected_quantity"]),
                "location": row["location"], "destination": "", "lot": row["lot_number"],
                "expiration": str(row.get("expiration_date") or ""),
                "date": date.today().isoformat(), "unit_cost": "0",
                "reference": session_id, "reason": row["notes"] or "Formal physical count session",
            }, actor, str(uuid.uuid5(uuid.NAMESPACE_URL, f"qcc-count:{session_id}:{row['line_id']}")))
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        conn.execute("""
            UPDATE qcc_inventory_count_sessions SET status='COMPLETED',completed_by=%s,
                completed_at=NOW(),uncounted_acknowledged=%s
            WHERE session_id=%s AND status='OPEN'
        """, (actor, bool(uncounted), session_id))
    return {"counted": len(lines) - len(uncounted), "uncounted": len(uncounted)}


def _next_po_number(conn: Any, year: int) -> str:
    conn.execute("SELECT pg_advisory_xact_lock(73119022)")
    rows = conn.execute(
        "SELECT po_number FROM qcc_purchase_orders WHERE tenant_id=%s AND po_number LIKE %s",
        (DEFAULT_TENANT_ID, f"QCC-PO-{year}-%"),
    ).fetchall()
    numbers = []
    for row in rows:
        match = re.fullmatch(rf"QCC-PO-{year}-(\d+)", str(row[0]))
        if match:
            numbers.append(int(match.group(1)))
    return f"QCC-PO-{year}-{max(numbers, default=0) + 1:04d}"


def create_purchase_order(record: dict[str, Any], actor: str) -> str:
    initialize()
    supplier = _upper(record.get("supplier"))
    if not supplier:
        raise ValueError("Supplier is required.")
    required = str(record.get("required_date", "") or "").strip()
    if required:
        date.fromisoformat(required)
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        po_number = _next_po_number(conn, date.today().year)
        conn.execute("""
            INSERT INTO qcc_purchase_orders (
                po_id,tenant_id,facility_id,po_number,supplier,purchasing_channel,
                required_date,contact_name,contact_email,notes,created_by,updated_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (str(uuid.uuid4()), DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, po_number,
              supplier, _upper(record.get("purchasing_channel")), required or None,
              _upper(record.get("contact_name")), str(record.get("contact_email", "") or "").strip().lower(),
              str(record.get("notes", "") or "").strip(), actor, actor))
    return po_number


def add_purchase_order_line(po_number: str, record: dict[str, Any], actor: str) -> str:
    initialize()
    domain = _upper(record.get("inventory_domain"))
    if domain not in COUNT_DOMAINS:
        raise ValueError("Purchase-order item type must be Packaging or Supply.")
    quantity = whole_number(record.get("quantity"), "Ordered quantity")
    if quantity <= 0:
        raise ValueError("Ordered quantity must be greater than zero.")
    item_id = _upper(record.get("item_id"))
    if not item_id:
        raise ValueError("Item ID is required.")
    if domain == "PACKAGING":
        master = next((row for row in packaging_items() if _upper(row.get("material_id")) == item_id), None)
        description_key = "item"
    else:
        master = next((row for row in supply_items() if _upper(row.get("item_id")) == item_id), None)
        description_key = "description"
    if not master:
        raise ValueError(f"{item_id} is not an active {domain.title()} inventory-master item.")
    if str(master.get("status", "ACTIVE")).upper() != "ACTIVE":
        raise ValueError(f"Reactivate {item_id} before adding it to a purchase order.")
    description = _upper(master.get(description_key) or record.get("description"))
    uom = _upper(master.get("uom") or record.get("uom"), "EACH")
    line_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        po = conn.execute(
            "SELECT po_id,status FROM qcc_purchase_orders WHERE tenant_id=%s AND po_number=%s FOR UPDATE",
            (DEFAULT_TENANT_ID, po_number),
        ).fetchone()
        if not po or po[1] not in {"DRAFT", "SENT"}:
            raise ValueError("Lines can only be added to a Draft or Sent purchase order.")
        conn.execute("""
            INSERT INTO qcc_purchase_order_lines (
                line_id,po_id,inventory_domain,item_id,description,ordered_quantity,uom,unit_cost,notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (line_id, po[0], domain, item_id, description, quantity,
              uom,
              nonnegative_number(record.get("unit_cost", 0), "Unit cost"),
              str(record.get("notes", "") or "").strip()))
        conn.execute("UPDATE qcc_purchase_orders SET updated_by=%s,updated_at=NOW() WHERE po_id=%s", (actor, po[0]))
    return line_id


def update_purchase_order_charges(
    po_number: str,
    standard_shipping: Any,
    expedited_shipping: Any,
    sales_tax: Any,
    actor: str,
) -> None:
    """Save PO-level freight and tax amounts as nonnegative dollar values."""
    values = (
        nonnegative_number(standard_shipping, "Standard shipping"),
        nonnegative_number(expedited_shipping, "Expedited shipping"),
        nonnegative_number(sales_tax, "Sales tax"),
    )
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        row = conn.execute(
            "SELECT status FROM qcc_purchase_orders "
            "WHERE tenant_id=%s AND facility_id=%s AND po_number=%s FOR UPDATE",
            (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, po_number),
        ).fetchone()
        if not row:
            raise ValueError("Select a purchase order.")
        if row[0] in {"CLOSED", "CANCELLED"}:
            raise ValueError("Charges cannot be changed on a closed purchase order.")
        conn.execute("""
            UPDATE qcc_purchase_orders
            SET standard_shipping=%s, expedited_shipping=%s, sales_tax=%s,
                updated_by=%s, updated_at=NOW()
            WHERE tenant_id=%s AND facility_id=%s AND po_number=%s
        """, (*values, actor, DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, po_number))


def purchase_orders(limit: int = 100) -> list[dict[str, Any]]:
    if not database_url():
        return []
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT po.*, COUNT(line.line_id) AS line_count,
                COALESCE(SUM(line.ordered_quantity*line.unit_cost),0)
                    + po.standard_shipping + po.expedited_shipping + po.sales_tax AS total,
                COALESCE(SUM(line.ordered_quantity),0) AS ordered_quantity,
                COALESCE(SUM(line.received_quantity),0) AS received_quantity
            FROM qcc_purchase_orders po LEFT JOIN qcc_purchase_order_lines line ON line.po_id=po.po_id
            WHERE po.tenant_id=%s AND po.facility_id=%s
            GROUP BY po.po_id ORDER BY po.created_at DESC LIMIT %s
        """, (DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID, limit)).fetchall()
    return [dict(row) for row in rows]


def purchase_order_detail(po_number: str) -> dict[str, Any]:
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        header = conn.execute(
            "SELECT * FROM qcc_purchase_orders WHERE tenant_id=%s AND po_number=%s",
            (DEFAULT_TENANT_ID, po_number),
        ).fetchone()
        if not header:
            raise ValueError("Purchase order was not found.")
        lines = conn.execute(
            "SELECT * FROM qcc_purchase_order_lines WHERE po_id=%s ORDER BY line_id", (header["po_id"],)
        ).fetchall()
    return {"header": dict(header), "lines": [dict(row) for row in lines]}


def receive_purchase_order_line(
    po_number: str, line_id: str, quantity: Any, actor: str,
    location: str = "UNASSIGNED", lot_number: str = "", notes: str = "",
) -> str:
    received = whole_number(quantity, "Received quantity")
    if received <= 0:
        raise ValueError("Received quantity must be greater than zero.")
    initialize()
    receipt_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        po = conn.execute(
            "SELECT * FROM qcc_purchase_orders WHERE tenant_id=%s AND po_number=%s FOR UPDATE",
            (DEFAULT_TENANT_ID, po_number),
        ).fetchone()
        line = conn.execute(
            "SELECT * FROM qcc_purchase_order_lines WHERE line_id=%s AND po_id=%s FOR UPDATE",
            (line_id, po["po_id"] if po else ""),
        ).fetchone()
        if not po or not line:
            raise ValueError("Purchase-order line was not found.")
        remaining = int(line["ordered_quantity"]) - int(line["received_quantity"])
        if received > remaining:
            raise ValueError(f"Only {remaining} remains open on this line.")
        conn.execute("""
            INSERT INTO qcc_purchase_receipts (
                receipt_id,po_id,line_id,quantity,location,lot_number,received_by,notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (receipt_id, po["po_id"], line_id, received,
              _upper(location, "UNASSIGNED"), str(lot_number or "").strip(), actor, notes))
        conn.execute(
            "UPDATE qcc_purchase_order_lines SET received_quantity=received_quantity+%s WHERE line_id=%s",
            (received, line_id),
        )
        # Recalculate against the now-current rows; this also handles single-line POs.
        current = conn.execute(
            "SELECT SUM(ordered_quantity),SUM(received_quantity) FROM qcc_purchase_order_lines WHERE po_id=%s",
            (po["po_id"],),
        ).fetchone()
        status = "RECEIVED" if int(current[1] or 0) >= int(current[0] or 0) else "PARTIALLY RECEIVED"
        conn.execute(
            "UPDATE qcc_purchase_orders SET status=%s,updated_by=%s,updated_at=NOW() WHERE po_id=%s",
            (status, actor, po["po_id"]),
        )
        if line["inventory_domain"] == "SUPPLY":
            conn.execute("""
                INSERT INTO qcc_supply_inventory_transactions (
                    transaction_id,tenant_id,facility_id,item_id,transaction_type,
                    quantity_delta,reference,notes,occurred_on,created_by
                ) VALUES (%s,%s,%s,%s,'PO RECEIPT',%s,%s,%s,CURRENT_DATE,%s)
            """, (str(uuid.uuid4()), DEFAULT_TENANT_ID, DEFAULT_FACILITY_ID,
                  line["item_id"], received, po_number, notes, actor))
    if line["inventory_domain"] == "PACKAGING":
        from .warehouse import post_activity
        post_activity({
            "material_id": line["item_id"], "action": "Receive", "quantity": str(received),
            "location": _upper(location, "UNASSIGNED"), "destination": "", "lot": lot_number,
            "expiration": "", "date": date.today().isoformat(), "unit_cost": str(line["unit_cost"]),
            "reference": po_number, "reason": notes or "Purchase-order receipt",
        }, actor, receipt_id)
    return receipt_id


def close_purchase_order(po_number: str, actor: str) -> None:
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        row = conn.execute(
            "SELECT po_id,status FROM qcc_purchase_orders WHERE tenant_id=%s AND po_number=%s FOR UPDATE",
            (DEFAULT_TENANT_ID, po_number),
        ).fetchone()
        if not row or row[1] in {"CLOSED", "CANCELLED"}:
            raise ValueError("Select an open purchase order.")
        conn.execute(
            "UPDATE qcc_purchase_orders SET status='CLOSED',updated_by=%s,updated_at=NOW() WHERE po_id=%s",
            (actor, row[0]),
        )


def purchase_order_pdf(po_number: str) -> bytes:
    """Create the supplier-facing PO PDF using the agreed QCC legal details."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("PDF support is not installed.") from error
    detail = purchase_order_detail(po_number)
    header, lines = detail["header"], detail["lines"]
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=.55*inch, leftMargin=.55*inch,
                            topMargin=.45*inch, bottomMargin=.45*inch)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("THE QCC GROUP", styles["Title"]),
        Paragraph("1355 West Front Street, Building 33<br/>Plainfield, NJ 07063<br/>"
                  "908-635-9255 &nbsp;&nbsp; purchasing@qccnj.com", styles["Normal"]),
        Spacer(1, 12),
        Table([
            ["PURCHASE ORDER", po_number], ["Supplier", header["supplier"]],
            ["Order Date", str(header["order_date"])],
            ["Required Date", str(header.get("required_date") or "")],
            ["Purchasing Contact", "Henry Barnett"],
        ], colWidths=[1.7*inch, 5.0*inch]),
        Spacer(1, 12),
    ]
    data = [["Item ID", "Description", "Qty", "UOM", "Unit Cost", "Line Total"]]
    for line in lines:
        total = float(line["ordered_quantity"]) * float(line["unit_cost"])
        data.append([
            line["item_id"], Paragraph(str(line["description"]), styles["BodyText"]),
            f"{int(line['ordered_quantity']):,}", line["uom"],
            f"${float(line['unit_cost']):,.4f}", f"${total:,.2f}",
        ])
    subtotal = sum(float(x["ordered_quantity"]) * float(x["unit_cost"]) for x in lines)
    standard_shipping = float(header.get("standard_shipping", 0) or 0)
    expedited_shipping = float(header.get("expedited_shipping", 0) or 0)
    sales_tax = float(header.get("sales_tax", 0) or 0)
    data.extend([
        ["", "", "", "", "SUBTOTAL", f"${subtotal:,.2f}"],
        ["", "", "", "", "STANDARD SHIPPING", f"${standard_shipping:,.2f}"],
        ["", "", "", "", "EXPEDITED SHIPPING", f"${expedited_shipping:,.2f}"],
        ["", "", "", "", "SALES TAX", f"${sales_tax:,.2f}"],
        ["", "", "", "", "TOTAL", f"${subtotal + standard_shipping + expedited_shipping + sales_tax:,.2f}"],
    ])
    table = Table(data, colWidths=[.9*inch, 3.0*inch, .55*inch, .55*inch, .8*inch, .9*inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#111827")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("ALIGN", (2,1), (-1,-1), "RIGHT"),
        ("FONTNAME", (-2,-5), (-1,-1), "Helvetica-Bold"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#E6F7F6")),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
    ]))
    story.extend([table, Spacer(1, 12), Paragraph(str(header.get("notes", "") or ""), styles["Normal"])])
    doc.build(story)
    return buffer.getvalue()


def send_purchase_order(po_number: str) -> None:
    """Send a PO with Microsoft Graph when IT has supplied scoped credentials."""
    tenant = os.getenv("QCC_MICROSOFT_TENANT_ID", "").strip()
    client = os.getenv("QCC_MICROSOFT_CLIENT_ID", "").strip()
    secret = os.getenv("QCC_MICROSOFT_CLIENT_SECRET", "").strip()
    sender = os.getenv("QCC_PURCHASING_FROM_EMAIL", "purchasing@qccnj.com").strip()
    detail = purchase_order_detail(po_number)
    recipient = str(detail["header"].get("contact_email", "") or "").strip()
    if not all((tenant, client, secret)):
        raise RuntimeError("Microsoft purchasing email is not configured in Render.")
    if not recipient:
        raise ValueError("Add the supplier contact email before sending this purchase order.")
    token_body = urllib.parse.urlencode({
        "client_id": client, "client_secret": secret,
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
    }).encode()
    token_request = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data=token_body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=20) as response:
            token = json.loads(response.read())["access_token"]
        payload = {
            "message": {
                "subject": f"The QCC Group Purchase Order {po_number}",
                "body": {"contentType": "Text", "content": "Please find the attached purchase order.\n\nHenry Barnett\nThe QCC Group"},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
                "attachments": [{
                    "@odata.type": "#microsoft.graph.fileAttachment", "name": f"{po_number}.pdf",
                    "contentType": "application/pdf",
                    "contentBytes": base64.b64encode(purchase_order_pdf(po_number)).decode(),
                }],
            }, "saveToSentItems": True,
        }
        request = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(sender)}/sendMail",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as error:
        detail_text = error.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Microsoft Graph rejected the email ({error.code}): {detail_text}") from error
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        conn.execute(
            "UPDATE qcc_purchase_orders SET status='SENT',updated_at=NOW() WHERE tenant_id=%s AND po_number=%s",
            (DEFAULT_TENANT_ID, po_number),
        )


def _zpl_text(value: Any, limit: int = 90) -> str:
    return re.sub(r"[^A-Z0-9 .,_/#()\-]", "", _upper(value))[:limit]


def box_label_zpl(
    material_id: str, description: str, lot_or_po: str, quantity: Any,
    print_date: str | None = None,
) -> str:
    """4x6, 203-dpi label with five separately scannable QR values."""
    qty = whole_number(quantity)
    values = [print_date or date.today().isoformat(), material_id, description, lot_or_po or "N/A", str(qty)]
    labels = ["DATE", "MATERIAL ID", "DESCRIPTION", "LOT / PO", "QUANTITY"]
    commands = ["^XA", "^PW812", "^LL1218", "^CI28"]
    y = 45
    for label, value in zip(labels, values):
        safe = _zpl_text(value)
        commands.extend([
            f"^FO40,{y}^A0N,30,28^FD{label}: {safe}^FS",
            f"^FO590,{y-8}^BQN,2,5^FDLA,{safe}^FS",
        ])
        y += 215
    commands.extend(["^FO40,1135^A0N,24,22^FDQCC MATERIAL INVENTORY^FS", "^XZ"])
    return "\n".join(commands)


def quantity_label_zpl(material_id: str, quantity: Any) -> str:
    """2.25x1.25, 203-dpi quantity label with separate ID and quantity QR codes."""
    qty = whole_number(quantity)
    mid = _zpl_text(material_id, 40)
    return "\n".join([
        "^XA", "^PW457", "^LL254", "^CI28",
        f"^FO18,20^A0N,28,25^FD{mid}^FS",
        f"^FO18,58^BQN,2,4^FDLA,{mid}^FS",
        f"^FO236,20^A0N,32,28^FDQTY: {qty}^FS",
        f"^FO260,62^BQN,2,4^FDLA,{qty}^FS",
        "^XZ",
    ])
