"""Editable cultivation schedule, room, bench, and harvest registries.

The registry deliberately falls back to the confirmed legacy cultivation
constants when Supabase is unavailable.  This keeps local/demo builds useful
while allowing the shared staging app to become data-driven.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import re
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover - demo builds do not require psycopg.
    psycopg = None

from .cultivation import (
    CLONE_PLANNING_FIRST_CROP,
    CLONE_PLANNING_FIRST_CUT_DATE,
    DEFAULT_POST_HARVEST_DAYS,
    PLANTS_PER_SQUARE_FOOT,
    ROOM_LAYOUTS,
    ROOTING_DAYS,
    STANDARD_FLOWER_DAYS,
    VEG_DAYS,
    bench_plant_capacity,
)
from .data import database_url


DEFAULT_PROGRAM_ID = "main-five-room"
DEFAULT_PROGRAM_NAME = "Main F1-F5 Rotation"
DEFAULT_FUTURE_CROPS = 26
OVERHEAD_LIGHTING_TYPES = ("HPS", "LED", "MH", "Other")
SUPPLEMENTAL_LIGHTING_TYPES = ("None", "Undercanopy", "Intercanopy", "Other")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def calculate_room_metrics(length: Any, width: Any, height: Any) -> dict[str, float]:
    """Return floor area and volume without changing physical canopy."""
    room_length = max(0.0, _number(length))
    room_width = max(0.0, _number(width))
    room_height = max(0.0, _number(height))
    return {
        "floor_area_sqft": round(room_length * room_width, 1),
        "volume_cuft": round(room_length * room_width * room_height, 1),
    }


def calculate_bench_metrics(
    length: Any,
    width: Any,
    density: Any = PLANTS_PER_SQUARE_FOOT,
) -> dict[str, float | int]:
    """Calculate locked physical canopy and the default plant target."""
    bench_length = max(0.0, _number(length))
    bench_width = max(0.0, _number(width))
    canopy = round(bench_length * bench_width, 1)
    return {
        "canopy_sqft": canopy,
        "target_plants": bench_plant_capacity(canopy, max(0.0, _number(density))),
    }


def calculate_lighting_total(
    rows_or_fixtures: Any,
    watts_each: Any,
    override_watts: Any = 0,
) -> float:
    """Use the explicit override when supplied, otherwise calculate the total."""
    override = max(0.0, _number(override_watts))
    if override > 0:
        return round(override, 1)
    return round(max(0, _integer(rows_or_fixtures)) * max(0.0, _number(watts_each)), 1)


def fresh_frozen_canopy(
    *,
    planted_canopy_sqft: Any,
    planted_plants: Any,
    fresh_frozen_plants: Any = 0,
    actual_fresh_frozen_canopy_sqft: Any = 0,
) -> dict[str, float | int]:
    """Translate Fresh Frozen plants into an auditable net dry canopy.

    Crop-report canopy is authoritative when supplied.  Before harvest, the
    planned plant share proportionally reduces planted canopy.
    """
    planted_canopy = max(0.0, _number(planted_canopy_sqft))
    plant_count = max(0, _integer(planted_plants))
    ff_plants = min(plant_count, max(0, _integer(fresh_frozen_plants)))
    actual_canopy = max(0.0, _number(actual_fresh_frozen_canopy_sqft))
    if actual_canopy > 0:
        ff_canopy = min(planted_canopy, actual_canopy)
        source = "Actual crop record"
    elif plant_count > 0 and ff_plants > 0:
        ff_canopy = planted_canopy * ff_plants / plant_count
        source = "Planned plant proportion"
    else:
        ff_canopy = 0.0
        source = "No Fresh Frozen diversion"
    return {
        "planted_canopy_sqft": round(planted_canopy, 1),
        "fresh_frozen_plants": ff_plants,
        "fresh_frozen_canopy_sqft": round(ff_canopy, 1),
        "net_dry_canopy_sqft": round(max(0.0, planted_canopy - ff_canopy), 1),
        "fresh_frozen_percent": round((ff_canopy / planted_canopy * 100) if planted_canopy else 0.0, 1),
        "fresh_frozen_source": source,
    }


def default_cycle_program() -> dict[str, Any]:
    return {
        "program_id": DEFAULT_PROGRAM_ID,
        "name": DEFAULT_PROGRAM_NAME,
        "code_prefix": "F",
        "cadence_days": 14,
        "rooting_days": ROOTING_DAYS,
        "veg_days": VEG_DAYS,
        "flowering_days": STANDARD_FLOWER_DAYS,
        "processing_days": DEFAULT_POST_HARVEST_DAYS,
        "target_future_crops": DEFAULT_FUTURE_CROPS,
        "room_rotation": [f"Flower Room {number}" for number in range(1, 6)],
        "active": True,
    }


def default_room_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position, room_name in enumerate(ROOM_LAYOUTS, start=1):
        canopy = sum(float(row["length"]) * float(row["width"]) for row in ROOM_LAYOUTS[room_name])
        rows.append({
            "room_id": re.sub(r"[^a-z0-9]+", "-", room_name.lower()).strip("-"),
            "room_code": f"F{position}",
            "name": room_name,
            "building": "1A",
            "program_id": DEFAULT_PROGRAM_ID,
            "length_ft": 0.0,
            "width_ft": 0.0,
            "height_ft": 0.0,
            "floor_area_sqft": 0.0,
            "volume_cuft": 0.0,
            "physical_canopy_sqft": round(canopy, 1),
            "overhead_type": "Other",
            "overhead_other": "Not entered",
            "fixture_count": 0,
            "watts_per_fixture": 0.0,
            "total_overhead_watts": 0.0,
            "effective_date": "",
            "retired_date": "",
            "active": True,
            "notes": "Legacy confirmed room; enter dimensions and lighting details.",
        })
    return rows


def default_bench_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for room in default_room_rows():
        for position, bench in enumerate(ROOM_LAYOUTS[room["name"]], start=1):
            metrics = calculate_bench_metrics(bench["length"], bench["width"])
            rows.append({
                "bench_id": f'{room["room_id"]}-{position}',
                "room_id": room["room_id"],
                "room_name": room["name"],
                "bench": bench["bench"],
                "length_ft": float(bench["length"]),
                "width_ft": float(bench["width"]),
                "canopy_sqft": metrics["canopy_sqft"],
                "default_density": PLANTS_PER_SQUARE_FOOT,
                "target_plants": metrics["target_plants"],
                "supplemental_type": "None",
                "supplemental_rows": 0,
                "watts_per_row": 0.0,
                "supplemental_watts_override": 0.0,
                "total_supplemental_watts": 0.0,
                "effective_date": "",
                "retired_date": "",
                "active": True,
                "notes": "",
            })
    return rows


def _crop_parts(crop: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)\.(\d+)", str(crop or "").strip())
    if not match:
        raise ValueError("Starting crop must use a format like F5.10.")
    return match.group(1).upper(), int(match.group(2)), int(match.group(3))


def generate_schedule(
    *,
    program: dict[str, Any],
    rooms: list[dict[str, Any]],
    start_crop: str,
    first_clone_cut: str | date,
    count: int,
) -> list[dict[str, Any]]:
    """Generate one program's rotation without assuming five rooms or 14 days."""
    prefix, starting_room_number, cycle_number = _crop_parts(start_crop)
    rotation = list(program.get("room_rotation") or [])
    if not rotation:
        rotation = [row["name"] for row in rooms if row.get("active", True)]
    if not rotation:
        raise ValueError("The cycle program needs at least one active flower room.")
    room_by_name = {str(row.get("name")): row for row in rooms}
    start_index = 0
    for index, room_name in enumerate(rotation):
        code = str(room_by_name.get(room_name, {}).get("room_code", ""))
        if code.upper() == f"{prefix}{starting_room_number}" or room_name.endswith(f" {starting_room_number}"):
            start_index = index
            break
    first_cut = first_clone_cut if isinstance(first_clone_cut, date) else date.fromisoformat(str(first_clone_cut))
    cadence = max(1, _integer(program.get("cadence_days"), 14))
    clone_to_flower = max(0, _integer(program.get("rooting_days"), ROOTING_DAYS)) + max(0, _integer(program.get("veg_days"), VEG_DAYS))
    flower_days = max(1, _integer(program.get("flowering_days"), STANDARD_FLOWER_DAYS))
    processing_days = max(0, _integer(program.get("processing_days"), DEFAULT_POST_HARVEST_DAYS))
    rows: list[dict[str, Any]] = []
    for offset in range(max(1, int(count))):
        rotation_position = start_index + offset
        room_index = rotation_position % len(rotation)
        completed_rotations = rotation_position // len(rotation) - start_index // len(rotation)
        room_name = rotation[room_index]
        room = room_by_name.get(room_name, {})
        room_code = str(room.get("room_code") or f"{prefix}{room_index + 1}")
        cut = first_cut + timedelta(days=cadence * offset)
        flower = cut + timedelta(days=clone_to_flower)
        harvest = flower + timedelta(days=flower_days)
        available = harvest + timedelta(days=processing_days)
        rows.append({
            "schedule_id": f'{program.get("program_id", DEFAULT_PROGRAM_ID)}-{room_code}-{cycle_number + completed_rotations}',
            "program_id": str(program.get("program_id", DEFAULT_PROGRAM_ID)),
            "program": str(program.get("name", DEFAULT_PROGRAM_NAME)),
            "crop": f"{room_code}.{cycle_number + completed_rotations}",
            "room": room_name,
            "clone_cut_date": cut.isoformat(),
            "flower_entry_date": flower.isoformat(),
            "harvest_date": harvest.isoformat(),
            "available_date": available.isoformat(),
            "status": "Planning" if offset == 0 else "Upcoming",
            "source": "Generated",
        })
    return rows


