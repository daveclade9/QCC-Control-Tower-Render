"""Configurable, tenant-scoped ME/MP manufacturing work orders."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None

from .data import database_url
from .packaging_inventory import packaging_bom_recipes, packaging_items

DEFAULT_TENANT_ID = "QCC"
DEFAULT_FACILITY_ID = "BUILDING-33"
ORDER_TYPES = ("ME", "MP")
ORDER_TYPE_LABELS = {"ME": "MANUFACTURING EXTRACTION", "MP": "MANUFACTURING PRODUCTION"}
DEFAULT_SETTINGS = {
    "number_pattern_me": "QCC-ME-{year}-{sequence}",
    "number_pattern_mp": "QCC-MP-{year}-{sequence}",
    "sequence_padding": 4,
    "me_close_rule": "RECONCILED OUTPUT TAG",
    "mp_close_rule": "ATS LISTED",
    "ats_closed_statuses": ["ATS", "ACTIVE", "LISTED"],
    "substitution_approver_roles": ["ADMIN", "MANUFACTURING MANAGER", "MANUFACTURING SUPERVISOR"],
    "prevent_self_approval": True,
    "allow_manual_reconciliation": True,
}
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def quantity(value: Any, label: str = "Quantity", allow_zero: bool = False) -> float:
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a number.") from error
    if result < 0 or (not allow_zero and result == 0) or result != result:
        suffix = "zero or greater" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {suffix}.")
    return result


def normalized_type(value: Any) -> str:
    result = str(value or "").strip().upper()
    if result not in ORDER_TYPES:
        raise ValueError("Work order type must be ME or MP.")
    return result


def normalized_role(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def json_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        return [normalized_role(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [normalized_role(item) for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
        return [normalized_role(item) for item in value.split(",") if item.strip()]
    return list(default)


def should_close_work_order(order_type: str, reconciled: bool, ats_status: str,
                            config: dict[str, Any]) -> bool:
    kind = normalized_type(order_type)
    rule = normalized_role(config.get("me_close_rule" if kind == "ME" else "mp_close_rule"))
    if rule == "RECONCILED OUTPUT TAG":
        return bool(reconciled)
    if rule == "ATS LISTED":
        allowed = json_list(config.get("ats_closed_statuses"), DEFAULT_SETTINGS["ats_closed_statuses"])
        return normalized_role(ats_status) in allowed
    if rule == "MANUAL":
        return False
    raise ValueError(f"Unsupported {kind} close rule: {rule or 'blank'}.")

def validate_substitution_approver(requested_by: str, actor: str, actor_role: str,
                                   config: dict[str, Any]) -> None:
    allowed = json_list(config.get("substitution_approver_roles"), DEFAULT_SETTINGS["substitution_approver_roles"])
    if normalized_role(actor_role) not in allowed:
        raise ValueError("A configured Manufacturing Supervisor, Manager, or Admin must approve this substitution.")
    if config.get("prevent_self_approval", True) and normalized_role(requested_by) == normalized_role(actor):
        raise ValueError("The user who requested a substitution cannot approve it.")


def _require_database() -> None:
    if not database_url() or psycopg is None:
        raise RuntimeError("Supabase is required for Manufacturing work orders.")


def initialize() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    _require_database()
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with psycopg.connect(database_url(), connect_timeout=15) as conn:
            statements = [
                """CREATE TABLE IF NOT EXISTS qcc_manufacturing_settings (
                    tenant_id TEXT NOT NULL, facility_id TEXT NOT NULL, settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_by TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (tenant_id,facility_id))""",
                """CREATE TABLE IF NOT EXISTS qcc_manufacturing_counters (
                    tenant_id TEXT NOT NULL, facility_id TEXT NOT NULL, order_type TEXT NOT NULL,
                    calendar_year INTEGER NOT NULL, next_sequence INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (tenant_id,facility_id,order_type,calendar_year))""",
                """CREATE TABLE IF NOT EXISTS qcc_manufacturing_work_orders (
                    work_order_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, facility_id TEXT NOT NULL,
                    work_order_number TEXT NOT NULL, order_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
                    product_name TEXT NOT NULL, strain TEXT NOT NULL DEFAULT '',
                    expected_output_quantity NUMERIC(14,4) NOT NULL DEFAULT 0, output_uom TEXT NOT NULL DEFAULT 'UNITS',
                    linked_bom_id TEXT NOT NULL DEFAULT '', requested_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    expected_completion_date DATE, requested_by TEXT NOT NULL, assigned_to TEXT NOT NULL DEFAULT '',
                    output_package_tag TEXT NOT NULL DEFAULT '', output_quantity NUMERIC(14,4),
                    output_location TEXT NOT NULL DEFAULT '', output_reconciled BOOLEAN NOT NULL DEFAULT FALSE,
                    ats_status TEXT NOT NULL DEFAULT '', cancellation_reason TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), closed_at TIMESTAMPTZ,
                    UNIQUE(tenant_id,work_order_number), CHECK(order_type IN ('ME','MP')))""",
            ]

            statements.extend([
                """CREATE TABLE IF NOT EXISTS qcc_manufacturing_inputs (
                    input_id TEXT PRIMARY KEY, work_order_id TEXT NOT NULL REFERENCES qcc_manufacturing_work_orders(work_order_id) ON DELETE CASCADE,
                    package_tag TEXT NOT NULL, item_name TEXT NOT NULL DEFAULT '', material_class TEXT NOT NULL DEFAULT '',
                    source_location TEXT NOT NULL DEFAULT '', reserved_quantity NUMERIC(14,4) NOT NULL,
                    consumed_quantity NUMERIC(14,4) NOT NULL DEFAULT 0, uom TEXT NOT NULL DEFAULT 'GRAMS',
                    created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(work_order_id,package_tag))""",
                """CREATE TABLE IF NOT EXISTS qcc_manufacturing_components (
                    component_id TEXT PRIMARY KEY, work_order_id TEXT NOT NULL REFERENCES qcc_manufacturing_work_orders(work_order_id) ON DELETE CASCADE,
                    material_id TEXT NOT NULL DEFAULT '', description TEXT NOT NULL, required_quantity NUMERIC(14,4) NOT NULL,
                    reserved_quantity NUMERIC(14,4) NOT NULL DEFAULT 0, consumed_quantity NUMERIC(14,4) NOT NULL DEFAULT 0,
                    uom TEXT NOT NULL DEFAULT 'EACH', source_location TEXT NOT NULL DEFAULT '', lot_number TEXT NOT NULL DEFAULT '',
                    substitution_status TEXT NOT NULL DEFAULT '', substitution_material_id TEXT NOT NULL DEFAULT '',
                    substitution_reason TEXT NOT NULL DEFAULT '', substitution_requested_by TEXT NOT NULL DEFAULT '',
                    substitution_approved_by TEXT NOT NULL DEFAULT '', substitution_approved_at TIMESTAMPTZ,
                    created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
                """CREATE TABLE IF NOT EXISTS qcc_manufacturing_events (
                    event_id TEXT PRIMARY KEY, work_order_id TEXT NOT NULL REFERENCES qcc_manufacturing_work_orders(work_order_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL, prior_status TEXT NOT NULL DEFAULT '', new_status TEXT NOT NULL DEFAULT '',
                    details JSONB NOT NULL DEFAULT '{}'::jsonb, actor TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
            ])
            for statement in statements:
                conn.execute(statement)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qcc_mwo_register ON qcc_manufacturing_work_orders(tenant_id,facility_id,created_at DESC)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_qcc_mwo_output_tag ON qcc_manufacturing_work_orders(output_package_tag) WHERE output_package_tag<>''")
            for table in ("qcc_manufacturing_settings","qcc_manufacturing_counters","qcc_manufacturing_work_orders",
                          "qcc_manufacturing_inputs","qcc_manufacturing_components","qcc_manufacturing_events"):
                conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        _SCHEMA_READY = True


def settings(tenant_id: str = DEFAULT_TENANT_ID,
             facility_id: str = DEFAULT_FACILITY_ID) -> dict[str, Any]:
    result = dict(DEFAULT_SETTINGS)
    if not database_url():
        return result
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT settings FROM qcc_manufacturing_settings WHERE tenant_id=%s AND facility_id=%s",
            (tenant_id, facility_id),
        ).fetchone()
    if row:
        result.update(dict(row["settings"] or {}))
    return result


def save_settings(values: dict[str, Any], actor: str,
                  tenant_id: str = DEFAULT_TENANT_ID,
                  facility_id: str = DEFAULT_FACILITY_ID) -> dict[str, Any]:
    merged = settings(tenant_id, facility_id)
    merged.update({
        "number_pattern_me": str(values.get("number_pattern_me", merged["number_pattern_me"])).strip(),
        "number_pattern_mp": str(values.get("number_pattern_mp", merged["number_pattern_mp"])).strip(),
        "sequence_padding": max(1, min(8, int(values.get("sequence_padding", merged["sequence_padding"])))),
        "me_close_rule": str(values.get("me_close_rule", merged["me_close_rule"])).strip().upper(),
        "mp_close_rule": str(values.get("mp_close_rule", merged["mp_close_rule"])).strip().upper(),
        "ats_closed_statuses": json_list(values.get("ats_closed_statuses"), merged["ats_closed_statuses"]),
        "substitution_approver_roles": json_list(values.get("substitution_approver_roles"), merged["substitution_approver_roles"]),
        "prevent_self_approval": bool(values.get("prevent_self_approval", True)),
        "allow_manual_reconciliation": bool(values.get("allow_manual_reconciliation", True)),
    })
    for kind in ORDER_TYPES:
        if "{sequence}" not in merged[f"number_pattern_{kind.lower()}"]:
            raise ValueError(f"{kind} number pattern must contain {{sequence}}.")
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        conn.execute("""INSERT INTO qcc_manufacturing_settings(tenant_id,facility_id,settings,updated_by)
            VALUES (%s,%s,%s::jsonb,%s) ON CONFLICT(tenant_id,facility_id) DO UPDATE SET
            settings=EXCLUDED.settings,updated_by=EXCLUDED.updated_by,updated_at=NOW()""",
            (tenant_id, facility_id, json.dumps(merged), actor))
    return merged


def _event(conn: Any, work_order_id: str, event_type: str, actor: str,
           prior_status: str = "", new_status: str = "", details: dict[str, Any] | None = None) -> None:
    conn.execute("""INSERT INTO qcc_manufacturing_events
        (event_id,work_order_id,event_type,prior_status,new_status,details,actor)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""",
        (str(uuid.uuid4()), work_order_id, event_type, prior_status, new_status,
         json.dumps(details or {}), actor))


def _next_number(conn: Any, order_type: str, config: dict[str, Any],
                 tenant_id: str, facility_id: str) -> str:
    year = date.today().year
    row = conn.execute("""INSERT INTO qcc_manufacturing_counters
        (tenant_id,facility_id,order_type,calendar_year,next_sequence) VALUES (%s,%s,%s,%s,2)
        ON CONFLICT(tenant_id,facility_id,order_type,calendar_year) DO UPDATE
        SET next_sequence=qcc_manufacturing_counters.next_sequence+1 RETURNING next_sequence-1""",
        (tenant_id, facility_id, order_type, year)).fetchone()
    sequence = str(int(row[0])).zfill(int(config["sequence_padding"]))
    return str(config[f"number_pattern_{order_type.lower()}"]).format(
        year=year, sequence=sequence, type=order_type, facility=facility_id)


def bom_options() -> list[dict[str, str]]:
    result = []
    for index, recipe in enumerate(packaging_bom_recipes()):
        name = str(recipe.get("recipe_name") or recipe.get("product_name") or recipe.get("name") or recipe.get("sku") or "").strip()
        if name:
            result.append({"id": str(recipe.get("bom_id") or recipe.get("id") or f"BOM-{index + 1}"), "name": name})
    return result


def available_packaging_components() -> list[dict[str, Any]]:
    rows = packaging_items()
    reserved: dict[str, float] = {}
    if database_url():
        initialize()
        with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
            for row in conn.execute("""SELECT c.material_id,SUM(c.reserved_quantity-c.consumed_quantity) AS reserved
                FROM qcc_manufacturing_components c JOIN qcc_manufacturing_work_orders w USING(work_order_id)
                WHERE w.status IN ('DRAFT','RELEASED','IN PROCESS','AWAITING OUTPUT','AWAITING ATS')
                AND c.material_id<>'' GROUP BY c.material_id""").fetchall():
                reserved[str(row["material_id"])] = float(row["reserved"] or 0)
    return [{
        "material_id": str(row.get("material_id") or ""), "description": str(row.get("item") or ""),
        "uom": str(row.get("uom") or "EACH"), "default_location": str(row.get("default_location") or "UNASSIGNED"),
        "available_quantity": max(float(row.get("on_hand", 0) or 0) - reserved.get(str(row.get("material_id") or ""), 0), 0),
    } for row in rows if str(row.get("status") or "ACTIVE").upper() == "ACTIVE"]


def _bom_components(bom_id: str, output_quantity: float) -> list[dict[str, Any]]:
    selected = None
    for index, recipe in enumerate(packaging_bom_recipes()):
        if str(recipe.get("bom_id") or recipe.get("id") or f"BOM-{index + 1}") == bom_id:
            selected = recipe
            break
    if not selected:
        return []
    stock = {row["material_id"]: row for row in available_packaging_components()}
    result = []
    for component in selected.get("components") or selected.get("items") or []:
        description = str(component.get("description") or component.get("name") or component.get("item") or "").strip()
        material_id = str(component.get("material_id") or component.get("sku") or "").strip().upper()
        if not material_id and description:
            material_id = next((key for key, row in stock.items()
                                if str(row.get("description", "")).strip().casefold() == description.casefold()), "")
        per_unit = quantity(component.get("quantity_per_unit") or component.get("qty_per_unit") or component.get("quantity") or 1)
        required = round(per_unit * output_quantity, 4)
        available = stock.get(material_id, {})
        result.append({
            "material_id": material_id,
            "description": description or str(available.get("description") or "UNMAPPED BOM COMPONENT"),
            "required_quantity": required,
            "reserved_quantity": min(required, float(available.get("available_quantity", 0) or 0)),
            "uom": str(component.get("uom") or available.get("uom") or "EACH").upper(),
            "source_location": str(available.get("default_location") or ""),
        })
    return result


def create_work_order(form: dict[str, Any], actor: str,
                      tenant_id: str = DEFAULT_TENANT_ID,
                      facility_id: str = DEFAULT_FACILITY_ID) -> str:
    kind = normalized_type(form.get("order_type"))
    product = str(form.get("product_name") or "").strip()
    if not product:
        raise ValueError("Product name is required.")
    expected = quantity(form.get("expected_output_quantity"), "Expected output")
    requested = date.fromisoformat(str(form.get("requested_date") or date.today().isoformat()))
    completion_text = str(form.get("expected_completion_date") or "").strip()
    completion = date.fromisoformat(completion_text) if completion_text else None
    config = settings(tenant_id, facility_id)
    initialize()
    work_order_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        number = _next_number(conn, kind, config, tenant_id, facility_id)
        conn.execute("""INSERT INTO qcc_manufacturing_work_orders (
            work_order_id,tenant_id,facility_id,work_order_number,order_type,status,product_name,strain,
            expected_output_quantity,output_uom,linked_bom_id,requested_date,expected_completion_date,
            requested_by,assigned_to,notes,created_by,updated_by)
            VALUES (%s,%s,%s,%s,%s,'DRAFT',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
            work_order_id, tenant_id, facility_id, number, kind, product, str(form.get("strain") or "").strip(),
            expected, str(form.get("output_uom") or ("GRAMS" if kind == "ME" else "UNITS")).strip().upper(),
            str(form.get("linked_bom_id") or "").strip(), requested, completion,
            str(form.get("requested_by") or actor).strip(), str(form.get("assigned_to") or "").strip(),
            str(form.get("notes") or "").strip(), actor, actor))
        for component in _bom_components(str(form.get("linked_bom_id") or ""), expected):
            conn.execute("""INSERT INTO qcc_manufacturing_components
                (component_id,work_order_id,material_id,description,required_quantity,reserved_quantity,uom,source_location,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), work_order_id, component["material_id"], component["description"],
                 component["required_quantity"], component["reserved_quantity"], component["uom"],
                 component["source_location"], actor))
        _event(conn, work_order_id, "CREATED", actor, new_status="DRAFT", details={"number": number, "type": kind})
    return work_order_id


def list_work_orders(tenant_id: str = DEFAULT_TENANT_ID,
                     facility_id: str = DEFAULT_FACILITY_ID) -> list[dict[str, Any]]:
    if not database_url():
        return []
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        return conn.execute("""SELECT work_order_id,work_order_number,order_type,status,product_name,strain,
            expected_output_quantity,output_uom,requested_date::text,COALESCE(expected_completion_date::text,'') AS expected_completion_date,
            requested_by,assigned_to,output_package_tag,ats_status,updated_at::text
            FROM qcc_manufacturing_work_orders WHERE tenant_id=%s AND facility_id=%s
            ORDER BY created_at DESC LIMIT 500""", (tenant_id, facility_id)).fetchall()


def work_order_detail(work_order_id: str) -> dict[str, Any]:
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        order = conn.execute("SELECT * FROM qcc_manufacturing_work_orders WHERE work_order_id=%s", (work_order_id,)).fetchone()
        if not order:
            raise ValueError("Work order was not found.")
        inputs = conn.execute("SELECT * FROM qcc_manufacturing_inputs WHERE work_order_id=%s ORDER BY created_at", (work_order_id,)).fetchall()
        components = conn.execute("SELECT * FROM qcc_manufacturing_components WHERE work_order_id=%s ORDER BY created_at", (work_order_id,)).fetchall()
        events = conn.execute("SELECT event_type,prior_status,new_status,details,actor,created_at::text FROM qcc_manufacturing_events WHERE work_order_id=%s ORDER BY created_at DESC", (work_order_id,)).fetchall()
    return {"order": dict(order), "inputs": [dict(row) for row in inputs],
            "components": [dict(row) for row in components], "events": [dict(row) for row in events]}


def available_cannabis_packages() -> list[dict[str, Any]]:
    if not database_url():
        return []
    initialize()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        rows = conn.execute("""WITH latest AS (SELECT snapshot_id FROM inventory_snapshots
                WHERE status='Published' ORDER BY published_at DESC LIMIT 1)
            SELECT p.package_tag,p.item,p.location,p.quantity,p.unit AS uom
            FROM inventory_snapshot_packages p JOIN latest USING(snapshot_id)
            WHERE COALESCE(p.quantity,0)>0
            ORDER BY p.item,p.package_tag""").fetchall()
        reserved_rows = conn.execute("""SELECT i.package_tag,
            SUM(CASE WHEN w.status='CANCELLED' THEN i.consumed_quantity
                     WHEN w.status IN ('DRAFT','RELEASED','IN PROCESS','AWAITING OUTPUT','AWAITING ATS')
                     THEN i.reserved_quantity ELSE i.consumed_quantity END) AS reserved
            FROM qcc_manufacturing_inputs i JOIN qcc_manufacturing_work_orders w USING(work_order_id)
            GROUP BY i.package_tag""").fetchall()
    reserved = {str(row["package_tag"]): float(row["reserved"] or 0) for row in reserved_rows}
    result = []
    for row in rows:
        available = max(float(row["quantity"] or 0) - reserved.get(str(row["package_tag"]), 0), 0)
        if available > 0:
            result.append({**dict(row), "available_quantity": round(available, 4)})
    return result


def _editable_order(conn: Any, work_order_id: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute("SELECT * FROM qcc_manufacturing_work_orders WHERE work_order_id=%s FOR UPDATE", (work_order_id,)).fetchone()
    if not row:
        raise ValueError("Work order was not found.")
    if row["status"] not in ("DRAFT", "RELEASED", "IN PROCESS"):
        raise ValueError(f"This action is not available while the work order is {row['status']}.")
    return dict(row)


def add_cannabis_input(work_order_id: str, package_tag: str, reserved_quantity: Any, actor: str) -> str:
    package = next((row for row in available_cannabis_packages() if str(row["package_tag"]) == package_tag), None)
    if not package:
        raise ValueError("Select an available cannabis package.")
    requested = quantity(reserved_quantity, "Reserved quantity")
    if requested > float(package["available_quantity"]):
        raise ValueError(f"Only {package['available_quantity']:g} {package['uom']} is available.")
    input_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        _editable_order(conn, work_order_id)
        conn.execute("""INSERT INTO qcc_manufacturing_inputs
            (input_id,work_order_id,package_tag,item_name,source_location,reserved_quantity,uom,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (input_id, work_order_id, package_tag, package["item"], package["location"], requested, package["uom"], actor))
        _event(conn, work_order_id, "INPUT RESERVED", actor, details={"package_tag": package_tag, "quantity": requested})
    return input_id


def add_packaging_component(work_order_id: str, material_id: str, required_quantity: Any,
                            actor: str, source_location: str = "", lot_number: str = "") -> str:
    item = next((row for row in available_packaging_components() if row["material_id"] == material_id), None)
    if not item:
        raise ValueError("Select an active packaging material.")
    required = quantity(required_quantity, "Required quantity")
    reserved = min(required, float(item["available_quantity"]))
    component_id = str(uuid.uuid4())
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        _editable_order(conn, work_order_id)
        conn.execute("""INSERT INTO qcc_manufacturing_components
            (component_id,work_order_id,material_id,description,required_quantity,reserved_quantity,uom,source_location,lot_number,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (component_id, work_order_id, material_id, item["description"], required, reserved, item["uom"],
             source_location or item["default_location"], lot_number, actor))
        _event(conn, work_order_id, "COMPONENT RESERVED", actor,
               details={"material_id": material_id, "required": required, "reserved": reserved})
    return component_id


def release_work_order(work_order_id: str, actor: str) -> None:
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        order = _editable_order(conn, work_order_id)
        if order["status"] != "DRAFT":
            raise ValueError("Only a draft work order can be released.")
        count = conn.execute("SELECT COUNT(*) AS count FROM qcc_manufacturing_inputs WHERE work_order_id=%s", (work_order_id,)).fetchone()["count"]
        if not count:
            raise ValueError("Reserve at least one cannabis input before release.")
        if order["order_type"] == "MP":
            shortage = conn.execute("""SELECT COUNT(*) AS count FROM qcc_manufacturing_components
                WHERE work_order_id=%s AND ((material_id='' AND NOT (substitution_status='APPROVED' AND substitution_material_id<>'')) OR reserved_quantity<required_quantity)""",
                (work_order_id,)).fetchone()["count"]
            if shortage:
                raise ValueError("Resolve unmapped or short packaging components before releasing this MP order.")
        conn.execute("UPDATE qcc_manufacturing_work_orders SET status='RELEASED',updated_by=%s,updated_at=NOW() WHERE work_order_id=%s", (actor, work_order_id))
        _event(conn, work_order_id, "RELEASED", actor, "DRAFT", "RELEASED")


def confirm_consumption(work_order_id: str, line_type: str, line_id: str,
                        consumed_quantity: Any, actor: str) -> None:
    amount = quantity(consumed_quantity, "Consumed quantity")
    kind = str(line_type).strip().upper()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        order = _editable_order(conn, work_order_id)
        if order["status"] not in ("RELEASED", "IN PROCESS"):
            raise ValueError("Release the work order before confirming consumption.")
        if kind == "CANNABIS":
            line = conn.execute("SELECT * FROM qcc_manufacturing_inputs WHERE input_id=%s AND work_order_id=%s FOR UPDATE", (line_id, work_order_id)).fetchone()
            if not line or amount > float(line["reserved_quantity"]):
                raise ValueError("Consumption cannot exceed the reserved cannabis quantity.")
            conn.execute("UPDATE qcc_manufacturing_inputs SET consumed_quantity=%s WHERE input_id=%s", (amount, line_id))
            details = {"package_tag": line["package_tag"], "quantity": amount, "uom": line["uom"]}
        elif kind == "PACKAGING":
            line = conn.execute("SELECT * FROM qcc_manufacturing_components WHERE component_id=%s AND work_order_id=%s FOR UPDATE", (line_id, work_order_id)).fetchone()
            if not line or amount > float(line["reserved_quantity"]):
                raise ValueError("Consumption cannot exceed the reserved packaging quantity.")
            if not line["source_location"]:
                raise ValueError("Enter a source location before consuming packaging.")
            from .warehouse import post_activity
            # The deterministic request ID prevents a retry from deducting inventory twice.
            post_activity({
                "material_id": line["substitution_material_id"] or line["material_id"],
                "action": "Issue to Production", "quantity": amount,
                "location": line["source_location"], "lot": line["lot_number"], "expiration": "",
                "unit_cost": 0, "reference": order["work_order_number"],
                "reason": f"Consumed by {order['work_order_number']}", "date": date.today().isoformat(),
            }, actor, f"MWO-{work_order_id}-{line_id}-{amount:g}")
            conn.execute("UPDATE qcc_manufacturing_components SET consumed_quantity=%s WHERE component_id=%s", (amount, line_id))
            details = {"material_id": line["substitution_material_id"] or line["material_id"], "quantity": amount, "uom": line["uom"]}
        else:
            raise ValueError("Line type must be CANNABIS or PACKAGING.")
        conn.execute("UPDATE qcc_manufacturing_work_orders SET status='IN PROCESS',updated_by=%s,updated_at=NOW() WHERE work_order_id=%s", (actor, work_order_id))
        _event(conn, work_order_id, f"{kind} CONSUMED", actor, order["status"], "IN PROCESS", details)


def request_substitution(work_order_id: str, component_id: str, replacement_material_id: str,
                         reason: str, actor: str) -> None:
    if not str(reason).strip():
        raise ValueError("A substitution reason is required.")
    if not any(row["material_id"] == replacement_material_id for row in available_packaging_components()):
        raise ValueError("Select an active replacement material.")
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        _editable_order(conn, work_order_id)
        changed = conn.execute("""UPDATE qcc_manufacturing_components SET substitution_status='PENDING',
            substitution_material_id=%s,substitution_reason=%s,substitution_requested_by=%s,
            substitution_approved_by='',substitution_approved_at=NULL WHERE component_id=%s AND work_order_id=%s""",
            (replacement_material_id, reason.strip(), actor, component_id, work_order_id)).rowcount
        if not changed:
            raise ValueError("Packaging component was not found.")
        _event(conn, work_order_id, "SUBSTITUTION REQUESTED", actor,
               details={"component_id": component_id, "replacement_material_id": replacement_material_id, "reason": reason.strip()})


def approve_substitution(work_order_id: str, component_id: str, actor: str, actor_role: str) -> None:
    config = settings()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('qcc-manufacturing-reservations'))")
        _editable_order(conn, work_order_id)
        line = conn.execute("SELECT * FROM qcc_manufacturing_components WHERE component_id=%s AND work_order_id=%s FOR UPDATE", (component_id, work_order_id)).fetchone()
        if not line or line["substitution_status"] != "PENDING":
            raise ValueError("No pending substitution was found for that component.")
        validate_substitution_approver(line["substitution_requested_by"], actor, actor_role, config)
        replacement = next((row for row in available_packaging_components()
                            if row["material_id"] == line["substitution_material_id"]), None)
        if not replacement:
            raise ValueError("The replacement material is no longer active.")
        reserved = min(float(line["required_quantity"]), float(replacement["available_quantity"]))
        conn.execute("""UPDATE qcc_manufacturing_components SET substitution_status='APPROVED',
            reserved_quantity=%s,source_location=%s,substitution_approved_by=%s,
            substitution_approved_at=NOW() WHERE component_id=%s""",
            (reserved, replacement["default_location"], actor, component_id))
        _event(conn, work_order_id, "SUBSTITUTION APPROVED", actor,
               details={"component_id": component_id, "replacement_material_id": line["substitution_material_id"],
                        "reserved_quantity": reserved})

def record_output(work_order_id: str, package_tag: str, output_quantity: Any,
                  output_location: str, reconciled: bool, ats_status: str, actor: str) -> str:
    tag = str(package_tag or "").strip().upper()
    if not tag:
        raise ValueError("The single finished Metrc tag is required.")
    amount = quantity(output_quantity, "Output quantity")
    config = settings()
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        order = conn.execute("SELECT * FROM qcc_manufacturing_work_orders WHERE work_order_id=%s FOR UPDATE", (work_order_id,)).fetchone()
        if not order or order["status"] in ("CANCELLED", "CLOSED"):
            raise ValueError("Output cannot be recorded for this work order.")
        existing = conn.execute("SELECT work_order_id FROM qcc_manufacturing_work_orders WHERE output_package_tag=%s AND work_order_id<>%s", (tag, work_order_id)).fetchone()
        if existing:
            raise ValueError("That output Metrc tag is already assigned to another work order.")
        close = should_close_work_order(order["order_type"], reconciled, ats_status, config)
        status = "CLOSED" if close else ("AWAITING ATS" if order["order_type"] == "MP" else "AWAITING OUTPUT")
        conn.execute("""UPDATE qcc_manufacturing_work_orders SET output_package_tag=%s,output_quantity=%s,
            output_location=%s,output_reconciled=%s,ats_status=%s,status=%s,
            closed_at=CASE WHEN %s THEN NOW() ELSE NULL END,updated_by=%s,updated_at=NOW()
            WHERE work_order_id=%s""",
            (tag, amount, output_location.strip().upper(), bool(reconciled), ats_status.strip().upper(),
             status, close, actor, work_order_id))
        _event(conn, work_order_id, "OUTPUT RECORDED", actor, order["status"], status,
               {"package_tag": tag, "quantity": amount, "reconciled": bool(reconciled), "ats_status": ats_status})
    return status


def cancel_work_order(work_order_id: str, reason: str, actor: str) -> None:
    if not str(reason).strip():
        raise ValueError("A cancellation reason is required.")
    with psycopg.connect(database_url(), connect_timeout=15, row_factory=dict_row) as conn:
        order = conn.execute("SELECT * FROM qcc_manufacturing_work_orders WHERE work_order_id=%s FOR UPDATE", (work_order_id,)).fetchone()
        if not order or order["status"] in ("CLOSED", "CANCELLED"):
            raise ValueError("Only an open work order can be cancelled.")
        conn.execute("UPDATE qcc_manufacturing_work_orders SET status='CANCELLED',cancellation_reason=%s,updated_by=%s,updated_at=NOW() WHERE work_order_id=%s", (reason.strip(), actor, work_order_id))
        _event(conn, work_order_id, "CANCELLED", actor, order["status"], "CANCELLED", {"reason": reason.strip()})
