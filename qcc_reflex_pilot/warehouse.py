"""Packaging import and warehouse ledger services, independent of the UI.

The existing QCC deployment owns this database. A SaaS adapter must supply an
isolated company database/repository; client-entered company IDs are not trusted.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import uuid
from collections import defaultdict
from datetime import date
from functools import lru_cache
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .data import database_url
from .packaging_inventory import (
    _initialize_packaging_database, _seed_item_defaults, _normalized_category,
    _normalized_brand_scope, PACKAGING_CATEGORIES, PACKAGING_BRAND_SCOPES,
    packaging_items, packaging_seed_items, save_packaging_item,
)

ACTIVITIES = ["Receive", "Issue to Production", "Return from Production",
              "Scrap / Damage", "Transfer Location", "Physical Count", "Reverse Activity"]
TEXT_FIELDS = {
    "Packaging Inventory Item": "item", "Brand / Shared Use": "brand_scope",
    "Category": "category", "Primary Supplier": "vendor",
    "Secondary Supplier": "secondary_vendor", "Ownership": "ownership",
    "UOM": "uom", "Default Location": "default_location", "Status": "status",
    "Size / Format": "size_format",
}
NUMBER_FIELDS = {
    "Primary Lead Time (Calendar Days)": "primary_lead_time_days",
    "Secondary Lead Time (Calendar Days)": "secondary_lead_time_days",
    "Units Per Case": "units_per_case", "Reorder Point": "reorder_point",
    "Safety Stock": "safety_stock", "Unit Cost": "unit_cost",
}


def number(value: Any) -> float:
    result = float(str(value).replace(",", "").strip())
    if not math.isfinite(result) or result < 0:
        raise ValueError("Quantities and numeric settings must be finite and nonnegative.")
    return result


def item_version(row: dict) -> str:
    # Counts and movement dates are intentionally excluded from metadata conflicts.
    fields = sorted(set(TEXT_FIELDS.values()) | set(NUMBER_FIELDS.values()))
    return hashlib.sha256(json.dumps(
        {key: row.get(key) for key in fields}, sort_keys=True, default=str
    ).encode()).hexdigest()


def preview_import(content: bytes, existing: list[dict]) -> dict:
    if len(content) > 5_000_000:
        raise ValueError("CSV must be smaller than 5 MB.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    if len(set(headers)) != len(headers):
        raise ValueError("CSV contains duplicate column headers.")
    if not {"Material ID", "Packaging Inventory Item"}.issubset(headers):
        raise ValueError("Material ID and Packaging Inventory Item columns are required.")
    by_id = {r["material_id"]: r for r in existing}
    result = {"rows": [], "errors": [], "warnings": [], "hash": hashlib.sha256(content).hexdigest()}
    seen = set()
    for line, raw in enumerate(reader, 2):
        if line > 5001:
            raise ValueError("Import is limited to 5,000 items.")
        if not any(raw.values()):
            continue
        mid = str(raw.get("Material ID", "")).strip().upper()
        try:
            if None in raw:
                raise ValueError("Extra CSV values do not match the headers.")
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,79}", mid):
                raise ValueError("A valid Material ID is required.")
            if mid in seen:
                raise ValueError("Duplicate Material ID.")
            seen.add(mid)
            before = by_id.get(mid)
            row = dict(before or _seed_item_defaults({"material_id": mid, "on_hand": 0}))
            token = str(raw.get("Registry Version", "") or "").strip()
            if token and (not before or token != item_version(before)):
                raise ValueError("Item changed since export. Export a fresh copy before updating it.")
            for label, key in TEXT_FIELDS.items():
                value = str(raw.get(label, "") or "").strip().upper()
                # Empty exported cells are not a request to erase stored settings.
                if not value or (key == "default_location" and value == "UNASSIGNED" and before):
                    continue
                if key == "category":
                    value = _normalized_category(value)
                    if value not in PACKAGING_CATEGORIES:
                        raise ValueError(f"Unknown category: {value}")
                if key == "brand_scope":
                    value = _normalized_brand_scope(value)
                    if value not in PACKAGING_BRAND_SCOPES:
                        raise ValueError(f"Unknown brand: {value}")
                if key == "status" and value not in {"ACTIVE", "INACTIVE"}:
                    raise ValueError("Status must be ACTIVE or INACTIVE.")
                if key == "uom" and before and value != before.get("uom"):
                    raise ValueError("UOM changes require a separate conversion review.")
                row[key] = value
            for label, key in NUMBER_FIELDS.items():
                value = str(raw.get(label, "") or "").strip()
                if value:
                    row[key] = number(value)
                    if "lead_time" in key and row[key] != int(row[key]):
                        raise ValueError("Lead time must be whole calendar days.")
            combined = str(raw.get("Lead Time: Primary / Secondary", "") or "").strip()
            if combined:
                match = re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*(?:DAYS)?", combined, re.I)
                if not match:
                    raise ValueError("Lead time must look like 26 / 0 DAYS.")
                row["primary_lead_time_days"] = int(match[1])
                # Blank secondary supplier preserves its existing lead time too.
                if str(raw.get("Secondary Supplier", "") or "").strip():
                    row["secondary_lead_time_days"] = int(match[2])
            if not row.get("item"):
                raise ValueError("Item description is required.")
            if row.get("vendor") and row.get("vendor") == row.get("secondary_vendor"):
                raise ValueError("Primary and secondary suppliers must differ.")
            if row.get("units_per_case", 1) <= 0:
                raise ValueError("Units per case must be greater than zero.")
            if "/" in row.get("vendor", ""):
                result["warnings"].append(f"{mid}: combined supplier name retained; review primary/secondary assignments.")
            if "Status" in raw and not str(raw.get("Status") or "").strip():
                result["warnings"].append(f"{mid}: blank status preserved (ACTIVE for a new item).")
            changes = [
                {"field": label, "before": (before or {}).get(key, ""), "after": row.get(key, "")}
                for label, key in {**TEXT_FIELDS, **NUMBER_FIELDS}.items()
                if (before or {}).get(key) != row.get(key)
            ]
            result["rows"].append({"material_id": mid, "record": row,
                "before": before, "version": item_version(before) if before else "",
                "changes": changes, "action": "NEW" if not before else "UPDATE" if changes else "UNCHANGED"})
        except (ValueError, TypeError) as error:
            result["errors"].append(f"Row {line} ({mid}): {error}")
    if not result["rows"]:
        result["errors"].append("No valid item rows found.")
    if "Registry Version" not in headers:
        result["warnings"].append("Older export: changes made before this preview cannot be detected. Review each changed field.")
    return result


@lru_cache(maxsize=1)
def initialize() -> None:
    _initialize_packaging_database()
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        _lock(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS qcc_packaging_locations (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, warehouse TEXT NOT NULL DEFAULT '',
            zone TEXT NOT NULL DEFAULT '', rack TEXT NOT NULL DEFAULT '', bin TEXT NOT NULL DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT TRUE, updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""INSERT INTO qcc_packaging_locations (code,name,updated_by)
            VALUES ('UNASSIGNED','UNASSIGNED','SYSTEM') ON CONFLICT DO NOTHING""")
        # Existing receipt locations become registry entries without moving stock.
        conn.execute("""INSERT INTO qcc_packaging_locations (code,name,updated_by)
            SELECT DISTINCT UPPER(TRIM(COALESCE(NULLIF(location,''),'UNASSIGNED'))),
                UPPER(TRIM(COALESCE(NULLIF(location,''),'UNASSIGNED'))),'LEGACY RECEIPT'
            FROM qcc_packaging_inventory_transactions ON CONFLICT DO NOTHING""")
        conn.execute("""CREATE TABLE IF NOT EXISTS qcc_packaging_activity (
            activity_id TEXT PRIMARY KEY, material_id TEXT NOT NULL, action TEXT NOT NULL,
            occurred_on DATE NOT NULL, reference TEXT NOT NULL, reason TEXT NOT NULL,
            actor TEXT NOT NULL, reversal_of TEXT UNIQUE REFERENCES qcc_packaging_activity(activity_id),
            details JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""ALTER TABLE qcc_packaging_inventory_transactions
            ADD COLUMN IF NOT EXISTS activity_id TEXT REFERENCES qcc_packaging_activity(activity_id)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS qcc_packaging_registry_imports (
            import_id TEXT PRIMARY KEY, file_hash TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
            content BYTEA NOT NULL, actor TEXT NOT NULL, changes JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        # The trusted server connection owns these tables. Do not expose employee
        # audit trails or uploaded files through public Supabase client roles.
        conn.execute("ALTER TABLE qcc_packaging_locations ENABLE ROW LEVEL SECURITY")
        conn.execute("ALTER TABLE qcc_packaging_activity ENABLE ROW LEVEL SECURITY")
        conn.execute("ALTER TABLE qcc_packaging_registry_imports ENABLE ROW LEVEL SECURITY")


def _lock(conn):
    conn.execute("SELECT pg_advisory_xact_lock(73119021)")


def apply_import(preview: dict, content: bytes, filename: str, actor: str) -> str:
    if preview.get("errors"):
        raise ValueError("Resolve the CSV errors before applying changes.")
    if hashlib.sha256(content).hexdigest() != preview["hash"]:
        raise ValueError("CSV changed. Preview it again.")
    initialize()
    import_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        _lock(conn)
        if conn.execute("SELECT 1 FROM qcc_packaging_registry_imports WHERE file_hash=%s", (preview["hash"],)).fetchone():
            raise ValueError("This file has already been imported. No changes were applied.")
        conn.execute("SELECT material_id FROM qcc_packaging_items FOR UPDATE").fetchall()
        current = {r["material_id"]: r for r in packaging_items(conn)}
        for entry in preview["rows"]:
            now = current.get(entry["material_id"])
            if (item_version(now) if now else "") != entry["version"]:
                raise ValueError(f"{entry['material_id']} changed since preview. Preview again.")
        for entry in preview["rows"]:
            if entry["action"] != "UNCHANGED":
                # Preserve fields not owned by this import even if changed since preview.
                record = dict(current.get(entry["material_id"], entry["record"]))
                for key in set(TEXT_FIELDS.values()) | set(NUMBER_FIELDS.values()):
                    if key in entry["record"]:
                        record[key] = entry["record"][key]
                save_packaging_item(record, initial_quantity=0, updated_by=actor,
                                    allow_update=entry["action"] != "NEW", _connection=conn)
        conn.execute("""INSERT INTO qcc_packaging_registry_imports
            (import_id,file_hash,filename,content,actor,changes) VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
            (import_id, preview["hash"], filename, content, actor,
             json.dumps([{k: r[k] for k in ("material_id","action","changes")} for r in preview["rows"]], default=str)))
    return import_id