def default_schedule(count: int = DEFAULT_FUTURE_CROPS) -> list[dict[str, Any]]:
    return generate_schedule(
        program=default_cycle_program(),
        rooms=default_room_rows(),
        start_crop=CLONE_PLANNING_FIRST_CROP,
        first_clone_cut=CLONE_PLANNING_FIRST_CUT_DATE,
        count=count,
    )


def schedule_conflicts(rows: list[dict[str, Any]]) -> list[str]:
    """Flag two crops occupying the same room during overlapping flower windows."""
    errors: list[str] = []
    by_room: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_room.setdefault(str(row.get("room", "")), []).append(row)
    for room, room_rows in by_room.items():
        ordered = sorted(room_rows, key=lambda row: str(row.get("flower_entry_date", "")))
        for previous, current in zip(ordered, ordered[1:]):
            if str(current.get("flower_entry_date", "")) < str(previous.get("harvest_date", "")):
                errors.append(f'{room}: {previous.get("crop")} overlaps {current.get("crop")}')
    return errors


def _ensure_schema(cursor: Any) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qcc_cultivation_cycle_programs (
            program_id TEXT PRIMARY KEY, name TEXT NOT NULL, code_prefix TEXT NOT NULL,
            cadence_days INTEGER NOT NULL, rooting_days INTEGER NOT NULL,
            veg_days INTEGER NOT NULL, flowering_days INTEGER NOT NULL,
            processing_days INTEGER NOT NULL, target_future_crops INTEGER NOT NULL,
            room_rotation JSONB NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
            updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qcc_cultivation_rooms (
            room_id TEXT PRIMARY KEY, room_code TEXT NOT NULL, name TEXT NOT NULL,
            building TEXT NOT NULL DEFAULT '', program_id TEXT NOT NULL DEFAULT '',
            length_ft DOUBLE PRECISION NOT NULL DEFAULT 0,
            width_ft DOUBLE PRECISION NOT NULL DEFAULT 0,
            height_ft DOUBLE PRECISION NOT NULL DEFAULT 0,
            overhead_type TEXT NOT NULL DEFAULT 'Other', overhead_other TEXT NOT NULL DEFAULT '',
            fixture_count INTEGER NOT NULL DEFAULT 0,
            watts_per_fixture DOUBLE PRECISION NOT NULL DEFAULT 0,
            overhead_watts_override DOUBLE PRECISION NOT NULL DEFAULT 0,
            effective_date DATE, retired_date DATE, active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qcc_cultivation_benches (
            bench_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, bench TEXT NOT NULL,
            length_ft DOUBLE PRECISION NOT NULL, width_ft DOUBLE PRECISION NOT NULL,
            default_density DOUBLE PRECISION NOT NULL DEFAULT 0.75,
            supplemental_type TEXT NOT NULL DEFAULT 'None',
            supplemental_rows INTEGER NOT NULL DEFAULT 0,
            watts_per_row DOUBLE PRECISION NOT NULL DEFAULT 0,
            supplemental_watts_override DOUBLE PRECISION NOT NULL DEFAULT 0,
            effective_date DATE, retired_date DATE, active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT NOT NULL DEFAULT '', display_order INTEGER NOT NULL DEFAULT 0,
            updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qcc_cultivation_schedule (
            schedule_id TEXT PRIMARY KEY, program_id TEXT NOT NULL, crop TEXT NOT NULL,
            room TEXT NOT NULL, clone_cut_date DATE NOT NULL, flower_entry_date DATE NOT NULL,
            harvest_date DATE NOT NULL, available_date DATE NOT NULL,
            status TEXT NOT NULL, source TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qcc_cultivation_historical_yields (
            harvest_id TEXT PRIMARY KEY, crop TEXT NOT NULL, room TEXT NOT NULL,
            strain TEXT NOT NULL DEFAULT '', harvest_date DATE NOT NULL,
            physical_canopy_sqft DOUBLE PRECISION NOT NULL DEFAULT 0,
            planted_canopy_sqft DOUBLE PRECISION NOT NULL DEFAULT 0,
            planted_plants INTEGER NOT NULL DEFAULT 0,
            planned_ff_plants INTEGER NOT NULL DEFAULT 0,
            actual_ff_plants INTEGER NOT NULL DEFAULT 0,
            actual_ff_canopy_sqft DOUBLE PRECISION NOT NULL DEFAULT 0,
            wet_yield_lbs DOUBLE PRECISION NOT NULL DEFAULT 0,
            dry_flower_lbs DOUBLE PRECISION NOT NULL DEFAULT 0,
            ab_flower_lbs DOUBLE PRECISION NOT NULL DEFAULT 0,
            c_flower_lbs DOUBLE PRECISION NOT NULL DEFAULT 0,
            trim_lbs DOUBLE PRECISION NOT NULL DEFAULT 0,
            quality_score DOUBLE PRECISION NOT NULL DEFAULT 0,
            data_source TEXT NOT NULL DEFAULT 'Manual', notes TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL)
    """)


def _fetch_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = [item.name for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_registry() -> dict[str, list[dict[str, Any]]]:
    """Load the shared registries, seeding legacy defaults on first use."""
    fallback = {
        "programs": [default_cycle_program()],
        "rooms": default_room_rows(),
        "benches": default_bench_rows(),
        "schedule": default_schedule(),
        "historical_yields": [],
    }
    if psycopg is None or not database_url():
        return fallback
    now = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            cursor.execute("SELECT COUNT(*) FROM qcc_cultivation_cycle_programs")
            if int(cursor.fetchone()[0]) == 0:
                program = default_cycle_program()
                cursor.execute(
                    "INSERT INTO qcc_cultivation_cycle_programs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                    (program["program_id"], program["name"], program["code_prefix"], program["cadence_days"], program["rooting_days"], program["veg_days"], program["flowering_days"], program["processing_days"], program["target_future_crops"], json.dumps(program["room_rotation"]), True, "System seed", now),
                )
            cursor.execute("SELECT COUNT(*) FROM qcc_cultivation_rooms")
            if int(cursor.fetchone()[0]) == 0:
                for room in fallback["rooms"]:
                    cursor.execute(
                        "INSERT INTO qcc_cultivation_rooms (room_id,room_code,name,building,program_id,length_ft,width_ft,height_ft,overhead_type,overhead_other,fixture_count,watts_per_fixture,effective_date,retired_date,active,notes,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s)",
                        (room["room_id"], room["room_code"], room["name"], room["building"], room["program_id"], room["length_ft"], room["width_ft"], room["height_ft"], room["overhead_type"], room["overhead_other"], room["fixture_count"], room["watts_per_fixture"], True, room["notes"], "System seed", now),
                    )
                for bench in fallback["benches"]:
                    cursor.execute(
                        "INSERT INTO qcc_cultivation_benches (bench_id,room_id,bench,length_ft,width_ft,default_density,supplemental_type,supplemental_rows,watts_per_row,supplemental_watts_override,effective_date,retired_date,active,notes,display_order,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s,%s)",
                        (bench["bench_id"], bench["room_id"], bench["bench"], bench["length_ft"], bench["width_ft"], bench["default_density"], bench["supplemental_type"], bench["supplemental_rows"], bench["watts_per_row"], bench["supplemental_watts_override"], True, bench["notes"], _integer(bench["bench_id"].rsplit("-", 1)[-1]), "System seed", now),
                    )
            cursor.execute("SELECT COUNT(*) FROM qcc_cultivation_schedule")
            if int(cursor.fetchone()[0]) == 0:
                for row in fallback["schedule"]:
                    cursor.execute(
                        "INSERT INTO qcc_cultivation_schedule (schedule_id,program_id,crop,room,clone_cut_date,flower_entry_date,harvest_date,available_date,status,source,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (row["schedule_id"], row["program_id"], row["crop"], row["room"], row["clone_cut_date"], row["flower_entry_date"], row["harvest_date"], row["available_date"], row["status"], row["source"], "System seed", now),
                    )
            connection.commit()
            cursor.execute("SELECT * FROM qcc_cultivation_cycle_programs ORDER BY active DESC,name")
            programs = _fetch_dicts(cursor)
            cursor.execute("SELECT * FROM qcc_cultivation_rooms ORDER BY active DESC,name")
            rooms = _fetch_dicts(cursor)
            cursor.execute("SELECT b.*,r.name AS room_name FROM qcc_cultivation_benches b JOIN qcc_cultivation_rooms r ON r.room_id=b.room_id ORDER BY r.name,b.display_order,b.bench")
            benches = _fetch_dicts(cursor)
            cursor.execute("SELECT * FROM qcc_cultivation_schedule ORDER BY clone_cut_date,crop")
            schedule = _fetch_dicts(cursor)
            cursor.execute("SELECT * FROM qcc_cultivation_historical_yields ORDER BY harvest_date DESC,crop,strain")
            yields = _fetch_dicts(cursor)
    for program in programs:
        rotation = program.get("room_rotation") or []
        if isinstance(rotation, str):
            rotation = json.loads(rotation)
        program["room_rotation"] = list(rotation)
    for room in rooms:
        room.update(calculate_room_metrics(room.get("length_ft"), room.get("width_ft"), room.get("height_ft")))
        room["total_overhead_watts"] = calculate_lighting_total(room.get("fixture_count"), room.get("watts_per_fixture"), room.get("overhead_watts_override"))
        room["physical_canopy_sqft"] = round(sum(_number(row.get("length_ft")) * _number(row.get("width_ft")) for row in benches if row.get("room_id") == room.get("room_id") and row.get("active", True)), 1)
    for bench in benches:
        bench.update(calculate_bench_metrics(bench.get("length_ft"), bench.get("width_ft"), bench.get("default_density")))
        bench["total_supplemental_watts"] = calculate_lighting_total(bench.get("supplemental_rows"), bench.get("watts_per_row"), bench.get("supplemental_watts_override"))
    for row in schedule + yields:
        for key, value in list(row.items()):
            if isinstance(value, (date, datetime)):
                row[key] = value.isoformat()
    return {"programs": programs, "rooms": rooms, "benches": benches, "schedule": schedule, "historical_yields": yields}


def save_cycle_program(record: dict[str, Any], updated_by: str) -> str:
    program_id = str(record.get("program_id") or re.sub(r"[^a-z0-9]+", "-", str(record.get("name", "")).lower()).strip("-"))
    if not program_id or not str(record.get("name", "")).strip():
        raise ValueError("Program name is required.")
    rotation = list(record.get("room_rotation") or [])
    if not rotation:
        raise ValueError("Select at least one room for the cycle program.")
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save the cultivation registry.")
    now = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            cursor.execute("INSERT INTO qcc_cultivation_cycle_programs (program_id,name,code_prefix,cadence_days,rooting_days,veg_days,flowering_days,processing_days,target_future_crops,room_rotation,active,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT (program_id) DO UPDATE SET name=EXCLUDED.name,code_prefix=EXCLUDED.code_prefix,cadence_days=EXCLUDED.cadence_days,rooting_days=EXCLUDED.rooting_days,veg_days=EXCLUDED.veg_days,flowering_days=EXCLUDED.flowering_days,processing_days=EXCLUDED.processing_days,target_future_crops=EXCLUDED.target_future_crops,room_rotation=EXCLUDED.room_rotation,active=EXCLUDED.active,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at", (program_id,record["name"],record.get("code_prefix","F"),max(1,_integer(record.get("cadence_days"),14)),max(0,_integer(record.get("rooting_days"),21)),max(0,_integer(record.get("veg_days"),19)),max(1,_integer(record.get("flowering_days"),68)),max(0,_integer(record.get("processing_days"),30)),max(1,_integer(record.get("target_future_crops"),26)),json.dumps(rotation),bool(record.get("active",True)),updated_by,now))
        connection.commit()
    return program_id


def save_room(record: dict[str, Any], updated_by: str) -> str:
    name = " ".join(str(record.get("name", "")).split())
    room_id = str(record.get("room_id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
    if not name or not str(record.get("room_code", "")).strip():
        raise ValueError("Room code and name are required.")
    if str(record.get("overhead_type", "Other")) not in OVERHEAD_LIGHTING_TYPES:
        raise ValueError("Select HPS, LED, MH, or Other for overhead lighting.")
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save rooms.")
    now = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            cursor.execute("INSERT INTO qcc_cultivation_rooms (room_id,room_code,name,building,program_id,length_ft,width_ft,height_ft,overhead_type,overhead_other,fixture_count,watts_per_fixture,overhead_watts_override,effective_date,retired_date,active,notes,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULLIF(%s,'')::date,NULLIF(%s,'')::date,%s,%s,%s,%s) ON CONFLICT (room_id) DO UPDATE SET room_code=EXCLUDED.room_code,name=EXCLUDED.name,building=EXCLUDED.building,program_id=EXCLUDED.program_id,length_ft=EXCLUDED.length_ft,width_ft=EXCLUDED.width_ft,height_ft=EXCLUDED.height_ft,overhead_type=EXCLUDED.overhead_type,overhead_other=EXCLUDED.overhead_other,fixture_count=EXCLUDED.fixture_count,watts_per_fixture=EXCLUDED.watts_per_fixture,overhead_watts_override=EXCLUDED.overhead_watts_override,effective_date=EXCLUDED.effective_date,retired_date=EXCLUDED.retired_date,active=EXCLUDED.active,notes=EXCLUDED.notes,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at", (room_id,record["room_code"],name,record.get("building",""),record.get("program_id",DEFAULT_PROGRAM_ID),max(0,_number(record.get("length_ft"))),max(0,_number(record.get("width_ft"))),max(0,_number(record.get("height_ft"))),record.get("overhead_type","Other"),record.get("overhead_other",""),max(0,_integer(record.get("fixture_count"))),max(0,_number(record.get("watts_per_fixture"))),max(0,_number(record.get("overhead_watts_override"))),record.get("effective_date",""),record.get("retired_date",""),bool(record.get("active",True)),record.get("notes",""),updated_by,now))
        connection.commit()
    return room_id


def save_bench(record: dict[str, Any], updated_by: str) -> str:
    room_id = str(record.get("room_id", "")).strip()
    bench_name = " ".join(str(record.get("bench", "")).split())
    bench_id = str(record.get("bench_id") or f'{room_id}-{re.sub(r"[^a-z0-9]+", "-", bench_name.lower()).strip("-")}')
    if not room_id or not bench_name:
        raise ValueError("Room and bench name are required.")
    if _number(record.get("length_ft")) <= 0 or _number(record.get("width_ft")) <= 0:
        raise ValueError("Bench length and width must be greater than zero.")
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save benches.")
    now = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            cursor.execute("INSERT INTO qcc_cultivation_benches (bench_id,room_id,bench,length_ft,width_ft,default_density,supplemental_type,supplemental_rows,watts_per_row,supplemental_watts_override,effective_date,retired_date,active,notes,display_order,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULLIF(%s,'')::date,NULLIF(%s,'')::date,%s,%s,%s,%s,%s) ON CONFLICT (bench_id) DO UPDATE SET room_id=EXCLUDED.room_id,bench=EXCLUDED.bench,length_ft=EXCLUDED.length_ft,width_ft=EXCLUDED.width_ft,default_density=EXCLUDED.default_density,supplemental_type=EXCLUDED.supplemental_type,supplemental_rows=EXCLUDED.supplemental_rows,watts_per_row=EXCLUDED.watts_per_row,supplemental_watts_override=EXCLUDED.supplemental_watts_override,effective_date=EXCLUDED.effective_date,retired_date=EXCLUDED.retired_date,active=EXCLUDED.active,notes=EXCLUDED.notes,display_order=EXCLUDED.display_order,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at", (bench_id,room_id,bench_name,_number(record["length_ft"]),_number(record["width_ft"]),max(0,_number(record.get("default_density"),0.75)),record.get("supplemental_type","None"),max(0,_integer(record.get("supplemental_rows"))),max(0,_number(record.get("watts_per_row"))),max(0,_number(record.get("supplemental_watts_override"))),record.get("effective_date",""),record.get("retired_date",""),bool(record.get("active",True)),record.get("notes",""),max(0,_integer(record.get("display_order"))),updated_by,now))
        connection.commit()
    return bench_id


def save_schedule_rows(rows: list[dict[str, Any]], updated_by: str) -> int:
    conflicts = schedule_conflicts(rows)
    if conflicts:
        raise ValueError("Schedule collision: " + "; ".join(conflicts[:3]))
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save the schedule.")
    now = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            cursor.execute(
                "SELECT schedule_id,program_id,crop,room,clone_cut_date,"
                "flower_entry_date,harvest_date,available_date,status,source "
                "FROM qcc_cultivation_schedule"
            )
            existing = _fetch_dicts(cursor)
            replacing = {str(row.get("schedule_id", "")) for row in rows}
            combined = [row for row in existing if str(row.get("schedule_id", "")) not in replacing]
            combined.extend(rows)
            combined_conflicts = schedule_conflicts(combined)
            if combined_conflicts:
                raise ValueError(
                    "Schedule collision with a saved crop: "
                    + "; ".join(combined_conflicts[:3])
                )
            for row in rows:
                cursor.execute("INSERT INTO qcc_cultivation_schedule (schedule_id,program_id,crop,room,clone_cut_date,flower_entry_date,harvest_date,available_date,status,source,notes,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (schedule_id) DO UPDATE SET program_id=EXCLUDED.program_id,crop=EXCLUDED.crop,room=EXCLUDED.room,clone_cut_date=EXCLUDED.clone_cut_date,flower_entry_date=EXCLUDED.flower_entry_date,harvest_date=EXCLUDED.harvest_date,available_date=EXCLUDED.available_date,status=EXCLUDED.status,source=EXCLUDED.source,notes=EXCLUDED.notes,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at", (row["schedule_id"],row["program_id"],row["crop"],row["room"],row["clone_cut_date"],row["flower_entry_date"],row["harvest_date"],row["available_date"],row.get("status","Upcoming"),row.get("source","Generated"),row.get("notes",""),updated_by,now))
        connection.commit()
    return len(rows)


def set_current_schedule(schedule_id: str, updated_by: str) -> None:
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to select the current crop.")
    now = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            cursor.execute("SELECT program_id FROM qcc_cultivation_schedule WHERE schedule_id=%s", (schedule_id,))
            found = cursor.fetchone()
            if not found:
                raise ValueError("The selected schedule record no longer exists.")
            cursor.execute("UPDATE qcc_cultivation_schedule SET status=CASE WHEN status='Planning' THEN 'Upcoming' ELSE status END,updated_by=%s,updated_at=%s WHERE program_id=%s", (updated_by,now,found[0]))
            cursor.execute("UPDATE qcc_cultivation_schedule SET status='Planning',updated_by=%s,updated_at=%s WHERE schedule_id=%s", (updated_by,now,schedule_id))
        connection.commit()


def save_historical_yield(record: dict[str, Any], updated_by: str) -> str:
    crop = str(record.get("crop", "")).strip()
    room = str(record.get("room", "")).strip()
    harvest_date = str(record.get("harvest_date", "")).strip()
    if not crop or not room or not harvest_date:
        raise ValueError("Crop, room, and harvest date are required.")
    strain = " ".join(str(record.get("strain", "")).split())
    harvest_id = str(record.get("harvest_id") or "QCC-HY-" + re.sub(r"[^A-Za-z0-9]+", "-", f"{crop}-{strain or 'ROOM'}").strip("-").upper())
    if psycopg is None or not database_url():
        raise RuntimeError("A live Supabase connection is required to save historical yields.")
    now = datetime.now().astimezone().isoformat()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            cursor.execute("INSERT INTO qcc_cultivation_historical_yields (harvest_id,crop,room,strain,harvest_date,physical_canopy_sqft,planted_canopy_sqft,planted_plants,planned_ff_plants,actual_ff_plants,actual_ff_canopy_sqft,wet_yield_lbs,dry_flower_lbs,ab_flower_lbs,c_flower_lbs,trim_lbs,quality_score,data_source,notes,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (harvest_id) DO UPDATE SET crop=EXCLUDED.crop,room=EXCLUDED.room,strain=EXCLUDED.strain,harvest_date=EXCLUDED.harvest_date,physical_canopy_sqft=EXCLUDED.physical_canopy_sqft,planted_canopy_sqft=EXCLUDED.planted_canopy_sqft,planted_plants=EXCLUDED.planted_plants,planned_ff_plants=EXCLUDED.planned_ff_plants,actual_ff_plants=EXCLUDED.actual_ff_plants,actual_ff_canopy_sqft=EXCLUDED.actual_ff_canopy_sqft,wet_yield_lbs=EXCLUDED.wet_yield_lbs,dry_flower_lbs=EXCLUDED.dry_flower_lbs,ab_flower_lbs=EXCLUDED.ab_flower_lbs,c_flower_lbs=EXCLUDED.c_flower_lbs,trim_lbs=EXCLUDED.trim_lbs,quality_score=EXCLUDED.quality_score,data_source=EXCLUDED.data_source,notes=EXCLUDED.notes,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at", (harvest_id,crop,room,strain,harvest_date,max(0,_number(record.get("physical_canopy_sqft"))),max(0,_number(record.get("planted_canopy_sqft"))),max(0,_integer(record.get("planted_plants"))),max(0,_integer(record.get("planned_ff_plants"))),max(0,_integer(record.get("actual_ff_plants"))),max(0,_number(record.get("actual_ff_canopy_sqft"))),max(0,_number(record.get("wet_yield_lbs"))),max(0,_number(record.get("dry_flower_lbs"))),max(0,_number(record.get("ab_flower_lbs"))),max(0,_number(record.get("c_flower_lbs"))),max(0,_number(record.get("trim_lbs"))),max(0,_number(record.get("quality_score"))),record.get("data_source","Manual"),record.get("notes",""),updated_by,now))
        connection.commit()
    return harvest_id
