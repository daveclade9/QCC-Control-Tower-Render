"""Bundled historical cultivation yield data used by the Control Tower.

The source workbook is ``NJ Historical Yield Analysis (1).xlsx``.  The values
below are intentionally stored with the application so the cultivation module
works locally and on Render without access to the original OneDrive file.
"""

from __future__ import annotations

from typing import Any


HISTORICAL_STRAIN_OBSERVATIONS = 323

HISTORICAL_HARVEST_COLUMNS = [
    "Crop",
    "Harvest Date",
    "Fresh Frozen",
    "Canopy sqft",
    "Wet Yield",
    "Dry Flower (Lbs)",
    "Yield (g/sqft)",
]

# Harvest dates from the first (current) sheet of ``NJ Cultivation Calendar.xlsx``.
# ISO formatting keeps the column chronologically sortable in Grid.js.
_HARVEST_DATES = {
    "1.1A": "2024-04-08",
    "2.1A": "2024-05-06",
    "1.1B": "2024-06-24",
    "2.1B": "2024-07-15",
    "3.1B": "2024-07-29",
    "1.1C": "2024-09-09",
    "2.1C": "2024-09-23",
    "3.1C": "2024-10-07",
    "4.1C": "2024-10-21",
    "1.2C": "2024-11-18",
    "2.2C": "2024-12-02",
    "3.2C": "2024-12-16",
    "4.2C": "2024-12-30",
    "1.1": "2025-01-27",
    "2.1": "2025-02-10",
    "3.1": "2025-02-24",
    "4.1": "2025-03-10",
    "5.1": "2025-03-24",
    "1.2": "2025-04-07",
    "2.2": "2025-04-21",
    "3.2": "2025-05-05",
    "4.2": "2025-05-19",
    "5.2": "2025-06-02",
    "1.3": "2025-06-16",
    "2.3": "2025-06-30",
    "3.3": "2025-07-14",
    "4.3": "2025-07-28",
    "5.3": "2025-08-11",
    "1.4": "2025-08-25",
    "2.4": "2025-09-08",
    "3.4": "2025-09-22",
    "4.4": "2025-10-06",
    "5.4": "2025-10-20",
    "1.5": "2025-11-03",
    "2.5": "2025-11-17",
    "3.5": "2025-12-01",
    "4.5": "2025-12-15",
    "5.5": "2025-12-29",
    "1.6": "2026-01-12",
    "2.6": "2026-01-26",
    "3.6": "2026-02-09",
    "4.6": "2026-02-23",
    "5.6": "2026-03-09",
    "1.7": "2026-03-23",
    "2.7": "2026-04-06",
    "3.7": "2026-04-20",
    "4.7": "2026-05-04",
    "5.7": "2026-05-18",
    "1.8": "2026-06-01",
    "2.8": "2026-06-15",
    "3.8": "2026-06-29",
}

HISTORICAL_ROOM_COLUMNS = [
    "Room",
    "Total Canopy sqft",
    "Total Dry Flower (Lbs)",
    "# Harvests",
    "Avg Yield (g/sqft)",
    "AB %",
    "C %",
    "Upgraded Lighting Harvests",
    "Upgraded Lighting Yield (g/sqft)",
    "Notes regarding Lighting Upgrades",
]

HISTORICAL_CYCLE_COLUMNS = [
    "Cycle",
    "Total Canopy sqft",
    "Total Dry Flower (Lbs)",
    "Total AB Yield (Lbs)",
    "Total C Yield (Lbs)",
    "Rooms Harvested",
    "Avg Yield (g/sqft)",
    "Notes regarding Lighting Upgrades",
]

HISTORICAL_STRAIN_COLUMNS = [
    "Strain",
    "Harvests",
    "Avg Quality Score",
    "Canopy (sqft)",
    "AB Flower (g/sqft)",
    "C Flower (g/sqft)",
    "Total Flower (g/sqft)",
    "Trim (g/sqft)",
    "Total Biomass (g/sqft)",
    "AB Flower %",
    "C Flower %",
]

