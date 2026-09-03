"""Cultivation planning calculations shared by the Reflex workspace and tests."""

from __future__ import annotations

from datetime import date, timedelta
from math import ceil, floor
import re
from typing import Any, TypedDict


PLANTS_PER_SQUARE_FOOT = 0.75
ROOTING_DAYS = 21
VEG_DAYS = 19
CLONE_TO_FLOWER_DAYS = ROOTING_DAYS + VEG_DAYS
CLONES_PER_TRAY = 32
STANDARD_FLOWER_DAYS = 68
DEFAULT_POST_HARVEST_DAYS = 30
SCHEDULED_SUPPLY_EXPIRY_DAYS = 45

CLADE9_CLONE_STRAINS = (
    "Blue Dream", "Brooklyn Runtz", "Diamond Bar", "Diamond Dust",
    "Fig Bar", "G13", "J1", "LA Piff", "Lemon Cherry Gelato",
    "Lipsmackerz", "Orange Push Pop", "Pine Tar", "Private Reserve",
    "Razberry Runtz", "South Central Purps", "Tahoe OG",
)

CRAFT_KINGS_CLONE_STRAINS = (
    "Candy Cut", "Golden Goat", "Sour Chem",
)


class RoomBench(TypedDict):
    bench: str
    length: float
    width: float


class BenchPlan(TypedDict):
    bench: str
    length: float
    width: float
    square_feet: float
    target_plants: int
    strain_count: int
    strain_1: str
    percent_1: float
    strain_2: str
    percent_2: float
    strain_3: str
    percent_3: float
    accent: str
    tint: str


class ScheduledCropAllocation(TypedDict):
    crop: str
    room: str
    harvest_date: str
    strain: str
    square_feet: float


class ScheduledSupplyReconciliation(TypedDict):
    gross_projected_lbs: float
    fresh_frozen_plants: int
    planted_plants: int
    fresh_frozen_percent: float
    fresh_frozen_reduction_lbs: float
    creative_use_reduction_lbs: float
    net_projected_lbs: float
    actual_processed_lbs: float
    unconfirmed_remainder_lbs: float
    forecast_counted_lbs: float
    expired: bool
    actual_detected: bool
    status: str


# Confirmed physical bench layouts. These are defaults rather than hard-coded
# UI geometry so an authorized user can edit a future room modification.
ROOM_LAYOUTS: dict[str, tuple[RoomBench, ...]] = {
    "Flower Room 1": (
        {"bench": "Bench 1", "length": 37.0, "width": 5.0},
        {"bench": "Bench 2", "length": 37.0, "width": 5.0},
        {"bench": "Bench 3A", "length": 16.0, "width": 5.0},
        {"bench": "Bench 3B", "length": 16.0, "width": 5.0},
        {"bench": "Bench 4A", "length": 16.5, "width": 5.0},
        {"bench": "Bench 4B", "length": 16.5, "width": 5.0},
        {"bench": "Bench 5", "length": 37.0, "width": 5.0},
        {"bench": "Bench 6", "length": 37.0, "width": 5.0},
        {"bench": "Bench 7", "length": 37.0, "width": 5.0},
    ),
    "Flower Room 2": (
        {"bench": "Bench 1", "length": 37.0, "width": 5.0},
        {"bench": "Bench 2", "length": 37.0, "width": 5.0},
        {"bench": "Bench 3", "length": 37.0, "width": 5.0},
        {"bench": "Bench 4A", "length": 16.5, "width": 5.0},
        {"bench": "Bench 4B", "length": 16.5, "width": 5.0},
        {"bench": "Bench 5", "length": 37.0, "width": 5.0},
        {"bench": "Bench 6", "length": 37.0, "width": 5.0},
        {"bench": "Bench 7", "length": 37.0, "width": 5.0},
    ),
    "Flower Room 3": (
        {"bench": "Bench 1", "length": 37.0, "width": 5.0},
        {"bench": "Bench 2", "length": 37.0, "width": 5.0},
        {"bench": "Bench 3", "length": 37.0, "width": 4.0},
        {"bench": "Bench 4A", "length": 21.0, "width": 5.0},
        {"bench": "Bench 4B", "length": 12.0, "width": 5.0},
        {"bench": "Bench 5", "length": 37.0, "width": 5.0},
        {"bench": "Bench 6", "length": 37.0, "width": 5.0},
        {"bench": "Bench 7", "length": 37.0, "width": 5.0},
    ),
    "Flower Room 4": (
        {"bench": "Bench 1", "length": 37.0, "width": 5.0},
        {"bench": "Bench 2", "length": 37.0, "width": 5.0},
        {"bench": "Bench 3", "length": 37.0, "width": 5.0},
        {"bench": "Bench 4A", "length": 16.0, "width": 5.0},
        {"bench": "Bench 4B", "length": 12.0, "width": 5.0},
        {"bench": "Bench 5", "length": 37.0, "width": 5.0},
        {"bench": "Bench 6", "length": 37.0, "width": 5.0},
        {"bench": "Bench 7", "length": 37.0, "width": 5.0},
    ),
    "Flower Room 5": (
        {"bench": "Bench 1", "length": 37.0, "width": 5.0},
        {"bench": "Bench 2", "length": 37.0, "width": 5.0},
        {"bench": "Bench 3", "length": 37.0, "width": 5.0},
        {"bench": "Bench 4", "length": 37.0, "width": 5.0},
        {"bench": "Bench 5", "length": 37.0, "width": 5.0},
        {"bench": "Bench 6", "length": 32.0, "width": 5.0},
    ),
}