def _balances(conn, material_id: str | None = None) -> list[dict]:
    balances = defaultdict(float)
    for row in packaging_seed_items():
        if material_id is None or row["material_id"] == material_id:
            balances[(row["material_id"], "UNASSIGNED", "", "")] += float(row.get("on_hand", 0) or 0)
    query = """SELECT material_id, UPPER(TRIM(COALESCE(NULLIF(location,''),'UNASSIGNED'))) AS location,
        COALESCE(lot_number,'') AS lot, COALESCE(expiration_date::text,'') AS expiration,
        SUM(quantity_delta) AS quantity FROM qcc_packaging_inventory_transactions"""
    params = ()
    if material_id:
        query += " WHERE material_id=%s"
        params = (material_id,)
    query += " GROUP BY 1,2,3,4"
    with conn.cursor(row_factory=dict_row) as cursor:
        for row in cursor.execute(query, params).fetchall():
            balances[(row["material_id"],row["location"],row["lot"],row["expiration"])] += float(row["quantity"])
    return [{"material_id": k[0], "location": k[1], "lot": k[2], "expiration": k[3], "quantity": round(v, 6)}
            for k,v in sorted(balances.items()) if abs(v) > 0.000001]


def warehouse_snapshot() -> dict:
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            locations = cursor.execute("SELECT * FROM qcc_packaging_locations ORDER BY code").fetchall()
            activities = cursor.execute("""SELECT a.*, EXISTS(SELECT 1 FROM qcc_packaging_activity r
                WHERE r.reversal_of=a.activity_id) AS reversed
                FROM qcc_packaging_activity a ORDER BY a.created_at DESC LIMIT 500""").fetchall()
            imports = cursor.execute("""SELECT import_id,filename,actor,created_at::text,
                jsonb_array_length(changes) AS rows FROM qcc_packaging_registry_imports
                ORDER BY created_at DESC LIMIT 100""").fetchall()
        return {"locations": locations, "balances": _balances(conn), "activities": activities, "imports": imports}