# crop, flower time, canopy sqft, wet yield lb, finished flower lb,
# finished flower g/sqft, wet-to-finished conversion percent
_HARVESTS = [
    ("FLOWER 1.1A", "9-WEEKS", 1245.0, 1797.0, 230.34, 83.92, 12.8),
    ("FLOWER 2.1A", "9-WEEKS", 1260.0, 1531.0, 181.10, 66.25, 11.8),
    ("FLOWER 1.1B", "9-WEEKS", 1245.0, 2132.0, 190.90, 69.61, 8.9),
    ("FLOWER 2.1B", "10-WEEKS", 1260.0, 2119.0, 238.76, 86.03, 11.3),
    ("FLOWER 3.1B", "10-WEEKS", 1225.0, 1749.0, 209.35, 77.59, 12.0),
    ("FLOWER 1.1C", "10-WEEKS", 1245.0, 2156.0, 252.05, 91.83, 11.7),
    ("FLOWER 2.1C", "10-WEEKS", 1260.0, 2001.0, 231.57, 83.37, 11.6),
    ("FLOWER 4.1C", "11+ WEEKS", 1270.0, 1655.0, 204.20, 72.65, 12.3),
    ("FLOWER 3.1C", "10 WEEKS", 1225.0, 1822.0, 226.12, 83.73, 12.4),
    ("FLOWER 1.2C", "10 WEEKS", 1245.0, 2185.0, 263.07, 95.85, 12.0),
    ("FLOWER 2.2C", "10 WEEKS", 1260.0, 2168.0, 254.72, 91.69, 11.7),
    ("FLOWER 3.2C", "10 WEEKS", 1225.0, 1883.0, 208.47, 77.32, 11.7),
    ("FLOWER 4.2C", "10 WEEKS", 1270.0, 1614.0, 158.60, 56.65, 9.8),
    ("FLOWER 1.1", "10 WEEKS", 1245.0, 2068.0, 213.50, 77.79, 10.3),
    ("FLOWER 2.1", "10 WEEKS", 1260.0, 1950.0, 211.04, 75.97, 10.8),
    ("FLOWER 3.1", "10 WEEKS", 1225.0, 1647.0, 175.58, 65.01, 10.7),
    ("FLOWER 4.1", "10 WEEKS", 1270.0, 1923.0, 211.18, 75.43, 11.0),
    ("FLOWER 5.1", "10 WEEKS", 1085.0, 1239.0, 172.12, 71.96, 13.9),
    ("FLOWER 1.2", "10 WEEKS", 1245.0, 1924.0, 256.69, 93.52, 13.3),
    ("FLOWER 2.2", "10 WEEKS", 1260.0, 2054.0, 270.71, 97.46, 13.2),
    ("FLOWER 3.2", "10 WEEKS", 1233.0, 1947.0, 240.60, 88.51, 12.4),
    ("FLOWER 4.2", "10 WEEKS", 1270.0, 1765.0, 266.72, 95.26, 15.1),
    ("FLOWER 5.2", "10 WEEKS", 1085.0, 2002.0, 255.01, 106.61, 12.7),
    ("FLOWER 1.3", "10 WEEKS", 1245.0, 2100.0, 245.28, 89.36, 11.7),
    ("FLOWER 2.3", "10 WEEKS", 1260.0, 1961.0, 242.88, 87.44, 12.4),
    ("FLOWER 3.3", "10 WEEKS", 1233.0, 1597.0, 225.27, 82.87, 14.1),
    ("FLOWER 4.3", "10 WEEKS", 1250.0, 2068.0, 255.40, 92.68, 12.4),
    ("FLOWER 5.3", "10 WEEKS", 1085.0, 2109.0, 251.20, 105.02, 11.9),
    ("FLOWER 1.4", "10 WEEKS", 1250.0, 1958.0, 219.40, 79.62, 11.2),
    ("FLOWER 2.4", "10 WEEKS", 1270.0, 1898.0, 229.60, 82.01, 12.1),
    ("FLOWER 3.4", "10 WEEKS", 1238.0, 1741.0, 215.75, 79.05, 12.4),
    ("FLOWER 4.4", "10 WEEKS", 1250.0, 2105.0, 300.40, 109.01, 14.3),
    ("FLOWER 5.4", "10 WEEKS", 1085.0, 2036.0, 248.06, 103.71, 12.2),
    ("FLOWER 1.5", "10 WEEKS", 1250.0, 2131.0, 267.62, 101.47, 12.6),
    ("FLOWER 2.5", "10 WEEKS", 1270.0, 1702.0, 206.18, 71.35, 12.1),
    ("FLOWER 3.5", "10 WEEKS", 1238.0, 1449.0, 189.50, 69.43, 13.1),
    ("FLOWER 4.5", "10 WEEKS", 1250.0, 2014.0, 288.19, 104.58, 14.3),
    ("FLOWER 5.5", "10 WEEKS", 1085.0, 1880.0, 265.15, 110.85, 14.1),
    ("FLOWER 1.6", "10 WEEKS", 1250.0, 2315.0, 269.52, 97.80, 11.6),
    ("FLOWER 2.6", "10 WEEKS", 1270.0, 2116.0, 258.15, 92.20, 12.2),
    ("FLOWER 3.6", "10 WEEKS", 1238.0, 1882.0, 201.97, 74.00, 10.7),
    ("FLOWER 4.6", "10 WEEKS (FF)", 1157.0, 2304.0, 296.56, 116.27, 12.9),
    ("FLOWER 5.6", "10 WEEKS (FF)", 884.0, 1643.0, 188.75, 96.85, 11.5),
    ("FLOWER 1.7", "10 WEEKS (FF)", 1043.0, 1574.0, 215.77, 93.84, 13.7),
    ("FLOWER 2.7", "10 WEEKS (FF)", 1142.0, 1916.0, 217.71, 86.47, 11.4),
    ("FLOWER 3.7", "10 WEEKS (FF)", 1028.0, 1558.0, 176.55, 77.90, 11.3),
    ("FLOWER 4.7", "10 WEEKS (FF)", 1065.0, 2170.0, 257.25, 109.57, 11.9),
    ("FLOWER 5.7", "10 WEEKS (FF)", 944.0, 2266.0, 201.43, 96.79, 8.9),
    ("FLOWER 1.8", "10 WEEKS (FF)", 1064.0, 2045.0, 230.03, 98.07, 11.2),
    ("FLOWER 2.8", "10 WEEKS (FF)", 1159.5, 2281.0, 247.44, 96.80, 10.8),
    ("FLOWER 3.8", "10 WEEKS (FF)", 1139.0, 1890.0, 219.48, 87.40, 11.6),
]