# Historical averages reviewed from NJ Historical Yield Analysis. The blend of
# room and strain performance avoids overreacting to a single crop while still
# preserving meaningful cultivar differences.
ROOM_YIELD_G_PER_SQFT: dict[str, float] = {
    "Flower Room 1": 96.7,
    "Flower Room 2": 84.8,
    "Flower Room 3": 78.8,
    "Flower Room 4": 109.9,
    "Flower Room 5": 103.3,
}

STRAIN_YIELD_G_PER_SQFT: dict[str, float] = {
    "g13": 102.2,
    "pine tar": 101.6,
    "j1": 97.6,
    "blue dream": 94.9,
    "diamond bar": 94.7,
    "razberry runtz": 87.6,
    "lemon cherry gelato": 87.2,
    "la piff": 87.2,
    "diamond dust": 84.5,
    "fig bar": 82.9,
    "orange push pop": 82.5,
    "lipsmackerz": 82.0,
    "tahoe og": 74.5,
    "figueroa og": 72.5,
    "private reserve": 65.5,
    "brooklyn runtz": 60.3,
}

# Temporary planning rates for cultivars that do not yet have enough completed
# harvest history for the standard 70% strain / 30% room blend. These values
# are used directly and should be retired when an evidence-based benchmark is
# available.
PROVISIONAL_STRAIN_YIELD_G_PER_SQFT: dict[str, float] = {
    "south central purps": 85.0,
}

STRAIN_ALIASES: dict[str, str] = {
    "lip smackers": "lipsmackerz",
    "lip smackerz": "lipsmackerz",
    "lipsmackers": "lipsmackerz",
    "private reserve og": "private reserve",
    "razruntz": "razberry runtz",
    "razberry runtz (rpg 103)": "razberry runtz",
    "gelato cherry lemon": "hood candy",
    "gcl": "hood candy",
}


