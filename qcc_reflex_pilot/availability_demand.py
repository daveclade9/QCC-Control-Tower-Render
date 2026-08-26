"""Experimental shipment-availability analysis for cultivation planning.

This module deliberately does not participate in the production SKU velocity
calculation.  It provides a shadow model that can be compared with planting
history before QCC elects to use availability-adjusted demand operationally.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


DEMAND_SKU_TYPES = (
    "1g Flower",
    "3.5g Flower",
    "7g Flower",
    "1g Pre-Roll",
    "3.5g Pre-Rolls",
)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.astype(object).where(pd.notna(frame), None)
    return clean.to_dict(orient="records")


def build_availability_demand_analysis(
    demand: pd.DataFrame,
    velocity: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    """Return all-strain flower summaries and weekly shipment signals.

    Weeks begin on Monday.  The first week in each strain/SKU series is the
    first shipped week, so pre-launch history is never treated as an outage.
    A zero week followed by a later shipment is a likely constrained week; a
    zero week after the latest shipment is a recent gap needing review.
    """
    if demand.empty:
        return {"summary": [], "weekly": []}

    required = {"created_at", "brand", "strain", "sku_type", "shipped_units"}
    if not required.issubset(demand.columns):
        return {"summary": [], "weekly": []}

    data = demand.loc[
        demand["sku_type"].isin(DEMAND_SKU_TYPES),
        list(required),
    ].copy()
    data["created_at"] = pd.to_datetime(data["created_at"], errors="coerce")
    data["shipped_units"] = pd.to_numeric(
        data["shipped_units"], errors="coerce"
    ).fillna(0.0)
    for column in ["brand", "strain", "sku_type"]:
        data[column] = data[column].fillna("").astype(str).str.strip()
    data = data[
        data["created_at"].notna()
        & data["strain"].ne("")
        & data["sku_type"].ne("")
        & data["shipped_units"].gt(0)
    ].copy()
    if data.empty:
        return {"summary": [], "weekly": []}

    data["week_start"] = (
        data["created_at"].dt.normalize()
        - pd.to_timedelta(data["created_at"].dt.weekday, unit="D")
    )
    weekly_actual = data.groupby(
        ["brand", "strain", "sku_type", "week_start"], dropna=False
    )["shipped_units"].sum()
    history_end = data["week_start"].max()

    current_velocity: dict[tuple[str, str, str], float] = {}
    if not velocity.empty:
        for _, row in velocity.iterrows():
            key = (
                str(row.get("Brand", "") or "").strip(),
                str(row.get("Strain", "") or "").strip(),
                str(row.get("SKU Type", "") or "").strip(),
            )
            if key[2] not in DEMAND_SKU_TYPES:
                continue
            velocity_value = pd.to_numeric(
                row.get("Avg Weekly Units", 0), errors="coerce"
            )
            current_velocity[key] = (
                0.0 if pd.isna(velocity_value) else float(velocity_value)
            )

    summary_rows: list[dict[str, Any]] = []
    weekly_rows: list[dict[str, Any]] = []
    keys = data[["brand", "strain", "sku_type"]].drop_duplicates()
    for brand, strain, sku_type in keys.itertuples(index=False, name=None):
        key = (brand, strain, sku_type)
        series = weekly_actual.loc[key]
        first_week = pd.Timestamp(series.index.min())
        last_ship_week = pd.Timestamp(series.index.max())
        all_weeks = pd.date_range(first_week, history_end, freq="W-MON")
        full = series.reindex(all_weeks, fill_value=0.0)
        active_weeks = int(full.gt(0).sum())
        total_weeks = int(len(full))
        constrained_weeks = int(
            ((full.eq(0)) & (full.index < last_ship_week)).sum()
        )
        recent_gap_weeks = int(
            ((full.eq(0)) & (full.index > last_ship_week)).sum()
        )
        total_units = float(full.sum())
        adjusted_velocity = total_units / active_weeks if active_weeks else 0.0
        calendar_velocity = current_velocity.get(
            key, total_units / total_weeks if total_weeks else 0.0
        )
        uplift = (
            (adjusted_velocity / calendar_velocity - 1) * 100
            if calendar_velocity > 0 else 0.0
        )
        signal = (
            "Recent gap — review"
            if recent_gap_weeks
            else "Historical constraints found"
            if constrained_weeks
            else "Continuous weekly shipping"
        )
        summary_rows.append({
            "Brand": brand,
            "Strain": strain,
            "SKU Type": sku_type,
            "First Ship Week": first_week.date().isoformat(),
            "Last Ship Week": last_ship_week.date().isoformat(),
            "Calendar Weeks": total_weeks,
            "Shipping Weeks": active_weeks,
            "Likely Constrained Weeks": constrained_weeks,
            "Recent Gap Weeks": recent_gap_weeks,
            "Current Velocity": round(calendar_velocity, 1),
            "Experimental Adjusted Velocity": round(adjusted_velocity, 1),
            "Adjustment": f"{uplift:+.0f}%",
            "Signal": signal,
        })
        for week, units in full.items():
            if units > 0:
                status = "Shipping"
            elif week < last_ship_week:
                status = "Likely OOS proxy"
            else:
                status = "Recent gap — review"
            weekly_rows.append({
                "Brand": brand,
                "Strain": strain,
                "SKU Type": sku_type,
                "Week Starting": pd.Timestamp(week).date().isoformat(),
                "Units Shipped": round(float(units), 1),
                "Availability Signal": status,
            })

    summary = pd.DataFrame(summary_rows).sort_values(
        ["Strain", "SKU Type", "Brand"], kind="stable"
    )
    weekly = pd.DataFrame(weekly_rows).sort_values(
        ["Strain", "SKU Type", "Week Starting"], kind="stable"
    )
    return {"summary": _records(summary), "weekly": _records(weekly)}