# room, canopy sqft, finished yield lb, harvests, avg lb/harvest,
# avg finished yield g/sqft, upgraded-lighting avg g/sqft, comments
_ROOM_SUMMARY = [
    ("Flower Room 1", 14572.0, 2854.17, 12, 237.85, 88.85, 96.71, "F1.5 first upgraded-lighting crop"),
    ("Flower Room 2", 14931.5, 2789.86, 12, 232.49, 84.75, None, "F2.7 first upgraded-lighting attempt"),
    ("Flower Room 3", 13182.0, 2288.64, 11, 208.06, 78.75, None, "F3.7 first upgraded-lighting crop; F3.8 first full trial"),
    ("Flower Room 4", 10992.0, 2238.50, 9, 248.72, 92.37, 109.86, "F4.4 first upgraded-lighting crop"),
    ("Flower Room 5", 7253.0, 1581.72, 7, 225.96, 98.92, 103.30, "F5.2 first upgraded-lighting crop"),
]

# AB/C percentages and upgraded-lighting harvest counts are calculated from
# the Raw Room Data sheet.  Room 2 and Room 3 do have upgraded-lighting
# observations even though the workbook's comparison chart leaves those bars
# blank, so the combined table preserves their measured averages here.
_ROOM_CLASSIFICATION_SUMMARY = {
    "Flower Room 1": (80.9, 19.2, 4, 96.71),
    "Flower Room 2": (83.1, 17.0, 2, 91.64),
    "Flower Room 3": (84.1, 15.9, 2, 82.65),
    "Flower Room 4": (82.1, 18.1, 4, 109.86),
    "Flower Room 5": (85.6, 14.3, 6, 103.30),
}