# Known rooms that were still flowering when this cultivation branch was
# created. Completed rooms fall out automatically by harvest date. Past rooms
# remain represented by actual WIP/inventory and are therefore not double
# counted here.
UPCOMING_CROP_ALLOCATIONS: tuple[ScheduledCropAllocation, ...] = (
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "Razberry Runtz", "square_feet": 185.0},
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "Lipsmackerz", "square_feet": 185.0},
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "South Central Purps", "square_feet": 160.0},
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "Lemon Cherry Gelato", "square_feet": 80.0},
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "Jelly Cake", "square_feet": 80.0},
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "J1", "square_feet": 185.0},
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "Orange Push Pop", "square_feet": 185.0},
    {"crop": "F1.9", "room": "Flower Room 1", "harvest_date": "2026-08-10", "strain": "Diamond Bar", "square_feet": 185.0},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "Fig Bar", "square_feet": 185.0},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "Pine Tar", "square_feet": 185.0},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "LA Piff", "square_feet": 185.0},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "Jelly Cake", "square_feet": 82.5},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "Lemon Cherry Gelato", "square_feet": 185.0},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "Hood Candy", "square_feet": 82.5},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "Lipsmackerz", "square_feet": 92.5},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "South Central Purps", "square_feet": 92.5},
    {"crop": "F2.9", "room": "Flower Room 2", "harvest_date": "2026-08-24", "strain": "Diamond Dust", "square_feet": 185.0},
    {"crop": "F3.9", "room": "Flower Room 3", "harvest_date": "2026-09-07", "strain": "Razberry Runtz", "square_feet": 185.0},
    {"crop": "F3.9", "room": "Flower Room 3", "harvest_date": "2026-09-07", "strain": "LA Piff", "square_feet": 185.0},
    {"crop": "F3.9", "room": "Flower Room 3", "harvest_date": "2026-09-07", "strain": "Orange Push Pop", "square_feet": 148.0},
    {"crop": "F3.9", "room": "Flower Room 3", "harvest_date": "2026-09-07", "strain": "J1", "square_feet": 165.0},
    {"crop": "F3.9", "room": "Flower Room 3", "harvest_date": "2026-09-07", "strain": "Diamond Bar", "square_feet": 185.0},
    {"crop": "F3.9", "room": "Flower Room 3", "harvest_date": "2026-09-07", "strain": "G13", "square_feet": 185.0},
    {"crop": "F3.9", "room": "Flower Room 3", "harvest_date": "2026-09-07", "strain": "Private Reserve", "square_feet": 185.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "Fig Bar", "square_feet": 185.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "Lemon Cherry Gelato", "square_feet": 185.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "Orange Push Pop", "square_feet": 185.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "Jelly Cake", "square_feet": 80.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "Hood Candy", "square_feet": 60.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "Pine Tar", "square_feet": 185.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "South Central Purps", "square_feet": 185.0},
    {"crop": "F4.9", "room": "Flower Room 4", "harvest_date": "2026-09-21", "strain": "Diamond Dust", "square_feet": 185.0},
    {"crop": "F5.9", "room": "Flower Room 5", "harvest_date": "2026-10-05", "strain": "Tahoe OG", "square_feet": 185.0},
    {"crop": "F5.9", "room": "Flower Room 5", "harvest_date": "2026-10-05", "strain": "Diamond Bar", "square_feet": 185.0},
    {"crop": "F5.9", "room": "Flower Room 5", "harvest_date": "2026-10-05", "strain": "J1", "square_feet": 185.0},
    {"crop": "F5.9", "room": "Flower Room 5", "harvest_date": "2026-10-05", "strain": "LA Piff", "square_feet": 185.0},
    {"crop": "F5.9", "room": "Flower Room 5", "harvest_date": "2026-10-05", "strain": "G13", "square_feet": 95.0},
    {"crop": "F5.9", "room": "Flower Room 5", "harvest_date": "2026-10-05", "strain": "Razberry Runtz", "square_feet": 160.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Private Reserve", "square_feet": 185.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Fig Bar", "square_feet": 185.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Lemon Cherry Gelato", "square_feet": 165.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Hood Candy", "square_feet": 80.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Jelly Cake", "square_feet": 80.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Pine Tar", "square_feet": 185.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Diamond Dust", "square_feet": 185.0},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "Tahoe OG", "square_feet": 92.5},
    {"crop": "F1.10", "room": "Flower Room 1", "harvest_date": "2026-10-19", "strain": "TPR #16", "square_feet": 92.5},
)


# Allocation-only history transcribed from ``Clone Planner new Notes.xlsx``.
# Crop reports remain authoritative when they exist; these rows fill the gaps
# needed for the four- and eight-crop lookback and future supply audit.
HISTORICAL_CLONE_ALLOCATIONS: dict[str, dict[str, float]] = {
    "F2.9": {
        "Fig Bar": 1.0, "Lemon Cherry Gelato": 1.0, "Diamond Dust": 1.0,
        "LA Piff": 1.0, "Pine Tar": 1.0, "Razberry Runtz": 1.0,
        "Lipsmackerz": 0.5, "Hood Candy": 0.5, "Jelly Cake": 0.5,
        "South Central Purps": 0.5,
    },
    "F3.9": {
        "Orange Push Pop": 1.0, "Diamond Bar": 1.0, "J1": 1.0,
        "LA Piff": 1.0, "Private Reserve": 1.0, "G13": 1.0,
        "Razberry Runtz": 1.0,
    },
    "F4.9": {
        "Orange Push Pop": 1.0, "Fig Bar": 1.0,
        "Lemon Cherry Gelato": 1.0, "Diamond Dust": 1.0,
        "Pine Tar": 1.0, "Hood Candy": 0.5, "Jelly Cake": 0.5,
        "South Central Purps": 1.0,
    },
    "F5.9": {
        "Diamond Bar": 1.0, "J1": 1.0, "LA Piff": 1.0,
        "Tahoe OG": 1.0, "G13": 0.5, "Razberry Runtz": 1.0,
        "EZ Gas OG (TPR 14)": 0.5,
    },
    "F1.10": {
        "Fig Bar": 1.0, "Lemon Cherry Gelato": 1.0,
        "Diamond Dust": 1.0, "Private Reserve": 1.0,
        "Tahoe OG": 0.5, "Pine Tar": 1.0, "Hood Candy": 0.5,
        "Jelly Cake": 0.5, "Shoreline OG (TPR 16)": 0.5,
    },
    "F2.10": {
        "Orange Push Pop": 1.0, "Diamond Bar": 1.0, "G13": 1.0,
        "Razberry Runtz": 1.0, "Lipsmackerz": 1.0,
        "EZ Gas OG (TPR 14)": 0.5, "Shoreline OG (TPR 16)": 0.5,
    },
    "F3.10": {
        "Diamond Bar": 1.0, "Fig Bar": 1.0,
        "Lemon Cherry Gelato": 1.0, "Diamond Dust": 1.0,
        "LA Piff": 1.0, "Pine Tar": 1.0, "Jelly Cake": 0.5,
        "South Central Purps": 0.5,
    },
    "F4.10": {
        "Orange Push Pop": 1.0, "Diamond Bar": 1.0, "J1": 1.0,
        "Private Reserve": 1.0, "Tahoe OG": 1.0,
        "Lipsmackerz": 1.0, "Hood Candy": 0.5, "Jelly Cake": 0.5,
    },
}