def save_location(form: dict, actor: str) -> str:
    code = str(form.get("code", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,79}", code):
        raise ValueError("Use a location code with letters, numbers, hyphens or underscores.")
    name = str(form.get("name", "")).strip().upper()
    if not name:
        raise ValueError("Location name is required.")
    active = form.get("status", "ACTIVE") == "ACTIVE"
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        _lock(conn)
        if not active and (code == "UNASSIGNED" or any(r["location"] == code and r["quantity"] for r in _balances(conn))):
            raise ValueError("Move stock out before deactivating this location. UNASSIGNED must remain active.")
        conn.execute("""INSERT INTO qcc_packaging_locations
            (code,name,warehouse,zone,rack,bin,active,updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,warehouse=EXCLUDED.warehouse,
            zone=EXCLUDED.zone,rack=EXCLUDED.rack,bin=EXCLUDED.bin,active=EXCLUDED.active,
            updated_by=EXCLUDED.updated_by,updated_at=NOW()""",
            (code,name,*[str(form.get(k, "")).strip().upper() for k in ("warehouse","zone","rack","bin")], active,actor))
    return code


def activity_legs(action: str, quantity: float, source: str, destination: str, balance: float) -> list[tuple[str,float]]:
    quantity = number(quantity)
    if action != "Physical Count" and quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if action in {"Receive", "Return from Production"}:
        return [(source, quantity)]
    if action == "Physical Count":
        return [(source, quantity - balance)]
    if action in {"Issue to Production", "Scrap / Damage", "Transfer Location"}:
        if quantity > balance + 0.000001:
            raise ValueError(f"Only {balance:g} is available at the selected location/lot.")
        if action == "Transfer Location":
            if source == destination:
                raise ValueError("Choose different source and destination locations.")
            return [(source,-quantity),(destination,quantity)]
        return [(source,-quantity)]
    raise ValueError("Select a supported activity.")


def post_activity(form: dict, actor: str, request_id: str) -> str:
    initialize()
    mid = str(form.get("material_id", "")).strip().upper()
    action = str(form.get("action", ""))
    reference = str(form.get("reference", "")).strip()
    reason = str(form.get("reason", "")).strip()
    if not reason:
        raise ValueError("Enter a reason or production/receiving note.")
    occurred = date.fromisoformat(str(form.get("date", "")))
    if occurred > date.today():
        raise ValueError("Activity date cannot be in the future.")
    if action == "Physical Count" and occurred != date.today():
        raise ValueError("Physical Count must reflect today's stock. Use the current count.")
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        _lock(conn)
        if conn.execute("SELECT 1 FROM qcc_packaging_activity WHERE activity_id=%s", (request_id,)).fetchone():
            return request_id
        item = next((r for r in packaging_items(conn) if r["material_id"] == mid), None)
        if not item:
            raise ValueError("Scan or enter a valid Material ID.")
        # Persist seed master only once; never replace a concurrently edited master.
        if not conn.execute("SELECT 1 FROM qcc_packaging_items WHERE material_id=%s", (mid,)).fetchone():
            save_packaging_item(item, updated_by=actor, allow_update=True, _connection=conn)
        balances = {(r["location"],r["lot"],r["expiration"]): r["quantity"] for r in _balances(conn, mid)}
        source = str(form.get("location", "")).strip().upper()
        dest = str(form.get("destination", "")).strip().upper()
        lot = str(form.get("lot", "")).strip()
        expiry = str(form.get("expiration", "") or "").strip()
        if expiry:
            date.fromisoformat(expiry)
        reversal = None
        if action == "Reverse Activity":
            reversal = str(form.get("reversal_of", "")).strip()
            with conn.cursor(row_factory=dict_row) as cursor:
                original = cursor.execute("SELECT * FROM qcc_packaging_activity WHERE activity_id=%s", (reversal,)).fetchone()
            if not original or original["material_id"] != mid or original["reversal_of"]:
                raise ValueError("Enter an original activity ID for this item.")
            if conn.execute("SELECT 1 FROM qcc_packaging_activity WHERE reversal_of=%s", (reversal,)).fetchone():
                raise ValueError("This activity has already been reversed.")
            legs = [(r["location"], -float(r["quantity"])) for r in original["details"]["legs"]]
            lot, expiry = original["details"]["lot"], original["details"]["expiration"]
            cost = original["details"].get("unit_cost", 0)
            for loc,delta in legs:
                if balances.get((loc,lot,expiry),0) + delta < -0.000001:
                    raise ValueError("Reversal would make location stock negative. Resolve subsequent usage first.")
        else:
            if item.get("status") != "ACTIVE" and action in {"Receive", "Issue to Production"}:
                raise ValueError("Reactivate this item before receiving or issuing it.")
            balance = balances.get((source,lot,expiry), 0)
            if action == "Physical Count" and abs(number(form.get("expected", 0)) - balance) > 0.000001:
                raise ValueError("Stock changed since preview. Refresh and review your physical count again.")
            if action in {"Receive", "Return from Production"} or (
                action == "Physical Count" and number(form.get("quantity", "")) > balance
            ):
                if item.get("lot_tracking") and not lot:
                    raise ValueError("A supplier lot is required for this item.")
                if item.get("expiration_tracking") and not expiry:
                    raise ValueError("An expiration date is required for this item.")
            legs = activity_legs(action, number(form.get("quantity", "")), source, dest, balance)
            cost = number(form.get("unit_cost", 0) or 0)
        for loc,delta in legs:
            found = conn.execute("SELECT active FROM qcc_packaging_locations WHERE code=%s", (loc,)).fetchone()
            if not found or (not found[0] and delta > 0):
                raise ValueError(f"Location {loc or '(blank)'} is missing or inactive.")
        details = {"lot":lot,"expiration":expiry,"unit_cost":cost,
            "counted": form.get("quantity") if action == "Physical Count" else None,
            "legs":[{"location":loc,"quantity":delta} for loc,delta in legs]}
        conn.execute("""INSERT INTO qcc_packaging_activity
            (activity_id,material_id,action,occurred_on,reference,reason,actor,reversal_of,details)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (request_id,mid,action,occurred,reference,reason,actor,reversal,json.dumps(details)))
        for loc,delta in legs:
            conn.execute("""INSERT INTO qcc_packaging_inventory_transactions
                (transaction_id,material_id,transaction_type,quantity_delta,uom,location,lot_number,
                expiration_date,unit_cost,reference,notes,occurred_at,created_by,activity_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()),mid,action,delta,item["uom"],loc,lot,expiry or None,cost,
                 reference,reason,occurred,actor,request_id))
    return request_id