# cycle, rooms, canopy sqft, finished lb, avg lb/room, yield g/sqft,
# AB g/sqft, C g/sqft, AB:C ratio
_CYCLE_SUMMARY = [
    ("1A", 2, 2505, 411.44, 205.72, 74.50, 58.44, 16.07, 3.64),
    ("1B", 3, 3730, 639.01, 213.00, 77.71, 67.99, 10.15, 6.70),
    ("1C", 4, 4980, 913.94, 228.48, 83.25, 67.09, 16.47, 4.07),
    ("2C", 4, 4980, 884.86, 221.22, 80.60, 60.93, 19.68, 3.10),
    ("Cycle 1", 5, 6085, 983.42, 196.68, 73.31, 62.22, 11.08, 5.61),
    ("Cycle 2", 5, 6078, 1289.73, 257.95, 96.25, 79.44, 16.82, 4.72),
    ("Cycle 3", 5, 6078, 1220.03, 244.01, 91.05, 75.36, 15.69, 4.80),
    ("Cycle 4", 5, 6093, 1213.21, 242.64, 90.32, 76.87, 13.29, 5.78),
    ("Cycle 5", 5, 6093, 1216.64, 243.33, 90.57, 79.57, 11.00, 7.23),
    ("Cycle 6", 5, 5724, 1214.95, 242.99, 96.28, 72.05, 24.63, 2.92),
    ("Cycle 7", 5, 5222, 1068.71, 213.74, 92.83, 77.85, 27.00, 2.88),
]

# Exact AB and C pounds from the Cycle Yield Summary sheet.
_CYCLE_CLASS_POUNDS = {
    "1A": (322.74, 88.73),
    "1B": (559.12, 83.50),
    "1C": (736.59, 180.82),
    "2C": (668.99, 216.02),
    "Cycle 1": (834.71, 148.68),
    "Cycle 2": (1064.44, 225.32),
    "Cycle 3": (1009.81, 210.21),
    "Cycle 4": (1032.58, 178.55),
    "Cycle 5": (1068.84, 147.80),
    "Cycle 6": (909.17, 310.83),
    "Cycle 7": (896.20, 310.83),
}

_CYCLE_LIGHTING_NOTES = {
    "Cycle 2": "F5.2 first upgraded-lighting crop",
    "Cycle 4": "F4.4 first upgraded-lighting crop",
    "Cycle 5": "F1.5 first upgraded-lighting crop",
    "Cycle 7": (
        "F2.7 first upgraded-lighting attempt; "
        "F3.7 first upgraded-lighting crop"
    ),
}

# strain, harvest observations, quality score, canopy sqft, AB g/sqft,
# C g/sqft, total flower g/sqft, trim g/sqft, total biomass g/sqft
_STRAIN_SUMMARY = [
    ("G13", 14, 7.50, 2004.1, 86.28, 15.89, 102.16, 50.71, 152.87),
    ("Pine Tar", 7, 8.42, 1003.9, 79.80, 21.81, 101.61, 40.43, 142.04),
    ("J1", 40, 8.30, 8154.2, 80.69, 16.90, 97.59, 36.37, 133.96),
    ("Blue Dream", 2, None, 185.0, 55.26, 39.59, 94.85, 56.17, 151.02),
    ("Diamond Bar", 42, 8.05, 8177.0, 77.30, 17.45, 94.75, 35.24, 129.99),
    ("Razberry Runtz (RPG 103)", 4, None, 303.0, 68.21, 19.43, 87.63, 51.05, 138.68),
    ("Lemon Cherry Gelato", 26, 7.99, 4101.2, 74.18, 13.04, 87.22, 31.53, 118.75),
    ("LA Piff", 19, 8.65, 3135.0, 71.77, 15.40, 87.17, 32.85, 120.01),
    ("Diamond Dust", 25, 8.70, 3494.1, 71.35, 13.15, 84.50, 35.61, 120.11),
    ("Fig Bar", 45, 7.90, 8126.9, 68.59, 14.28, 82.88, 31.97, 114.85),
    ("Orange Push Pop", 46, 7.93, 11792.8, 67.71, 14.77, 82.49, 38.68, 121.17),
    ("Lipsmackerz", 3, 8.55, 456.8, 73.44, 8.60, 82.04, 34.94, 116.98),
    ("Tahoe OG", 2, None, 277.5, 57.42, 17.13, 74.54, 53.15, 127.69),
    ("Figueroa OG", 13, None, 2411.1, 57.62, 14.88, 72.50, 28.06, 100.56),
    ("Razberry Runtz", 9, None, 993.0, 59.74, 12.39, 72.13, 36.50, 108.63),
    ("Private Reserve", 11, 6.90, 1390.5, 52.75, 12.77, 65.52, 32.93, 98.45),
    ("Bubba Skywalker", 1, None, 105.0, 56.36, 5.17, 61.53, 23.99, 85.52),
    ("Brooklyn Runtz", 5, None, 727.5, 49.09, 11.20, 60.30, 31.79, 92.09),
]