class CloneRecommendation(TypedDict):
    target_plants: int
    requested_overage_percent: int
    trays: int
    recommended_clones: int
    actual_overage_percent: float


class CultivationTimeline(TypedDict):
    clone_cut_date: str
    veg_transfer_date: str
    flower_entry_date: str


class ClonePlanningPeriod(TypedDict):
    crop: str
    room: str
    clone_cut_date: str
    flower_entry_date: str
    harvest_date: str
    available_date: str


CLONE_PLANNING_FIRST_CROP = "F5.10"
CLONE_PLANNING_FIRST_CUT_DATE = date(2026, 8, 28)


def bench_plant_capacity(
    square_feet: float,
    plants_per_square_foot: float = PLANTS_PER_SQUARE_FOOT,
) -> int:
    """Return the flower population for a bench at the selected density.

    QCC's confirmed default remains 0.75 plants per square foot. Conventional
    half-up rounding avoids Python's banker rounding for half-plant results.
    """
    density = max(0.0, float(plants_per_square_foot))
    return max(0, floor((float(square_feet) * density) + 0.5))


def recommend_clone_trays(
    target_plants: int,
    requested_overage_percent: int = 30,
    tray_size: int = CLONES_PER_TRAY,
) -> CloneRecommendation:
    """Round a clone requirement to the nearest practical full tray count."""
    target = max(0, int(target_plants))
    overage = min(30, max(25, int(requested_overage_percent)))
    if target == 0:
        return {
            "target_plants": 0,
            "requested_overage_percent": overage,
            "trays": 0,
            "recommended_clones": 0,
            "actual_overage_percent": 0.0,
        }

    ideal = target * (1 + (overage / 100))
    lower_trays = max(1, floor(ideal / tray_size))
    upper_trays = max(1, ceil(ideal / tray_size))
    candidates = sorted({lower_trays, upper_trays})
    # Never recommend fewer clones than the target flower population. On an
    # exact tie, select the larger safety allocation.
    candidates = [count for count in candidates if count * tray_size >= target]
    trays = min(
        candidates,
        key=lambda count: (abs((count * tray_size) - ideal), -count),
    )
    clones = trays * tray_size
    actual_overage = ((clones - target) / target) * 100
    return {
        "target_plants": target,
        "requested_overage_percent": overage,
        "trays": trays,
        "recommended_clones": clones,
        "actual_overage_percent": round(actual_overage, 1),
    }


def cultivation_timeline(flower_entry_date: str) -> CultivationTimeline:
    """Calculate the clone cut and Veg transfer dates for a flower entry."""
    entry = date.fromisoformat(flower_entry_date)
    return {
        "clone_cut_date": (entry - timedelta(days=CLONE_TO_FLOWER_DAYS)).isoformat(),
        "veg_transfer_date": (entry - timedelta(days=VEG_DAYS)).isoformat(),
        "flower_entry_date": entry.isoformat(),
    }


def room_bench_plans(
    room: str,
    plants_per_square_foot: float = PLANTS_PER_SQUARE_FOOT,
) -> list[BenchPlan]:
    """Return a fresh editable planning row for every bench in a room."""
    benches = ROOM_LAYOUTS.get(room, ROOM_LAYOUTS["Flower Room 1"])
    palette = (
        ("#0f766e", "#f0fdfa"),
        ("#2563eb", "#eff6ff"),
        ("#7c3aed", "#f5f3ff"),
        ("#ea580c", "#fff7ed"),
        ("#db2777", "#fdf2f8"),
        ("#0891b2", "#ecfeff"),
        ("#65a30d", "#f7fee7"),
        ("#ca8a04", "#fefce8"),
        ("#4f46e5", "#eef2ff"),
    )
    return [
        {
            "bench": bench["bench"],
            "length": float(bench["length"]),
            "width": float(bench["width"]),
            "square_feet": round(float(bench["length"]) * float(bench["width"]), 1),
            "target_plants": bench_plant_capacity(
                float(bench["length"]) * float(bench["width"]),
                plants_per_square_foot,
            ),
            "strain_count": 1,
            "strain_1": "",
            "percent_1": 100.0,
            "strain_2": "",
            "percent_2": 0.0,
            "strain_3": "",
            "percent_3": 0.0,
            "accent": palette[position % len(palette)][0],
            "tint": palette[position % len(palette)][1],
        }
        for position, bench in enumerate(benches)
    ]


