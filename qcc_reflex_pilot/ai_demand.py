"""Transparent strain-level demand ensemble for clone planning.

This first pass is intentionally deterministic and auditable.  It blends the
existing availability-adjusted windows, applies a bounded recent trend that
fades over the planning horizon, and uses a conservative seasonal index only
when at least a year of weekly evidence exists.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import exp, sqrt
from statistics import mean
from typing import Any

from .cultivation import normalized_strain, sku_fill_grams


FLOWER_SKU_MARKERS = ("1g flower", "3.5g flower", "7g flower")
WINDOW_WEIGHTS = {"30 Days": 0.45, "60 Days": 0.35, "All Time": 0.20}


def _is_demand_sku(value: Any, product_scope: str) -> bool:
    label = str(value or "").casefold()
    is_flower = any(marker in label for marker in FLOWER_SKU_MARKERS)
    is_preroll = "pre-roll" in label or "preroll" in label
    if product_scope == "Pre-Rolls Only":
        return is_preroll
    if product_scope == "Flower Only":
        return is_flower
    return is_flower or is_preroll


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _window_weekly_lbs(
    rows: list[dict[str, Any]], product_scope: str
) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        sku = row.get("SKU Type", "")
        if not _is_demand_sku(sku, product_scope):
            continue
        strain = normalized_strain(row.get("Strain", ""))
        grams = sku_fill_grams(sku)
        if strain and grams > 0:
            totals[strain] += (
                max(0.0, _number(row.get("Avg Weekly Units")))
                * grams
                / 453.59237
            )
    return dict(totals)


def _blended_baselines(
    adjusted_windows: dict[str, list[dict[str, Any]]],
    fallback_rows: list[dict[str, Any]],
    product_scope: str,
) -> dict[str, float]:
    window_values = {
        label: _window_weekly_lbs(
            adjusted_windows.get(label, []), product_scope
        )
        for label in WINDOW_WEIGHTS
    }
    fallback = _window_weekly_lbs(fallback_rows, product_scope)
    strains = set(fallback)
    for values in window_values.values():
        strains.update(values)
    baselines: dict[str, float] = {}
    for strain in strains:
        available = [
            (WINDOW_WEIGHTS[label], values[strain])
            for label, values in window_values.items()
            if strain in values
        ]
        if available:
            weight_total = sum(weight for weight, _ in available)
            baselines[strain] = sum(
                weight * value for weight, value in available
            ) / weight_total
        else:
            baselines[strain] = fallback.get(strain, 0.0)
    return baselines


def _weekly_lbs_history(
    weekly_rows: list[dict[str, Any]], product_scope: str,
) -> dict[str, list[tuple[date, float]]]:
    # A strain/week can contain several package sizes.  Zero weeks identified
    # as likely OOS are omitted only when every observed SKU in that strain-week
    # carries the constrained signal.  Recent trailing gaps remain as zeroes.
    grouped: dict[tuple[str, date], dict[str, Any]] = {}
    for row in weekly_rows:
        sku = row.get("SKU Type", "")
        if not _is_demand_sku(sku, product_scope):
            continue
        strain = normalized_strain(row.get("Strain", ""))
        try:
            week = date.fromisoformat(str(row.get("Week Starting", "")))
        except ValueError:
            continue
        grams = sku_fill_grams(sku)
        if not strain or grams <= 0:
            continue
        key = (strain, week)
        bucket = grouped.setdefault(key, {"lbs": 0.0, "signals": []})
        bucket["lbs"] += max(0.0, _number(row.get("Units Shipped"))) * grams / 453.59237
        bucket["signals"].append(str(row.get("Availability Signal", "")))
    history: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for (strain, week), bucket in grouped.items():
        signals = [signal.casefold() for signal in bucket["signals"]]
        if signals and all("likely oos" in signal for signal in signals):
            continue
        history[strain].append((week, float(bucket["lbs"])))
    for values in history.values():
        values.sort(key=lambda item: item[0])
    return dict(history)


def _trend(history: list[tuple[date, float]]) -> float:
    values = [value for _, value in history]
    if len(values) < 8:
        return 1.0
    recent = values[-4:]
    prior = values[-12:-4]
    if len(prior) < 4 or mean(prior) <= 0:
        return 1.0
    raw = mean(recent) / mean(prior)
    return min(1.25, max(0.80, sqrt(max(0.0, raw))))


def _seasonality(
    history: list[tuple[date, float]], target: date
) -> float:
    if len(history) < 52:
        return 1.0
    long_average = mean(value for _, value in history)
    if long_average <= 0:
        return 1.0
    target_week = target.isocalendar().week
    comparable = [
        value
        for week, value in history
        if min(
            abs(week.isocalendar().week - target_week),
            52 - abs(week.isocalendar().week - target_week),
        ) <= 3
        and week.year < target.year
    ]
    if len(comparable) < 3:
        return 1.0
    raw = mean(comparable) / long_average
    # Shrink the observed seasonal difference by half and cap it so sparse
    # cannabis history cannot swing a cultivation plan excessively.
    return min(1.15, max(0.85, 1.0 + 0.5 * (raw - 1.0)))


def ai_two_week_demand_forecast(
    *,
    periods: list[dict[str, Any]],
    adjusted_windows: dict[str, list[dict[str, Any]]],
    weekly_rows: list[dict[str, Any]],
    fallback_rows: list[dict[str, Any]],
    product_scope: str = "Flower + Pre-Rolls",
) -> dict[str, list[float]]:
    """Return a two-week pounds forecast for every strain and period."""
    baselines = _blended_baselines(
        adjusted_windows, fallback_rows, product_scope
    )
    histories = _weekly_lbs_history(weekly_rows, product_scope)
    forecasts: dict[str, list[float]] = {}
    for strain, baseline in baselines.items():
        history = histories.get(strain, [])
        trend = _trend(history)
        future_index = 0
        values: list[float] = []
        for period in periods:
            if bool(period.get("is_historical", False)):
                values.append(0.0)
                continue
            try:
                target = date.fromisoformat(str(period.get("clone_cut_date", "")))
            except ValueError:
                target = date.today()
            damped_trend = 1.0 + (trend - 1.0) * exp(-future_index / 4.0)
            seasonal = _seasonality(history, target)
            values.append(max(0.0, 2.0 * baseline * damped_trend * seasonal))
            future_index += 1
        forecasts[strain] = values
    return forecasts
