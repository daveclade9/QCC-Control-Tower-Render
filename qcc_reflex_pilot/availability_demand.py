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
    period_days: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return all-strain flower summaries and weekly shipment signals.

    Weeks begin on Monday.  The first week in each strain/SKU series is the
    first shipped week, so pre-launch history is never treated as an outage.
    A zero full week followed by a later shipment is a likely constrained
    week; a zero week after the latest shipment is a recent gap needing review.
    Only likely constrained weeks are removed from the adjusted denominator.
    Recent trailing gaps remain counted so the operational shadow model does
    not mistake slowing demand for a confirmed stockout.
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
    history_end_date = pd.Timestamp(data["created_at"].max()).normalize()
    history_end_week = history_end_date - pd.to_timedelta(
        history_end_date.weekday(), unit="D"
    )
    period_start = (
        history_end_date - pd.Timedelta(days=max(int(period_days), 1) - 1)
        if period_days else None
    )

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
        key_rows = data[
            data["brand"].eq(brand)
            & data["strain"].eq(strain)
            & data["sku_type"].eq(sku_type)
        ]
        first_ship_date = pd.Timestamp(key_rows["created_at"].min()).normalize()
        analysis_start = max(
            first_ship_date,
            period_start if period_start is not None else first_ship_date,
        )
        if analysis_start > history_end_date:
            continue
        window_rows = key_rows[
            key_rows["created_at"].between(
                analysis_start, history_end_date + pd.Timedelta(days=1),
                inclusive="left",
            )
        ]
        if window_rows.empty:
            continue
        first_week = analysis_start - pd.to_timedelta(
            analysis_start.weekday(), unit="D"
        )
        last_ship_week = pd.Timestamp(series.index.max())
        all_weeks = pd.date_range(first_week, history_end_week, freq="W-MON")
        full = series.reindex(all_weeks, fill_value=0.0)
        active_weeks = int(full.gt(0).sum())
        calendar_days = int((history_end_date - analysis_start).days) + 1
        calendar_weeks = calendar_days / 7
        complete_week = (
            (full.index >= analysis_start)
            & ((full.index + pd.Timedelta(days=6)) <= history_end_date)
        )
        constrained_weeks = int(
            (
                full.eq(0)
                & (full.index < last_ship_week)
                & complete_week
            ).sum()
        )
        recent_gap_weeks = int(
            (
                full.eq(0)
                & (full.index > last_ship_week)
                & complete_week
            ).sum()
        )
        total_units = float(window_rows["shipped_units"].sum())
        availability_weeks = max(calendar_weeks - constrained_weeks, 1 / 7)
        adjusted_velocity = total_units / availability_weeks
        calendar_velocity = current_velocity.get(
            key, total_units / calendar_weeks if calendar_weeks else 0.0
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
            "First Ship Week": first_ship_date.date().isoformat(),
            "Last Ship Week": last_ship_week.date().isoformat(),
            "Calendar Weeks": round(calendar_weeks, 2),
            "Availability Weeks": round(availability_weeks, 2),
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