def default_split_percentages(strain_count: int) -> tuple[float, float, float]:
    """Return simple whole-percent defaults that always total 100%."""
    count = min(3, max(1, int(strain_count)))
    if count == 1:
        return (100.0, 0.0, 0.0)
    if count == 2:
        return (50.0, 50.0, 0.0)
    return (34.0, 33.0, 33.0)


def normalized_strain(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return STRAIN_ALIASES.get(name, name)


def exact_bench_allocations(
    bench_plans: list[dict[str, Any]],
) -> dict[str, float]:
    """Convert a finalized physical room map into bench equivalents by strain."""
    square_feet_by_strain: dict[str, float] = {}
    labels: dict[str, str] = {}
    for bench in bench_plans:
        square_feet = max(0.0, float(bench.get("square_feet", 0) or 0))
        strain_count = min(3, max(1, int(bench.get("strain_count", 1) or 1)))
        for index in range(1, strain_count + 1):
            strain = " ".join(
                str(bench.get(f"strain_{index}", "") or "").strip().split()
            )
            percent = max(
                0.0, min(100.0, float(bench.get(f"percent_{index}", 0) or 0))
            )
            key = normalized_strain(strain)
            if not key or percent <= 0:
                continue
            labels.setdefault(key, strain)
            square_feet_by_strain[key] = (
                square_feet_by_strain.get(key, 0.0)
                + square_feet * percent / 100
            )
    return {
        labels[key]: round(square_feet / 185.0, 2)
        for key, square_feet in square_feet_by_strain.items()
        if square_feet > 0
    }


def estimated_yield_g_per_sqft(strain: str, room: str) -> float:
    """Blend cultivar history (70%) with the selected room history (30%)."""
    provisional_rate = PROVISIONAL_STRAIN_YIELD_G_PER_SQFT.get(
        normalized_strain(strain)
    )
    if provisional_rate is not None:
        return round(provisional_rate, 1)
    room_rate = ROOM_YIELD_G_PER_SQFT.get(room, 90.0)
    strain_rate = STRAIN_YIELD_G_PER_SQFT.get(normalized_strain(strain))
    if strain_rate is None:
        return round(room_rate, 1)
    return round((strain_rate * 0.70) + (room_rate * 0.30), 1)


def estimated_yield_pounds(square_feet: float, strain: str, room: str) -> float:
    grams = max(0.0, float(square_feet)) * estimated_yield_g_per_sqft(strain, room)
    return round(grams / 453.59237, 1)


def inventory_counts_as_current_cultivation_supply(row: dict[str, Any]) -> bool:
    """Return whether a package contributes to current net-flower supply."""
    return bool(cultivation_flower_supply_bucket(row))


def cultivation_flower_supply_bucket(
    row: dict[str, Any], *, include_pre_wip: bool = False
) -> str:
    """Classify net-flower supply using Control Tower production stages.

    Packaged flower and flower-equivalent pre-rolls remain CPG. Cultivation
    WIP must use the authoritative ``WIP-Cultivation`` stage and have passed
    testing. ``Pre-WIP-Cultivation`` is separate and is returned only when
    explicitly requested. Purchased Building 1A supply has its own stages and
    is intentionally excluded. Trim, shake, retention, samples, manufacturing
    inputs, and partner-owned material never contribute to Clone Allocation.
    """
    stage = str(row.get("Production Stage", "") or "").strip().casefold()
    location = str(row.get("Location", "") or "").strip().casefold()
    item = str(row.get("Item", "") or row.get("Product", "") or "").strip().casefold()
    package_type = str(row.get("Package Type", "") or "").strip().casefold()
    sku_type = str(row.get("SKU Type", "") or "").strip().casefold()
    category = str(row.get("Category", "") or "").strip().casefold()
    material_type = str(row.get("Material Type", "") or "").strip().casefold()
    qa_status = str(row.get("QA Status", "") or "").strip().casefold()
    license_type = str(row.get("License", "") or "").strip().casefold()
    ownership = str(row.get("Ownership Status", "") or "").strip().casefold()
    excluded = " ".join((stage, location, item, package_type, sku_type))
    if "retention" in excluded or "sample" in excluded:
        return ""
    byproduct_text = " ".join((stage, item, category, material_type))
    if "trim" in byproduct_text or "shake" in byproduct_text:
        return ""
    if not ("bud/flower" in category or "raw pre-roll" in category):
        return ""
    if ownership and not ownership.startswith("qcc-owned"):
        return ""
    if stage == "packaged goods":
        if "quarantine" in location and qa_status != "test passed":
            return ""
        return "CPG"
    if "cultivation" not in license_type:
        return ""
    if stage == "wip-cultivation" and qa_status == "test passed":
        return "WIP-Cultivation"
    if include_pre_wip and stage == "pre-wip-cultivation":
        return "Pre-WIP-Cultivation"
    return ""


def clone_planning_periods(
    count: int = 10,
    first_crop: str = CLONE_PLANNING_FIRST_CROP,
    first_cut_date: date = CLONE_PLANNING_FIRST_CUT_DATE,
    post_harvest_days: int = DEFAULT_POST_HARVEST_DAYS,
) -> list[ClonePlanningPeriod]:
    """Build QCC's rolling two-week, five-room clone-planning schedule."""
    match = re.fullmatch(r"F([1-5])[.](\d+)", str(first_crop).strip())
    if not match:
        raise ValueError("Clone-planning crops must use the F1.10 format.")
    room_number = int(match.group(1))
    crop_number = int(match.group(2))
    periods: list[ClonePlanningPeriod] = []
    for position in range(max(1, int(count))):
        offset = position * 14
        cut = first_cut_date + timedelta(days=offset)
        entry = cut + timedelta(days=CLONE_TO_FLOWER_DAYS)
        harvest = entry + timedelta(days=STANDARD_FLOWER_DAYS)
        available = harvest + timedelta(days=max(0, int(post_harvest_days)))
        periods.append({
            "crop": f"F{room_number}.{crop_number}",
            "room": f"Flower Room {room_number}",
            "clone_cut_date": cut.isoformat(),
            "flower_entry_date": entry.isoformat(),
            "harvest_date": harvest.isoformat(),
            "available_date": available.isoformat(),
        })
        room_number += 1
        if room_number > 5:
            room_number = 1
            crop_number += 1
    return periods


def prior_clone_planning_periods(
    count: int = 8,
    first_crop: str = CLONE_PLANNING_FIRST_CROP,
    first_cut_date: date = CLONE_PLANNING_FIRST_CUT_DATE,
    post_harvest_days: int = DEFAULT_POST_HARVEST_DAYS,
) -> list[ClonePlanningPeriod]:
    """Build the clone-plan periods immediately before the active crop.

    Results are newest first so F4.10, F3.10, F2.10, and F1.10 are the
    first four historical choices when F5.10 is active.
    """
    match = re.fullmatch(r"F([1-5])[.](\d+)", str(first_crop).strip())
    if not match:
        raise ValueError("Clone-planning crops must use the F1.10 format.")
    room_number = int(match.group(1))
    crop_number = int(match.group(2))
    periods: list[ClonePlanningPeriod] = []
    for position in range(1, max(1, int(count)) + 1):
        room_number -= 1
        if room_number < 1:
            room_number = 5
            crop_number -= 1
        cut = first_cut_date - timedelta(days=position * 14)
        entry = cut + timedelta(days=CLONE_TO_FLOWER_DAYS)
        harvest = entry + timedelta(days=STANDARD_FLOWER_DAYS)
        available = harvest + timedelta(days=max(0, int(post_harvest_days)))
        periods.append({
            "crop": f"F{room_number}.{crop_number}",
            "room": f"Flower Room {room_number}",
            "clone_cut_date": cut.isoformat(),
            "flower_entry_date": entry.isoformat(),
            "harvest_date": harvest.isoformat(),
            "available_date": available.isoformat(),
        })
    return periods


def cultivation_crop_harvest_dates() -> dict[str, str]:
    """Return the crop-report/calendar harvest schedule used by plant views.

    Crop-report dates take precedence where detailed reports have already been
    bundled.  The established two-week cultivation-calendar cadence fills the
    nearby historical and future crops so active Metrc plants can be assigned a
    harvest date without relying on Metrc's Hold field.
    """
    schedule = {
        str(row.get("crop", "")): str(row.get("harvest_date", ""))
        for row in [
            *prior_clone_planning_periods(20),
            *clone_planning_periods(20),
        ]
        if row.get("crop") and row.get("harvest_date")
    }
    for row in UPCOMING_CROP_ALLOCATIONS:
        crop = str(row.get("crop", ""))
        harvest_date = str(row.get("harvest_date", ""))
        if crop and harvest_date:
            schedule[crop] = harvest_date
    return schedule


def active_plant_harvest_date(row: dict[str, Any]) -> str:
    """Find an active plant's harvest date from its crop or flower room.

    Exact crop codes embedded in the plant batch/location win.  When Metrc does
    not carry a crop code, a flowering plant is matched to the room calendar by
    choosing the scheduled harvest closest to its phase-date + standard flower
    time.  Vegetative and propagation records without an assigned crop remain
    explicitly unscheduled.
    """
    schedule = cultivation_crop_harvest_dates()
    descriptors = " ".join(
        str(row.get(field, "") or "")
        for field in ("plant_batch", "location", "sublocation")
    )
    crop_match = re.search(r"\bF([1-5])[.](\d+)\b", descriptors, re.I)
    if crop_match:
        return schedule.get(
            f"F{crop_match.group(1)}.{crop_match.group(2)}", "Not Scheduled"
        )
    if str(row.get("phase", "")).casefold() != "flowering":
        return "Not Scheduled"
    # The QCC cultivation calendar covers the Main Cultivation F1-F5 rooms.
    # 1A has its own room numbering and must not inherit an unrelated F-room
    # date merely because, for example, its location is named 1A Flower-2.
    if str(row.get("facility", "")) == "1A Building":
        return "Not Scheduled"
    location = str(row.get("location", "") or "")
    # Main locations such as ``Cultivation 5 - F4`` use the trailing F-number
    # as the flower-room schedule.  The leading cultivation-area number is not
    # the room number, so it must never win that match.
    room_match = re.search(r"(?:^|[-\s])F([1-5])\b", location, re.I)
    if not room_match:
        room_match = re.search(r"flower\s*room\s*[-#:]?\s*([1-5])\b", location, re.I)
    phase_date = str(row.get("phase_date", "") or "")
    if not room_match or not phase_date:
        return "Not Scheduled"
    try:
        expected = date.fromisoformat(phase_date) + timedelta(days=STANDARD_FLOWER_DAYS)
    except ValueError:
        return "Not Scheduled"
    room_number = int(room_match.group(1))
    candidates: list[tuple[int, str]] = []
    for crop, harvest_date in schedule.items():
        if not crop.startswith(f"F{room_number}."):
            continue
        try:
            scheduled = date.fromisoformat(harvest_date)
        except ValueError:
            continue
        candidates.append((abs((scheduled - expected).days), harvest_date))
    if not candidates:
        return "Not Scheduled"
    distance, harvest_date = min(candidates)
    return harvest_date if distance <= 21 else "Not Scheduled"


def clone_plan_edit_window(
    clone_cut_date: str,
    today: date | None = None,
) -> tuple[date, date]:
    """Return the seven-day edit window beginning on the clone-cut date."""
    start = date.fromisoformat(clone_cut_date)
    return start, start + timedelta(days=6)


def clone_plan_is_editable(
    clone_cut_date: str,
    today: date | None = None,
    override: bool = False,
) -> bool:
    if override:
        return True
    current = today or date.today()
    _, end = clone_plan_edit_window(clone_cut_date, current)
    # The active allocation may be prepared before its cut date. It locks at
    # the end of the seven-day clone-cut week unless an admin overrides it.
    return current <= end


def valid_bench_equivalent(value: Any) -> float:
    """Validate a clone allocation expressed as 0.1–7.0 bench equivalents."""
    try:
        parsed = round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0
    if parsed == 0:
        return 0.0
    if parsed < 0.1 or parsed > 7.0:
        raise ValueError("Bench allocation must be between 0.1 and 7.0.")
    return parsed


def approved_clone_plan_for_crop(
    plans: list[dict[str, Any]], crop: str
) -> dict[str, Any] | None:
    """Return the newest approved saved plan for a crop, if one exists."""
    crop_key = str(crop or "").strip().casefold()
    return next(
        (
            dict(plan)
            for plan in plans
            if str(plan.get("crop", "") or "").strip().casefold() == crop_key
            and str(plan.get("status", "") or "").strip().casefold() == "approved"
        ),
        None,
    )


def forecast_two_week_balances(
    current_lbs: float,
    weekly_demand_lbs: float,
    scheduled_by_period: list[float],
) -> list[float]:
    """Apply Current + Scheduled - demand, flooring physical inventory at zero."""
    balance = max(0.0, float(current_lbs))
    results: list[float] = []
    for scheduled in scheduled_by_period:
        balance = max(
            0.0,
            balance + float(scheduled) - (2 * float(weekly_demand_lbs)),
        )
        results.append(round(balance, 1))
    return results


def crop_is_scheduled_supply(
    harvest_date: date,
    today: date,
    plan_available_date: date,
    post_harvest_days: int = DEFAULT_POST_HARVEST_DAYS,
    expiry_days: int = SCHEDULED_SUPPLY_EXPIRY_DAYS,
) -> bool:
    """Keep a projection visible until its 45-day reconciliation deadline."""
    crop_available = harvest_date + timedelta(days=max(0, int(post_harvest_days)))
    crop_expires = harvest_date + timedelta(
        days=max(int(post_harvest_days), int(expiry_days))
    )
    return today <= crop_expires and crop_available <= plan_available_date


def scheduled_supply_reconciliation(
    gross_projected_lbs: float,
    planted_plants: int,
    fresh_frozen_plants: int,
    actual_processed_lbs: float,
    harvest_date: date,
    today: date,
    expiry_days: int = SCHEDULED_SUPPLY_EXPIRY_DAYS,
    creative_use_reduction_lbs: float = 0.0,
) -> ScheduledSupplyReconciliation:
    """Reconcile projected dry flower without counting uncertain remainder.

    Fresh Frozen is entered as plants and translated through the crop's exact
    planted population. Once any crop-matched actual inventory appears, that
    actual inventory is authoritative: the expected remainder remains visible
    for investigation but contributes zero usable forecast pounds.
    """
    gross = max(0.0, float(gross_projected_lbs or 0))
    planted = max(0, int(planted_plants or 0))
    frozen = max(0, min(int(fresh_frozen_plants or 0), planted))
    frozen_percent = (frozen / planted * 100) if planted else 0.0
    frozen_reduction = gross * frozen_percent / 100
    creative_use = max(
        0.0,
        min(float(creative_use_reduction_lbs or 0), gross - frozen_reduction),
    )
    net = max(0.0, gross - frozen_reduction - creative_use)
    actual = max(0.0, float(actual_processed_lbs or 0))
    actual_detected = actual > 0
    expired = today > harvest_date + timedelta(days=max(0, int(expiry_days)))
    unconfirmed = max(0.0, net - actual) if actual_detected else 0.0
    if expired:
        forecast = 0.0
        status = "Expired — processing overdue"
    elif actual_detected:
        forecast = 0.0
        status = "Actual detected — remainder unconfirmed"
    elif frozen >= planted and planted > 0:
        forecast = 0.0
        status = "Fresh Frozen — no dry flower scheduled"
    elif creative_use >= gross - frozen_reduction and gross > 0:
        forecast = 0.0
        status = "Creative Use — no dry flower scheduled"
    else:
        forecast = net
        status = (
            "Projected — Creative Use planned" if creative_use > 0 else "Projected"
        )
    return {
        "gross_projected_lbs": round(gross, 1),
        "fresh_frozen_plants": frozen,
        "planted_plants": planted,
        "fresh_frozen_percent": round(frozen_percent, 1),
        "fresh_frozen_reduction_lbs": round(frozen_reduction, 1),
        "creative_use_reduction_lbs": round(creative_use, 1),
        "net_projected_lbs": round(net, 1),
        "actual_processed_lbs": round(actual, 1),
        "unconfirmed_remainder_lbs": round(unconfirmed, 1),
        "forecast_counted_lbs": round(forecast, 1),
        "expired": expired,
        "actual_detected": actual_detected,
        "status": status,
    }


def projected_harvest_dates(
    flower_entry_date: str,
    post_harvest_days: int = DEFAULT_POST_HARVEST_DAYS,
) -> dict[str, str]:
    entry = date.fromisoformat(flower_entry_date)
    harvest = entry + timedelta(days=STANDARD_FLOWER_DAYS)
    available = harvest + timedelta(days=max(0, int(post_harvest_days)))
    return {
        "harvest_date": harvest.isoformat(),
        "available_date": available.isoformat(),
    }


def sku_fill_grams(sku_type: Any) -> float:
    """Return a conservative flower-equivalent fill weight for demand planning."""
    text = str(sku_type or "").lower().replace("½", "0.5")
    patterns = (
        (r"(?:^|\D)28\s*g", 28.0),
        (r"(?:^|\D)14\s*g", 14.0),
        (r"(?:^|\D)7\s*g", 7.0),
        (r"3[.]5\s*g", 3.5),
        (r"0[.]5\s*g", 0.5),
        (r"(?:^|\D)1\s*g", 1.0),
    )
    for pattern, grams in patterns:
        if re.search(pattern, text):
            # Multipacks consume the stated unit weight for every piece.
            pack = re.search(r"(\d+)\s*(?:pk|pack)", text)
            return grams * int(pack.group(1)) if pack else grams
    return 0.0


def projected_risk(weeks_of_supply: float | None) -> str:
    if weeks_of_supply is None:
        return "Review"
    if weeks_of_supply > 8:
        return "Excess"
    if weeks_of_supply > 4:
        return "Warning"
    return "Balanced"