def room_from_crop(crop: str) -> str:
    """Return the normalized flower-room name encoded in a crop name."""
    number = crop.replace("FLOWER", "").strip().split(".", 1)[0]
    return f"Flower Room {number}"


def historical_harvest_rows(room: str = "All Flower Rooms") -> list[dict[str, Any]]:
    rows = []
    for crop, flower_time, canopy, wet, flower, yield_sqft, conversion in _HARVESTS:
        crop_room = room_from_crop(crop)
        if room != "All Flower Rooms" and crop_room != room:
            continue
        rows.append({
            "Crop": crop.replace("FLOWER ", "F"),
            "Room": crop_room,
            "Flower Time": flower_time.replace("-", " "),
            "Canopy Sq Ft": round(canopy, 1),
            "Wet Yield (lb)": round(wet, 1),
            "Finished Flower (lb)": round(flower, 2),
            "Yield (g/sqft)": round(yield_sqft, 2),
            "Wet Conversion": f"{conversion:.1f}%",
        })
    return list(reversed(rows))


def historical_room_rows() -> list[dict[str, Any]]:
    return [{
        "Room": room,
        "Harvests": harvests,
        "Total Finished (lb)": round(total, 1),
        "Avg / Harvest (lb)": round(average, 1),
        "Avg Yield (g/sqft)": round(yield_sqft, 1),
        "Upgraded Lighting (g/sqft)": "—" if upgraded is None else round(upgraded, 1),
        "Historical Canopy (sqft)": round(canopy, 1),
        "Notes": comments,
    } for room, canopy, total, harvests, average, yield_sqft, upgraded, comments in _ROOM_SUMMARY]


def historical_harvest_table_data(
    room: str = "All Flower Rooms",
) -> list[list[Any]]:
    """Return individual harvests in sortable table-column order."""
    rows: list[list[Any]] = []
    for crop, flower_time, canopy, wet, flower, yield_sqft, _ in reversed(_HARVESTS):
        crop_room = room_from_crop(crop)
        if room != "All Flower Rooms" and crop_room != room:
            continue
        crop_code = crop.replace("FLOWER ", "")
        rows.append([
            f"F{crop_code}",
            _HARVEST_DATES.get(crop_code, "—"),
            "Yes" if "(FF)" in flower_time.upper() else "No",
            round(canopy, 1),
            round(wet, 1),
            round(flower, 2),
            round(yield_sqft, 2),
        ])
    return rows


def historical_room_table_data(
    room_filter: str = "All Flower Rooms",
) -> list[list[Any]]:
    """Return room rollups in sortable table-column order."""
    rows: list[list[Any]] = []
    for room, canopy, total, harvests, _, yield_sqft, _, comments in _ROOM_SUMMARY:
        if room_filter != "All Flower Rooms" and room != room_filter:
            continue
        ab_percent, c_percent, upgraded_count, upgraded_yield = (
            _ROOM_CLASSIFICATION_SUMMARY[room]
        )
        rows.append([
            room,
            round(canopy, 1),
            round(total, 2),
            harvests,
            round(yield_sqft, 2),
            round(ab_percent, 1),
            round(c_percent, 1),
            upgraded_count,
            round(upgraded_yield, 2),
            comments,
        ])
    return rows


def historical_cycle_table_data() -> list[list[Any]]:
    """Return operating-cycle rollups in sortable table-column order."""
    rows: list[list[Any]] = []
    for cycle, rooms, canopy, total, _, yield_sqft, _, _, _ in _CYCLE_SUMMARY:
        ab_pounds, c_pounds = _CYCLE_CLASS_POUNDS[cycle]
        rows.append([
            cycle,
            canopy,
            round(total, 2),
            round(ab_pounds, 2),
            round(c_pounds, 2),
            rooms,
            round(yield_sqft, 2),
            _CYCLE_LIGHTING_NOTES.get(cycle, "—"),
        ])
    return rows


