"""Read-only demand normalization rules copied from QCC Control Tower 81.2."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


QCC_ORIGIN_FACILITY = "The QCC Group LLC"
GRAMS_PER_POUND = 453.59237

SKU_WEIGHT_GRAMS = {
    "1g Flower": 1.0,
    "3.5g Flower": 3.5,
    "7g Flower": 7.0,
    "14g Flower": 14.0,
    "28g Flower": 28.0,
    "28g Flower Smalls": 28.0,
    "1g Pre-Roll": 1.0,
    "3.5g Pre-Rolls": 3.5,
    "1g Infused Pre-Roll": 1.0,
    "1g IWH Infused Pre-Roll": 1.0,
    "3.5g Infused Pre-Rolls 5-Pack": 3.5,
    "3.5g IWH Infused Pre-Rolls 5-Pack": 3.5,
    "1g Vape DC": 1.0,
    "1g Vape CR": 1.0,
    "0.5g Vape LR": 0.5,
    "0.5g Concentrate": 0.5,
    "1g Concentrate": 1.0,
    "Wet Badder 1g": 1.0,
    "Wet Diamonds 1g": 1.0,
    "1g Live Rosin": 1.0,
}

CLADE9_1G_LIVE_ROSIN_PACKAGE_TAGS = {
    "1A4110300006019000006685",
    "1A4110300006019000006688",
    "1A4110300006019000006762",
    "1A4110300006019000006759",
    "1A4110300006019000006844",
    "1A4110300006019000006779",
}

CLADE9_STRAIN_PATTERNS = {
    "Fig Bar": r"\bfig\s*bar\b",
    "Diamond Bar": r"\bdiamond\s+bar\b|\bsalted\s+caramel\b",
    "Figueroa OG": r"\bfigueroa\s+og\b",
    "LA Piff": r"\bla\s+piff\b",
    "Diamond Dust": r"\bdiamond\s+dust\b",
    "Brooklyn Runtz": r"\bbrooklyn\s+runtz\b",
    "Orange Push Pop": r"\borange\s+push\s+pop\b",
    "J1": r"\bj\s*1\b",
    "Private Reserve OG": r"\bprivate\s+reserve(?:\s+og)?\b",
    "Tahoe OG": r"\btahoe\s+og\b",
    "Pre-98 Bubba": r"\bpre[- ]?98\s+bubba\b",
    "RPG #34": r"\brpg\s*#?\s*34\b",
    "Razberry Runtz": r"\brpg\s*#?\s*38\b|\brazberry\s+runtz\b",
    "RPG #42": r"\brpg\s*#?\s*42\b",
    "Blue Dream": r"\bblue\s+dream\b",
    "Lemon Cherry Gelato": r"\blemon\s+cherry\s+gelato\b",
    "Lip Smackerz": r"\blip\s*smackerz\b|\blipsmackerz\b",
    "Pine Tar": r"\bpine\s*tar\b|\bpinetar\b",
}

CRAFT_KINGS_STRAIN_PATTERNS = {
    "Candy Cut": r"\bcandy\s+cut\b",
    "Sour Chem": r"\bsour\s+chem\b",
    "Golden Goat": r"\bgolden\s+goat\b",
}

CRAFT_KINGS_HYBRID_BLEND_PACKAGE_TAGS = {
    "1A4110300002A31000037453",
    "1A4110300006019000006265",
    "1A4110300006019000006266",
    "1A4110300006019000006267",
}

BLEND_PATTERNS = {
    "Sativa Blend": r"^\s*sativa\s+blend\b",
    "Indica Blend": r"^\s*indica\s+blend\b",
    "Hybrid Blend": r"^\s*hybrid\s+blend\b",
}

RETIRED_OR_ON_HOLD_STRAINS = {
    "figueroa og", "peak", "prime", "pluto", "pluto 2.0",
    "pre-98 bubba", "rpg #34", "rpg #42",
}

# Manual exceptions take precedence over shipment-age recommendations. Add a
# normalized ``(brand, strain, sku_type)`` tuple here when Sales confirms that
# a product is intentionally seasonal or permanently retired.
PRODUCT_LIFECYCLE_OVERRIDES: dict[tuple[str, str, str], str] = {}


def contains_text(value: Any, search_terms: list[str]) -> bool:
    text = str(value or "").lower()
    return any(term.lower() in text for term in search_terms)


def canonicalize_transfer_item(item: Any) -> str:
    text = str(item or "").strip().lower()
    text = re.sub(r"\b7gs\b", "7g", text)
    text = re.sub(r"\b(?:ea|each)\b", "", text)
    text = re.sub(r"pre[ -]?rolls?", "pre-roll", text)
    text = re.sub(r"[^a-z0-9.#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_strain_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip(" -_"))
    aliases = {
        "private reserve": "Private Reserve OG",
        "private reserve og": "Private Reserve OG",
        "lip smackerz": "Lip Smackerz",
        "lipsmackerz": "Lip Smackerz",
        "rpg38": "Razberry Runtz",
        "rpg #38": "Razberry Runtz",
        "pinetar": "Pine Tar",
    }
    return aliases.get(text.lower(), text.title() if text else "Strain Needs Review")


UNFINISHED_INVENTORY_STAGES = {
    "Sellable Bulk", "WIP-Cultivation", "WIP-Manufacturing", "Pre-WIP",
}


def compatible_inventory_brand(
    row: dict[str, Any], demand_brand_by_strain: dict[str, str] | None = None
) -> str:
    """Return the planning brand for unfinished inventory without renaming it.

    Facility and ownership gates intentionally run before product and strain
    inference. The three shared blend outputs remain a production-planning
    exception and do not make every source package Craft Kings-compatible.
    """
    stage = str(row.get("Production Stage", row.get("production_stage", "")) or "").strip()
    if stage not in UNFINISHED_INVENTORY_STAGES:
        return ""

    current_facility = str(
        row.get("Current Facility", row.get("current_facility", ""))
        or row.get("Facility", row.get("facility", ""))
        or ""
    ).strip()
    ownership = str(
        row.get("Ownership Status", row.get("ownership_status", "")) or ""
    ).strip()
    if (
        current_facility == "Building 1A"
        or ownership == "Partner-Owned / Compliance Managed"
    ):
        return "ROFR / Not Purchased"
    if ownership == "QCC-Owned / Purchased from Building 1A":
        return "Unallocated QCC Brand"
    if current_facility and current_facility != "Building 33 (C9)":
        return "Compatibility Needs Review"

    item = str(row.get("Item", row.get("item", "")) or "")
    category = str(row.get("Category", row.get("category", "")) or "")
    combined = f"{item} {category}"
    package_tag = str(
        row.get("Metrc Tag", row.get("package_tag", "")) or ""
    ).strip()
    strain = normalize_strain_name(
        row.get("Strain", row.get("strain", ""))
    )

    if re.search(r"\bwet\s+(?:badder|diamonds?)\b", combined, re.I):
        return "Locals Only"
    if package_tag in CLADE9_1G_LIVE_ROSIN_PACKAGE_TAGS:
        return "Clade9"
    if package_tag in CRAFT_KINGS_HYBRID_BLEND_PACKAGE_TAGS:
        return "Craft Kings"
    if re.search(r"\bcraft\s+kings?\b", combined, re.I):
        return "Craft Kings"
    if re.search(r"\bclade\s*9\b", combined, re.I):
        return "Clade9"
    if strain in CLADE9_STRAIN_PATTERNS:
        return "Clade9"
    if strain in CRAFT_KINGS_STRAIN_PATTERNS:
        return "Craft Kings"

    demand_brand = str(
        (demand_brand_by_strain or {}).get(strain.lower(), "") or ""
    ).strip()
    if demand_brand:
        return demand_brand

    existing_brand = str(
        row.get("Brand", row.get("brand", "")) or ""
    ).strip()
    if existing_brand in {
        "Clade9", "Craft Kings", "Royal Smalls", "Locals Only",
        "Cookies", "Precious",
    }:
        return existing_brand
    return "Clade9" if current_facility == "Building 33 (C9)" else "Compatibility Needs Review"


def gummy_variant(item: Any) -> str | None:
    text = str(item or "")
    match = re.search(
        r"(?:bulk[- ]*)?([a-z][a-z ]+?)[- ]*gumm(?:y|ies)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    flavor = re.sub(r"\s+", " ", match.group(1)).strip(" -_")
    return f"{flavor.title()} Gummies" if flavor else "Gummies"


def locals_only_fields(item: Any) -> tuple[str, str]:
    text = str(item or "")
    product = (
        "Wet Diamonds 1g"
        if re.search(r"wet\s+diamonds?", text, re.IGNORECASE)
        else "Wet Badder 1g"
    )
    strain = re.split(
        r"\s*[-:]?\s*\d*(?:\.\d+)?\s*g?\s*wet\s+",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    strain = re.sub(r"^\s*locals\s+only\s*[-:]?\s*", "", strain, flags=re.I)
    return normalize_strain_name(strain), product


def classify_infused_pre_roll(item: Any) -> str | None:
    text = str(item or "").lower()
    if not re.search(r"infused\s+pre[- ]?roll", text):
        return None
    iwh = bool(re.search(r"\biwh\b|ice\s+water\s+hash", text))
    five_pack = bool(
        re.search(r"\b5\s*(?:pk|pack)\b", text)
        or re.search(r"(?<!\d)3\.5\s*g\b", text)
    )
    if five_pack:
        return (
            "3.5g IWH Infused Pre-Rolls 5-Pack"
            if iwh else "3.5g Infused Pre-Rolls 5-Pack"
        )
    return "1g IWH Infused Pre-Roll" if iwh else "1g Infused Pre-Roll"


def classify_sku_type(row: pd.Series) -> str:
    package_tag = str(row.get("package_tag", "") or "").strip()
    if package_tag in CLADE9_1G_LIVE_ROSIN_PACKAGE_TAGS:
        return "1g Live Rosin"
    item = str(row.get("item", "") or "").lower()
    category = str(row.get("item_category", "") or "").lower()
    combined = f"{item} {category}"
    infused = classify_infused_pre_roll(item)
    if infused:
        return infused
    if re.search(r"\bwet\s+(?:badder|diamonds?)\b", item):
        return locals_only_fields(item)[1]
    if "live rosin" in item and re.search(r"(?<![\d.])1\s*g\b", item):
        return "1g Live Rosin"
    if contains_text(item, ["vape", "cartridge", "disposable"]):
        if re.search(r"\bdc\b", item) and re.search(r"(?<![\d.])1\s*g\b", item):
            return "1g Vape DC"
        if re.search(r"\bcr\b", item) and re.search(r"(?<![\d.])1\s*g\b", item):
            return "1g Vape CR"
        if re.search(r"\blr\b", item) and re.search(r"(?<!\d)0\.5\s*g\b", item):
            return "0.5g Vape LR"
        return "Other Packaged SKU"
    if contains_text(item, ["gumm", "edible", "chocolate"]):
        return "Edibles"
    if contains_text(combined, ["concentrate", "wet badder", "badder"]):
        if re.search(r"(?<!\d)0\.5\s*g\b", item):
            return "0.5g Concentrate"
        if re.search(r"(?<![\d.])1\s*g\b", item):
            return "1g Concentrate"
        return "Other Packaged SKU"
    if contains_text(combined, ["pre-roll", "preroll", "pre roll"]):
        if re.search(r"(?<!\d)3\.5\s*g\b", item):
            return "3.5g Pre-Rolls"
        if re.search(r"(?<![\d.])1\s*g\b", item):
            return "1g Pre-Roll"
        return "Other Packaged SKU"
    size_rules = [
        (r"(?<!\d)3\.5\s*g\b", "3.5g Flower"),
        (r"(?<![\d.])7\s*g(?:s)?\b", "7g Flower"),
        (r"(?<![\d.])14\s*g\b", "14g Flower"),
        (r"(?<![\d.])28(?:\.0+)?\s*g\b", "28g Flower"),
        (r"(?<![\d.])1\s*(?:oz|ounce)\b", "28g Flower"),
        (r"(?<![\d.])1\s*g\b", "1g Flower"),
    ]
    for pattern, sku_type in size_rules:
        if re.search(pattern, item):
            if sku_type == "28g Flower" and contains_text(
                item, ["royal smalls", "smalls", "small buds", "small bud"]
            ):
                return "28g Flower Smalls"
            return sku_type
    reported_weight = pd.to_numeric(row.get("unit_weight_grams"), errors="coerce")
    if "packaged" in item and pd.notna(reported_weight):
        for weight, sku_type in [(1, "1g Flower"), (3.5, "3.5g Flower"), (7, "7g Flower"), (14, "14g Flower"), (28, "28g Flower")]:
            if abs(float(reported_weight) - weight) < 0.01:
                return sku_type
    return "Other Packaged SKU"


def infer_strain(item: Any) -> str:
    text = str(item or "")
    if re.search(r"\bwet\s+(?:badder|diamonds?)\b", text, re.I):
        return locals_only_fields(text)[0]
    gummy = gummy_variant(text)
    if gummy:
        return gummy
    for name, pattern in {**CLADE9_STRAIN_PATTERNS, **CRAFT_KINGS_STRAIN_PATTERNS, **BLEND_PATTERNS}.items():
        if re.search(pattern, text, re.IGNORECASE):
            return name
    candidate = re.sub(
        r"^(?:royal\s+smalls|craft\s+kings|clade\s*9|locals\s+only|precious)\s*[-:]?\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    candidate = re.split(
        r"\s+(?:packaged\b|\d+(?:\.\d+)?\s*g(?:s)?\b|\d+pk\b|iwh\b|infused\b|pre[- ]?rolls?\b|vape\b|cartridge\b|disposable\b|gumm(?:y|ies)\b)",
        candidate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return normalize_strain_name(candidate)


def infer_brand(item: Any, strain: str, sku_type: str) -> str:
    text = str(item or "")
    if strain == "Diamond Bar" and re.search(r"salted\s+caramel", text, re.I):
        return "Cookies"
    if re.search(r"royal\s+smalls?", text, re.I):
        return "Royal Smalls"
    if re.search(r"craft\s+kings?", text, re.I):
        return "Craft Kings"
    if re.search(r"\bclade\s*9\b", text, re.I):
        return "Clade9"
    if re.search(r"\bwet\s+(?:badder|diamonds?)\b", text, re.I):
        return "Locals Only"
    if str(item or "").strip().lower().startswith("precious"):
        return "Precious"
    if strain in BLEND_PATTERNS or strain in CRAFT_KINGS_STRAIN_PATTERNS:
        return "Craft Kings"
    if "Infused Pre-Roll" in sku_type or sku_type == "Edibles":
        return "Craft Kings"
    if strain in CLADE9_STRAIN_PATTERNS:
        return "Clade9"
    return "Brand Needs Review"


def is_finished_cpg(row: pd.Series) -> bool:
    item = str(row.get("item", "") or "").lower()
    category = str(row.get("item_category", "") or "").lower()
    return contains_text(
        item,
        ["packaged", "pre-roll", "preroll", "pre roll", "vape", "cartridge", "disposable", "gumm", "edible", "chocolate"],
    ) or contains_text(category, ["packaged", "raw pre-roll", "concentrate (each)"])


def transfer_unit_weight(row: pd.Series) -> float | None:
    reported = pd.to_numeric(row.get("unit_weight_grams"), errors="coerce")
    if pd.notna(reported) and reported > 0:
        return float(reported)
    if row.get("sku_type") in SKU_WEIGHT_GRAMS:
        return SKU_WEIGHT_GRAMS[row["sku_type"]]
    match = re.search(r"(?<![\d.])(0\.5|1|2|3\.5|7|14|28)\s*g(?:s)?\b", str(row.get("item", "")), re.I)
    return float(match.group(1)) if match else None


def transfer_units(row: pd.Series) -> float:
    count = pd.to_numeric(row.get("count_shipped"), errors="coerce")
    if pd.notna(count):
        count = float(count)
        if math.isfinite(count) and count > 0:
            return count
    shipped = pd.to_numeric(row.get("actual_shipped"), errors="coerce")
    if pd.isna(shipped):
        return 0.0
    shipped = float(shipped)
    if not math.isfinite(shipped) or shipped <= 0:
        return 0.0
    uom = str(row.get("actual_shipped_uom", "") or "").strip().lower()
    if uom in {"ea", "each"}:
        return shipped
    weight = pd.to_numeric(
        row.get("planning_unit_weight_grams"), errors="coerce"
    )
    if pd.isna(weight):
        return 0.0
    weight = float(weight)
    if not math.isfinite(weight) or weight <= 0:
        return 0.0
    conversion = {
        "g": 1.0, "gram": 1.0, "grams": 1.0,
        "oz": 28.349523125, "ounce": 28.349523125, "ounces": 28.349523125,
        "lb": GRAMS_PER_POUND, "pound": GRAMS_PER_POUND, "pounds": GRAMS_PER_POUND,
    }
    if uom not in conversion:
        return 0.0
    units = shipped * conversion[uom] / weight
    if not math.isfinite(units):
        return 0.0
    rounded = round(units)
    return float(rounded) if abs(units - rounded) < 0.001 else units


def prepare_transfer_analysis(transfers: pd.DataFrame) -> pd.DataFrame:
    """Normalize stored transfers using the Version 81.2 attribution order."""
    if transfers.empty:
        return transfers.copy()
    data = transfers.copy()
    for column in [
        "shipper_dollar_amount", "actual_shipped", "count_shipped",
        "unit_weight_grams",
    ]:
        data[column] = pd.to_numeric(data.get(column), errors="coerce")
    data["created_at"] = pd.to_datetime(data["created_at"], errors="coerce")
    data["item_key"] = data["item"].apply(canonicalize_transfer_item)
    data["sku_type"] = data.apply(classify_sku_type, axis=1)
    data["strain"] = data["item"].apply(infer_strain)
    data["brand"] = data.apply(
        lambda row: infer_brand(
            row.get("item"), row["strain"], row["sku_type"]
        ),
        axis=1,
    )
    data["is_finished_cpg"] = data.apply(is_finished_cpg, axis=1)
    data["brand_attribution_reason"] = "Established product rule"

    item_text = data["item"].fillna("").astype(str)
    royal_smalls = item_text.str.contains(
        r"royal\s+smalls?", case=False, regex=True
    )
    craft_kings = item_text.str.contains(
        r"craft\s+kings?", case=False, regex=True
    )
    infused = item_text.str.contains(
        r"infused\s+pre[- ]?roll", case=False, regex=True
    )
    clade9 = item_text.str.contains(
        r"\bclade\s*9\b", case=False, regex=True
    )
    precious = item_text.str.contains(
        r"^\s*precious\b", case=False, regex=True
    )
    data.loc[royal_smalls, "brand"] = "Royal Smalls"
    data.loc[craft_kings | infused, "brand"] = "Craft Kings"
    data.loc[clade9, "brand"] = "Clade9"
    data.loc[precious, "brand"] = "Precious"

    blend_mask = pd.Series(False, index=data.index)
    for strain, pattern in BLEND_PATTERNS.items():
        match = (
            data["is_finished_cpg"]
            & data["sku_type"].eq("1g Pre-Roll")
            & item_text.str.contains(pattern, case=False, regex=True)
        )
        blend_mask |= match
        data.loc[match, "strain"] = strain
    data.loc[blend_mask, "brand"] = "Craft Kings"

    for strain, pattern in CLADE9_STRAIN_PATTERNS.items():
        match = data["is_finished_cpg"] & item_text.str.contains(
            pattern, case=False, regex=True
        )
        data.loc[match, "strain"] = strain
    normalized_strain = (
        data["strain"].fillna("").astype(str).str.lower().str.strip()
    )
    data.loc[
        data["is_finished_cpg"]
        & normalized_strain.isin(
            {strain.lower() for strain in CLADE9_STRAIN_PATTERNS}
        ),
        "brand",
    ] = "Clade9"

    for strain, pattern in CRAFT_KINGS_STRAIN_PATTERNS.items():
        match = (
            data["is_finished_cpg"]
            & ~royal_smalls
            & item_text.str.contains(pattern, case=False, regex=True)
        )
        data.loc[match, "strain"] = strain
        data.loc[match, "brand"] = "Craft Kings"

    hybrid_tag = (
        data["package_tag"].fillna("").astype(str).str.strip().isin(
            CRAFT_KINGS_HYBRID_BLEND_PACKAGE_TAGS
        )
    )
    data.loc[hybrid_tag, "strain"] = "Hybrid Blend"
    data.loc[hybrid_tag, "brand"] = "Craft Kings"
    edible = data["is_finished_cpg"] & data["sku_type"].eq("Edibles")
    data.loc[edible, "brand"] = "Craft Kings"

    live_rosin_tag = (
        data["package_tag"].fillna("").astype(str).str.strip().isin(
            CLADE9_1G_LIVE_ROSIN_PACKAGE_TAGS
        )
    )
    data.loc[live_rosin_tag, "sku_type"] = "1g Live Rosin"
    data.loc[live_rosin_tag, "brand"] = "Clade9"

    locals_only = (
        data["is_finished_cpg"]
        & item_text.str.contains(
            r"\bwet\s+(?:badder|diamonds?)\b", case=False, regex=True
        )
    )
    data.loc[locals_only, "brand"] = "Locals Only"
    salted_caramel = item_text.str.contains(
        r"salted\s+caramel", case=False, regex=True
    )
    data.loc[salted_caramel, "strain"] = "Diamond Bar"
    data.loc[salted_caramel, "brand"] = "Cookies"

    data["planning_unit_weight_grams"] = data.apply(transfer_unit_weight, axis=1)
    data["shipped_units"] = data.apply(transfer_units, axis=1)
    data["shipment_month"] = data["created_at"].dt.strftime("%Y-%m")
    data["received_at"] = pd.to_datetime(
        data.get("received_at"), errors="coerce"
    )
    data["transit_hours"] = (
        data["received_at"] - data["created_at"]
    ).dt.total_seconds() / 3600
    voided = data["voided"].fillna(0).astype(str).str.lower().isin({"1", "true", "yes"})
    qcc_outbound = data["origin_facility"].eq(QCC_ORIGIN_FACILITY)
    retail_wholesale = (
        data["transfer_type"].eq("Wholesale Transfer")
        & data["destination_facility_type"].fillna("").str.contains(
            "Retailer", case=False
        )
    )
    data["is_demand"] = (
        qcc_outbound
        & retail_wholesale
        & data["state"].eq("Accepted")
        & ~voided
        & data["is_finished_cpg"]
    )
    data["is_open_shipment"] = (
        qcc_outbound & data["state"].eq("Shipped") & ~voided
    )
    data["is_shipment_exception"] = (
        qcc_outbound
        & data["state"].isin(["Rejected", "Returned"])
        & ~voided
    )
    return data


def prepare_demand_frame(transfers: pd.DataFrame) -> pd.DataFrame:
    """Return only accepted finished-good retailer demand rows."""
    analysis = prepare_transfer_analysis(transfers)
    if analysis.empty:
        return analysis
    return analysis[analysis["is_demand"]].copy()