def historical_room_chart_rows() -> list[dict[str, Any]]:
    return [{
        "Room": room.replace("Flower Room ", "F"),
        "Average Yield": round(yield_sqft, 1),
        "Upgraded Lighting": None if upgraded is None else round(upgraded, 1),
    } for room, _, _, _, _, yield_sqft, upgraded, _ in _ROOM_SUMMARY]


def historical_cycle_rows() -> list[dict[str, Any]]:
    return [{
        "Cycle": cycle,
        "Rooms": rooms,
        "Canopy Sq Ft": canopy,
        "Finished Flower (lb)": round(total, 1),
        "Avg / Room (lb)": round(average, 1),
        "Yield (g/sqft)": round(yield_sqft, 1),
        "AB (g/sqft)": round(ab_yield, 1),
        "C (g/sqft)": round(c_yield, 1),
        "AB:C Ratio": round(ratio, 2),
    } for cycle, rooms, canopy, total, average, yield_sqft, ab_yield, c_yield, ratio in _CYCLE_SUMMARY]


def historical_strain_rows() -> list[dict[str, Any]]:
    return [{
        "Strain": strain,
        "Harvest Observations": harvests,
        "Quality Score": "—" if quality is None else round(quality, 2),
        "Canopy Grown (sqft)": round(canopy, 1),
        "AB Flower (g/sqft)": round(ab_yield, 1),
        "C Flower (g/sqft)": round(c_yield, 1),
        "Total Flower (g/sqft)": round(total_yield, 1),
        "Trim (g/sqft)": round(trim_yield, 1),
        "Total Biomass (g/sqft)": round(biomass_yield, 1),
    } for strain, harvests, quality, canopy, ab_yield, c_yield, total_yield, trim_yield, biomass_yield in _STRAIN_SUMMARY]


def historical_strain_options() -> list[str]:
    """Return strain choices for the historical benchmark filter."""
    return ["All Strains", *sorted(row[0] for row in _STRAIN_SUMMARY)]


def historical_strain_table_data(
    strain_filter: str = "All Strains",
) -> list[list[Any]]:
    """Return strain benchmarks in sortable table-column order."""
    rows: list[list[Any]] = []
    for (
        strain,
        harvests,
        quality,
        canopy,
        ab_yield,
        c_yield,
        total_yield,
        trim_yield,
        biomass_yield,
    ) in _STRAIN_SUMMARY:
        if strain_filter != "All Strains" and strain != strain_filter:
            continue
        ab_percent = (ab_yield / total_yield * 100) if total_yield else 0.0
        c_percent = (c_yield / total_yield * 100) if total_yield else 0.0
        rows.append([
            strain,
            harvests,
            "—" if quality is None else round(quality, 2),
            round(canopy, 1),
            round(ab_yield, 1),
            round(c_yield, 1),
            round(total_yield, 1),
            round(trim_yield, 1),
            round(biomass_yield, 1),
            round(ab_percent, 1),
            round(c_percent, 1),
        ])
    return rows


def historical_strain_chart_rows(
    limit: int = 10,
    strain_filter: str = "All Strains",
) -> list[dict[str, Any]]:
    return [{
        "Strain": row[0],
        "Flower Yield": round(row[6], 1),
        "Observations": row[1],
    } for row in _STRAIN_SUMMARY
      if strain_filter == "All Strains" or row[0] == strain_filter][:limit]


def historical_kpis(room: str = "All Flower Rooms") -> dict[str, str]:
    rows = historical_harvest_rows(room)
    total_finished = sum(float(row["Finished Flower (lb)"]) for row in rows)
    total_canopy = sum(float(row["Canopy Sq Ft"]) for row in rows)
    weighted_yield = (
        total_finished * 453.59237 / total_canopy if total_canopy else 0.0
    )
    avg_finished = total_finished / len(rows) if rows else 0.0
    avg_conversion = (
        sum(float(str(row["Wet Conversion"]).rstrip("%")) for row in rows) / len(rows)
        if rows else 0.0
    )
    return {
        "harvests": f"{len(rows):,}",
        "total_finished": f"{total_finished:,.1f} lb",
        "average_finished": f"{avg_finished:,.1f} lb",
        "weighted_yield": f"{weighted_yield:,.1f} g/sqft",
        "average_conversion": f"{avg_conversion:.1f}%",
    }
