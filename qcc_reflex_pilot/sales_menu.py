"""Buyer-facing sales menu, customer controls, ordering, and approvals."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import secrets
import smtplib
import threading
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, TypedDict
from urllib.request import Request, urlopen

import reflex as rx

try:
    import psycopg
except ImportError:  # pragma: no cover - demo mode remains usable without it.
    psycopg = None

from .auth import validate_app_session
from .data import (
    database_url,
    load_current_metrc_customers,
    load_latest_inventory_skus,
)


MENU_BRANDS = [
    "Clade9",
    "Craft Kings",
    "Royal Smalls",
    "Melt x Clade9",
    "Locals Only",
]
ORDER_STATUS_PENDING = "Pending Sales Approval"
ORDER_STATUS_APPROVED = "Approved"
ORDER_STATUS_DECLINED = "Declined"
MENU_EMAIL_TO_DEFAULT = "dave@clade9.com"

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


class MenuProductRow(TypedDict):
    product_id: str
    brand: str
    category: str
    package_size: str
    product_type: str
    strain: str
    thc_display: str
    terpene_display: str
    unit_price: float
    units_per_case: int
    available_cases: int
    notes: str
    cart_cases: int
    sold_out: bool


class MenuSizeGroup(TypedDict):
    package_size: str
    products: list[MenuProductRow]


def _clean_text(value: Any) -> str:
    return " ".join(
        str(value or "").replace("\ufffd", "").replace("%%", "%").split()
    )


def _money(value: Any) -> float:
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    try:
        return round(float(text), 2)
    except (TypeError, ValueError):
        return 0.0


def _package_size_sort(value: str) -> tuple[float, str]:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return (float(match.group()) if match else 9999.0, str(value).casefold())


def _section_identity(section: str) -> tuple[str, str]:
    lowered = section.casefold()
    if "royal smalls" in lowered:
        return "Royal Smalls", "Flower"
    if "melt x clade9 disposables" in lowered:
        return "Melt x Clade9", "Disposables"
    if "melt x clade9 rosin" in lowered:
        return "Melt x Clade9", "Concentrates"
    if "locals only" in lowered:
        return "Locals Only", "Concentrates"
    if "craft kings edibles" in lowered:
        return "Craft Kings", "Edibles"
    if "craft kings pre-roll" in lowered:
        return "Craft Kings", "Pre-Rolls"
    if "clade9 pre-roll" in lowered:
        return "Clade9", "Pre-Rolls"
    if "clade9 510" in lowered:
        return "Clade9", "Vape Cartridges"
    if "clade9 disposables" in lowered:
        return "Clade9", "Disposables"
    if "craft kings flower" in lowered:
        return "Craft Kings", "Flower"
    if "clade9 flower" in lowered:
        return "Clade9", "Flower"
    return "QCC", "Other"


def sales_menu_seed_products() -> list[dict[str, Any]]:
    """Normalize the supplied buyer workbook into stable product records."""
    source = Path(__file__).with_name("sales_menu_seed.csv")
    # The sales export is produced by desktop Excel and contains Windows-1252
    # punctuation/non-breaking spaces rather than guaranteed UTF-8 bytes.
    with source.open("r", encoding="cp1252", newline="") as stream:
        rows = list(csv.reader(stream))
    header_index = next(
        index for index, row in enumerate(rows)
        if len(row) > 2 and row[1:3] == ["Type", "Strain"]
    )
    products: list[dict[str, Any]] = []
    section = ""
    package_size = ""
    product_type = ""
    for source_index, raw in enumerate(rows[header_index + 1 :], start=1):
        row = list(raw) + [""] * max(0, 10 - len(raw))
        size_value = _clean_text(row[0])
        type_value = _clean_text(row[1])
        strain = _clean_text(row[2])
        price = _money(row[5])
        if not price:
            if type_value and not strain:
                section = type_value
                package_size = ""
                product_type = ""
            continue
        if size_value:
            package_size = size_value
        if type_value:
            product_type = type_value
        if not strain or not package_size or not product_type:
            continue
        brand, category = _section_identity(section)
        units_per_case = int(_money(row[6]))
        identity = "|".join(
            [brand, category, package_size, product_type, strain]
        ).casefold()
        product_id = "MENU-" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:16].upper()
        products.append(
            {
                "product_id": product_id,
                "brand": brand,
                "category": category,
                "package_size": package_size,
                "product_type": product_type,
                "strain": strain,
                "thc_display": _clean_text(row[3]),
                "terpene_display": _clean_text(row[4]),
                "unit_price": price,
                "units_per_case": units_per_case,
                "available_cases": 0,
                "is_active": True,
                "notes": _clean_text(row[9]),
                "sort_order": source_index,
            }
        )
    return products


def _access_code_hash(access_code: str) -> str:
    normalized = re.sub(r"\s+", "", str(access_code or "")).upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _inventory_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _menu_inventory_family(product: dict[str, Any]) -> str:
    category = _inventory_key(product.get("category"))
    if category == "flower":
        return "flower"
    if category == "prerolls":
        return "preroll"
    if category in {"vapecartridges", "disposables"}:
        return "vape"
    if category == "concentrates":
        return "concentrate"
    if category == "edibles":
        return "edible"
    return category


def _expected_inventory_identity(product: dict[str, Any]) -> tuple[str, str, str]:
    """Return the exact Metrc brand, strain, and SKU type for a menu row."""
    brand = str(product.get("brand", "") or "").strip()
    strain = str(product.get("strain", "") or "").strip()
    size = str(product.get("package_size", "") or "").strip()
    category = str(product.get("category", "") or "").strip()
    product_type = str(product.get("product_type", "") or "").casefold()

    if _inventory_key(strain) == "privatereserve":
        strain = "Private Reserve OG"
    if brand == "Craft Kings" and category == "Pre-Rolls":
        blend = _inventory_key(strain)
        if blend in {"indica", "hybrid", "sativa"}:
            strain = f"{strain} Blend"
        if size == "1g":
            if "ice water hash" in product_type:
                sku_type = "1g IWH Infused Pre-Roll"
            elif "cured resin" in product_type:
                sku_type = "1g Infused Pre-Roll"
            else:
                sku_type = "1g Pre-Roll"
        elif "ice water hash" in product_type:
            sku_type = "3.5g IWH Infused Pre-Rolls 5-Pack"
        else:
            sku_type = "3.5g Infused Pre-Rolls 5-Pack"
        return brand, strain, sku_type
    if brand == "Clade9" and category == "Pre-Rolls":
        return brand, strain, "1g Pre-Roll" if size == "1g" else "3.5g Pre-Rolls"
    if brand == "Clade9" and category in {"Vape Cartridges", "Disposables"}:
        return brand, strain, "1g Vape CR" if "cured resin" in product_type else "1g Vape DC"
    if brand == "Melt x Clade9" and "live rosin" in product_type:
        strain = re.sub(r"^Melt\s*x\s*Clade9\s+", "", strain, flags=re.IGNORECASE)
        return "Clade9", strain, "1g Live Rosin"
    if brand == "Craft Kings" and category == "Edibles":
        return brand, strain, "Edibles"
    if category == "Flower":
        if brand == "Royal Smalls" and size == "28g":
            return brand, strain, "28g Flower Smalls"
        return brand, strain, f"{size} Flower"
    return brand, strain, " ".join(part for part in (size, str(product.get("product_type", ""))) if part)


def _inventory_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(
        _inventory_key(row.get(field)) for field in ("brand", "strain", "sku_type")
    )


def _menu_sku_filter_label(product: dict[str, Any]) -> str:
    _, _, sku_type = _expected_inventory_identity(product)
    return sku_type or "Other SKU"


def match_menu_inventory(
    products: list[dict[str, Any]], inventory_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Match menu rows to the latest Metrc SKU summary conservatively.

    Brand, strain, package size, and product family must agree.  Additional
    product-type tokens break ties for infused pre-rolls and concentrates.  An
    ambiguous row is left for manual review instead of publishing a bad count.
    """
    results: list[dict[str, Any]] = []
    brand_aliases = {
        "meltxclade9": {"meltxclade9", "meltclade9"},
        "royalsmalls": {"royalsmalls", "craftkingsroyalsmalls"},
    }
    for product in products:
        expected = _inventory_identity(dict(zip(
            ("brand", "strain", "sku_type"), _expected_inventory_identity(product)
        )))
        exact = [row for row in inventory_rows if _inventory_identity(row) == expected]
        if exact:
            units = int(round(sum(float(row.get("on_hand_units", 0) or 0) for row in exact)))
            units_per_case = max(int(product.get("units_per_case", 0) or 0), 1)
            results.append({
                "product_id": product["product_id"], "metrc_on_hand_units": max(units, 0),
                "metrc_case_equivalent": max(units, 0) // units_per_case,
                "match_status": "Matched",
                "match_detail": ", ".join(sorted({str(row.get("sku_type", "")) for row in exact})),
                "matched_inventory_keys": [expected],
            })
            continue
        product_brand = _inventory_key(product.get("brand"))
        accepted_brands = brand_aliases.get(product_brand, {product_brand})
        strain = _inventory_key(product.get("strain"))
        size = _inventory_key(product.get("package_size"))
        family = _menu_inventory_family(product)
        type_tokens = {
            token for token in re.findall(r"[a-z0-9]+", str(product.get("product_type", "")).casefold())
            if token not in {"single", "pack", "mylar", "jars", "non", "infused"}
        }
        candidates: list[tuple[int, dict[str, Any]]] = []
        for row in inventory_rows:
            row_brand = _inventory_key(row.get("brand"))
            row_strain = _inventory_key(row.get("strain"))
            sku_key = _inventory_key(row.get("sku_type"))
            if row_brand not in accepted_brands or row_strain != strain:
                continue
            if size and size not in sku_key:
                continue
            family_match = (
                (family == "flower" and "flower" in sku_key)
                or (family == "preroll" and "preroll" in sku_key)
                or (family == "vape" and any(word in sku_key for word in ("vape", "cartridge", "disposable")))
                or (family == "concentrate" and any(word in sku_key for word in ("concentrate", "rosin", "badder", "diamond")))
                or (family == "edible" and any(word in sku_key for word in ("edible", "gumm")))
            )
            if not family_match:
                continue
            score = 10
            score += sum(1 for token in type_tokens if _inventory_key(token) in sku_key)
            candidates.append((score, row))
        if not candidates:
            results.append({
                "product_id": product["product_id"], "metrc_on_hand_units": 0,
                "metrc_case_equivalent": 0, "match_status": "No Metrc SKU match",
                "match_detail": "Use a manual override until the SKU mapping is available.",
                "matched_inventory_keys": [],
            })
            continue
        best_score = max(score for score, _ in candidates)
        best = [row for score, row in candidates if score == best_score]
        sku_names = {_inventory_key(row.get("sku_type")) for row in best}
        if len(sku_names) > 1:
            results.append({
                "product_id": product["product_id"], "metrc_on_hand_units": 0,
                "metrc_case_equivalent": 0, "match_status": "Multiple Metrc SKU matches",
                "match_detail": ", ".join(sorted({str(row.get("sku_type", "")) for row in best})),
                "matched_inventory_keys": [],
            })
            continue
        units = int(round(sum(float(row.get("on_hand_units", 0) or 0) for row in best)))
        units_per_case = max(int(product.get("units_per_case", 0) or 0), 1)
        results.append({
            "product_id": product["product_id"], "metrc_on_hand_units": max(units, 0),
            "metrc_case_equivalent": max(units, 0) // units_per_case,
            "match_status": "Matched",
            "match_detail": ", ".join(sorted({str(row.get("sku_type", "")) for row in best})),
            "matched_inventory_keys": [_inventory_identity(row) for row in best],
        })
    return results


def _discover_metrc_menu_products(
    cursor: Any,
    products: list[dict[str, Any]],
    inventory_rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> int:
    """Add high-confidence Metrc-only SKUs to admin as inactive review rows."""
    matched_keys = {
        tuple(key)
        for match in matches
        for key in match.get("matched_inventory_keys", [])
    }
    templates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for product in products:
        expected_brand, _, expected_sku = _expected_inventory_identity(product)
        templates.setdefault(
            (_inventory_key(expected_brand), _inventory_key(expected_sku)), []
        ).append(product)
    inserted = 0
    max_sort = max((int(row.get("sort_order", 0) or 0) for row in products), default=0)
    for row in inventory_rows:
        identity = _inventory_identity(row)
        units = float(row.get("on_hand_units", 0) or 0)
        if identity in matched_keys or units <= 1:
            continue
        candidates = templates.get((identity[0], identity[2]), [])
        if not candidates:
            continue
        signatures = {
            (
                str(item.get("category", "")), str(item.get("package_size", "")),
                str(item.get("product_type", "")), float(item.get("unit_price", 0) or 0),
                int(item.get("units_per_case", 0) or 0),
            )
            for item in candidates
        }
        if len(signatures) != 1:
            continue
        template = candidates[0]
        strain = str(row.get("strain", "") or "").strip()
        if _inventory_key(strain) == "lipsmackerz":
            strain = "Lipsmackerz"
        elif _inventory_key(strain) == "privatereserveog":
            strain = "Private Reserve"
        identity_text = "|".join([
            str(template.get("brand", "")), str(template.get("category", "")),
            str(template.get("package_size", "")), str(template.get("product_type", "")),
            strain,
        ]).casefold()
        product_id = "MENU-" + hashlib.sha256(identity_text.encode("utf-8")).hexdigest()[:16].upper()
        max_sort += 1
        cursor.execute(
            "INSERT INTO qcc_sales_menu_products (product_id, brand, category, "
            "package_size, product_type, strain, thc_display, terpene_display, "
            "unit_price, units_per_case, available_cases, is_active, notes, "
            "sort_order, inventory_match_status, inventory_match_detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, '', '', %s, %s, 0, FALSE, %s, %s, "
            "'Imported for review', %s) ON CONFLICT (product_id) DO NOTHING",
            (
                product_id, template.get("brand"), template.get("category"),
                template.get("package_size"), template.get("product_type"), strain,
                template.get("unit_price"), template.get("units_per_case"),
                "Discovered in Metrc inventory. Review before publishing to buyers.",
                max_sort, str(row.get("sku_type", "")),
            ),
        )
        inserted += int(cursor.rowcount == 1)
    return inserted


def _records(cursor: Any) -> list[dict[str, Any]]:
    if not cursor.description:
        return []
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _ensure_sales_menu_schema(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_sales_menu_products (
            product_id TEXT PRIMARY KEY,
            brand TEXT NOT NULL,
            category TEXT NOT NULL,
            package_size TEXT NOT NULL,
            product_type TEXT NOT NULL,
            strain TEXT NOT NULL,
            thc_display TEXT NOT NULL DEFAULT '',
            terpene_display TEXT NOT NULL DEFAULT '',
            unit_price NUMERIC(12, 2) NOT NULL,
            units_per_case INTEGER NOT NULL,
            available_cases INTEGER NOT NULL DEFAULT 0,
            metrc_on_hand_units INTEGER,
            metrc_case_equivalent INTEGER,
            manual_override_cases INTEGER,
            inventory_match_status TEXT NOT NULL DEFAULT 'Not synced',
            inventory_match_detail TEXT NOT NULL DEFAULT '',
            inventory_synced_at TIMESTAMPTZ,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            updated_by TEXT NOT NULL DEFAULT 'QCC Control Tower',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_sales_menu_customers (
            customer_id TEXT PRIMARY KEY,
            buyer_name TEXT NOT NULL,
            store_name TEXT NOT NULL,
            license_number TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            payment_terms TEXT NOT NULL DEFAULT '',
            assigned_salesperson TEXT NOT NULL DEFAULT '',
            minimum_order_cases INTEGER NOT NULL DEFAULT 0,
            allowed_brands JSONB NOT NULL DEFAULT '[]'::jsonb,
            access_code_hash TEXT UNIQUE,
            access_code_display TEXT NOT NULL DEFAULT '',
            source_system TEXT NOT NULL DEFAULT 'Manual',
            metrc_synced_at TIMESTAMPTZ,
            last_shipment TIMESTAMPTZ,
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        "ALTER TABLE qcc_sales_menu_products "
        "ADD COLUMN IF NOT EXISTS metrc_on_hand_units INTEGER, "
        "ADD COLUMN IF NOT EXISTS metrc_case_equivalent INTEGER, "
        "ADD COLUMN IF NOT EXISTS manual_override_cases INTEGER, "
        "ADD COLUMN IF NOT EXISTS inventory_match_status TEXT NOT NULL DEFAULT 'Not synced', "
        "ADD COLUMN IF NOT EXISTS inventory_match_detail TEXT NOT NULL DEFAULT '', "
        "ADD COLUMN IF NOT EXISTS inventory_synced_at TIMESTAMPTZ"
    )
    cursor.execute(
        "ALTER TABLE qcc_sales_menu_customers "
        "ALTER COLUMN access_code_hash DROP NOT NULL, "
        "ADD COLUMN IF NOT EXISTS access_code_display TEXT NOT NULL DEFAULT '', "
        "ADD COLUMN IF NOT EXISTS source_system TEXT NOT NULL DEFAULT 'Manual', "
        "ADD COLUMN IF NOT EXISTS metrc_synced_at TIMESTAMPTZ, "
        "ADD COLUMN IF NOT EXISTS last_shipment TIMESTAMPTZ"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_sales_menu_customer_prices (
            customer_id TEXT REFERENCES qcc_sales_menu_customers(customer_id)
                ON DELETE CASCADE,
            product_id TEXT REFERENCES qcc_sales_menu_products(product_id)
                ON DELETE CASCADE,
            unit_price NUMERIC(12, 2) NOT NULL,
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (customer_id, product_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_sales_menu_orders (
            order_id TEXT PRIMARY KEY,
            order_number TEXT NOT NULL UNIQUE,
            customer_id TEXT REFERENCES qcc_sales_menu_customers(customer_id),
            status TEXT NOT NULL,
            total_cases INTEGER NOT NULL,
            total_units INTEGER NOT NULL,
            total_amount NUMERIC(14, 2) NOT NULL,
            requested_delivery_date DATE,
            customer_notes TEXT NOT NULL DEFAULT '',
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ,
            reviewed_by TEXT NOT NULL DEFAULT ''
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS qcc_sales_menu_order_items (
            order_item_id TEXT PRIMARY KEY,
            order_id TEXT REFERENCES qcc_sales_menu_orders(order_id)
                ON DELETE CASCADE,
            product_id TEXT REFERENCES qcc_sales_menu_products(product_id),
            brand TEXT NOT NULL,
            package_size TEXT NOT NULL,
            product_type TEXT NOT NULL,
            strain TEXT NOT NULL,
            case_count INTEGER NOT NULL,
            units_per_case INTEGER NOT NULL,
            unit_price NUMERIC(12, 2) NOT NULL,
            line_total NUMERIC(14, 2) NOT NULL
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_qcc_menu_orders_customer "
        "ON qcc_sales_menu_orders(customer_id, submitted_at DESC)"
    )
    cursor.execute(
        "ALTER TABLE qcc_sales_menu_orders "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_qcc_menu_orders_status "
        "ON qcc_sales_menu_orders(status, submitted_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_qcc_menu_items_product "
        "ON qcc_sales_menu_order_items(product_id)"
    )


def ensure_sales_menu_schema() -> bool:
    """Create the menu schema and seed the supplied 93-SKU menu once."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    if psycopg is None or not database_url():
        return False
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return True
        with psycopg.connect(database_url(), connect_timeout=15) as connection:
            with connection.cursor() as cursor:
                _ensure_sales_menu_schema(cursor)
                cursor.execute("SELECT COUNT(*) FROM qcc_sales_menu_products")
                product_count = int(cursor.fetchone()[0])
                if not product_count:
                    for product in sales_menu_seed_products():
                        cursor.execute(
                            "INSERT INTO qcc_sales_menu_products ("
                            "product_id, brand, category, package_size, product_type, "
                            "strain, thc_display, terpene_display, unit_price, "
                            "units_per_case, available_cases, is_active, notes, sort_order"
                            ") VALUES (" + ", ".join(["%s"] * 14) + ") "
                            "ON CONFLICT (product_id) DO NOTHING",
                            (
                                product["product_id"], product["brand"],
                                product["category"], product["package_size"],
                                product["product_type"], product["strain"],
                                product["thc_display"], product["terpene_display"],
                                product["unit_price"], product["units_per_case"],
                                0, True, product["notes"], product["sort_order"],
                            ),
                        )
            connection.commit()
        _SCHEMA_READY = True
    return True


def _demo_customer() -> dict[str, Any]:
    return {
        "customer_id": "DEMO-CUSTOMER",
        "buyer_name": "Demo Buyer",
        "store_name": "QCC Menu Preview",
        "license_number": "",
        "email": "",
        "payment_terms": "Net 30",
        "assigned_salesperson": "QCC Sales",
        "minimum_order_cases": 1,
        "allowed_brands": list(MENU_BRANDS),
        "is_active": True,
    }


def authenticate_menu_customer(access_code: str) -> dict[str, Any]:
    code = re.sub(r"\s+", "", str(access_code or "")).upper()
    if not code:
        raise ValueError("Enter your buyer access code.")
    if not ensure_sales_menu_schema():
        if code == "DEMO2026":
            return _demo_customer()
        raise RuntimeError(
            "The buyer menu database is not connected. Use DEMO2026 for a local preview."
        )
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT customer_id, buyer_name, store_name, license_number, email, "
                "payment_terms, assigned_salesperson, minimum_order_cases, "
                "allowed_brands, is_active FROM qcc_sales_menu_customers "
                "WHERE access_code_hash = %s",
                (_access_code_hash(code),),
            )
            rows = _records(cursor)
    if not rows or not bool(rows[0].get("is_active")):
        raise ValueError("That buyer access code is not active.")
    customer = rows[0]
    customer["allowed_brands"] = list(customer.get("allowed_brands") or [])
    return customer


def load_customer_menu_products(customer: dict[str, Any]) -> list[dict[str, Any]]:
    """Return active products, customer pricing, and availability after holds."""
    if customer.get("customer_id") == "DEMO-CUSTOMER" or not ensure_sales_menu_schema():
        rows = sales_menu_seed_products()
        for row in rows:
            row["available_cases"] = 25
            row["base_unit_price"] = row["unit_price"]
            row["case_price"] = round(
                row["unit_price"] * row["units_per_case"], 2
            )
        return rows
    customer_id = str(customer.get("customer_id", ""))
    allowed = set(customer.get("allowed_brands") or MENU_BRANDS)
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.product_id, p.brand, p.category, p.package_size, "
                "p.product_type, p.strain, p.thc_display, p.terpene_display, "
                "p.unit_price AS base_unit_price, "
                "COALESCE(cp.unit_price, p.unit_price) AS unit_price, "
                "p.units_per_case, GREATEST(p.available_cases - COALESCE(("
                "SELECT SUM(oi.case_count) FROM qcc_sales_menu_order_items oi "
                "JOIN qcc_sales_menu_orders o ON o.order_id = oi.order_id "
                "WHERE oi.product_id = p.product_id AND o.status = %s"
                "), 0), 0) AS available_cases, p.notes, p.sort_order "
                "FROM qcc_sales_menu_products p LEFT JOIN "
                "qcc_sales_menu_customer_prices cp ON cp.product_id = p.product_id "
                "AND cp.customer_id = %s WHERE p.is_active = TRUE "
                "ORDER BY p.sort_order, p.brand, p.strain",
                (ORDER_STATUS_PENDING, customer_id),
            )
            rows = _records(cursor)
    result: list[dict[str, Any]] = []
    for row in rows:
        if allowed and str(row.get("brand", "")) not in allowed:
            continue
        record = dict(row)
        record["unit_price"] = float(record.get("unit_price", 0) or 0)
        record["base_unit_price"] = float(
            record.get("base_unit_price", 0) or 0
        )
        record["units_per_case"] = int(record.get("units_per_case", 0) or 0)
        record["available_cases"] = int(record.get("available_cases", 0) or 0)
        record["case_price"] = round(
            record["unit_price"] * record["units_per_case"], 2
        )
        result.append(record)
    return result


def _order_summary(order_id: str) -> dict[str, Any]:
    if not ensure_sales_menu_schema():
        return {}
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT o.order_id, o.order_number, o.status, o.total_cases, "
                "o.total_units, o.total_amount, o.requested_delivery_date, "
                "o.customer_notes, o.submitted_at, o.reviewed_at, o.reviewed_by, "
                "c.buyer_name, c.store_name, c.license_number, c.email, "
                "c.payment_terms, c.assigned_salesperson FROM qcc_sales_menu_orders o "
                "JOIN qcc_sales_menu_customers c ON c.customer_id = o.customer_id "
                "WHERE o.order_id = %s",
                (order_id,),
            )
            orders = _records(cursor)
            cursor.execute(
                "SELECT order_item_id, product_id, brand, package_size, "
                "product_type, strain, case_count, "
                "units_per_case, unit_price, line_total FROM qcc_sales_menu_order_items "
                "WHERE order_id = %s ORDER BY brand, product_type, strain",
                (order_id,),
            )
            items = _records(cursor)
    if not orders:
        return {}
    order = dict(orders[0])
    order["total_amount"] = float(order.get("total_amount", 0) or 0)
    order["items"] = [
        {
            **item,
            "case_count": int(item.get("case_count", 0) or 0),
            "units_per_case": int(item.get("units_per_case", 0) or 0),
            "unit_price": float(item.get("unit_price", 0) or 0),
            "line_total": float(item.get("line_total", 0) or 0),
        }
        for item in items
    ]
    return order


def send_order_email(order: dict[str, Any], event_label: str) -> tuple[bool, str]:
    """Send one branded order summary through Google SMTP or Resend."""
    smtp_host = os.getenv("QCC_MENU_SMTP_HOST", "").strip()
    smtp_port_value = os.getenv("QCC_MENU_SMTP_PORT", "587").strip() or "587"
    smtp_username = os.getenv("QCC_MENU_SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("QCC_MENU_SMTP_APP_PASSWORD", "").replace(" ", "")
    api_key = (
        os.getenv("QCC_MENU_RESEND_API_KEY", "").strip()
        or os.getenv("RESEND_API_KEY", "").strip()
    )
    smtp_configured = bool(smtp_host and smtp_username and smtp_password)
    if not smtp_configured and not api_key:
        return False, "Order saved; email delivery is not configured yet."
    recipient_value = os.getenv(
        "QCC_MENU_ORDER_EMAIL_TO", MENU_EMAIL_TO_DEFAULT
    ).strip() or MENU_EMAIL_TO_DEFAULT
    sender = os.getenv(
        "QCC_MENU_EMAIL_FROM", "QCC Orders <orders@updates.clade9.com>"
    ).strip()
    item_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('brand', '')))}</td>"
        f"<td>{html.escape(str(item.get('package_size', '')))} "
        f"{html.escape(str(item.get('product_type', '')))}</td>"
        f"<td>{html.escape(str(item.get('strain', '')))}</td>"
        f"<td style='text-align:right'>{int(item.get('case_count', 0))}</td>"
        f"<td style='text-align:right'>${float(item.get('line_total', 0)) :,.2f}</td>"
        "</tr>"
        for item in order.get("items", [])
    )
    body = (
        "<div style='font-family:Arial,sans-serif;color:#171717;max-width:760px'>"
        "<div style='background:#111;color:#fff;padding:24px'>"
        "<h1 style='margin:0'>QCC Buyer Order</h1>"
        f"<p style='margin:8px 0 0'>{html.escape(event_label)}</p></div>"
        f"<h2>{html.escape(str(order.get('order_number', '')))}</h2>"
        f"<p><b>Customer:</b> {html.escape(str(order.get('store_name', '')))} - "
        f"{html.escape(str(order.get('buyer_name', '')))}<br>"
        f"<b>Status:</b> {html.escape(str(order.get('status', '')))}<br>"
        f"<b>Requested delivery:</b> {html.escape(str(order.get('requested_delivery_date') or 'Not specified'))}<br>"
        f"<b>Payment terms:</b> {html.escape(str(order.get('payment_terms', '')))}</p>"
        "<table style='border-collapse:collapse;width:100%' border='1' cellpadding='8'>"
        "<thead style='background:#eee'><tr><th>Brand</th><th>Product</th>"
        "<th>Strain</th><th>Cases</th><th>Line Total</th></tr></thead>"
        f"<tbody>{item_rows}</tbody></table>"
        f"<h3>Total: {int(order.get('total_cases', 0))} cases / "
        f"{int(order.get('total_units', 0)):,} units / "
        f"${float(order.get('total_amount', 0)):,.2f}</h3>"
        f"<p><b>Notes:</b> {html.escape(str(order.get('customer_notes', '') or 'None'))}</p>"
        "</div>"
    )
    recipients = [
        address.strip() for address in re.split(r"[,;]", recipient_value)
        if address.strip()
    ]
    buyer_email = str(order.get("email", "") or "").strip()
    if buyer_email and buyer_email.casefold() not in {
        address.casefold() for address in recipients
    }:
        recipients.append(buyer_email)
    subject = f"{event_label}: {order.get('order_number', 'QCC order')}"
    if smtp_configured:
        try:
            smtp_port = int(smtp_port_value)
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = sender
            message["To"] = ", ".join(recipients)
            message.set_content(
                f"{event_label}: {order.get('order_number', 'QCC order')}\n"
                f"Customer: {order.get('store_name', '')}\n"
                f"Total: {int(order.get('total_cases', 0))} cases / "
                f"{int(order.get('total_units', 0)):,} units / "
                f"${float(order.get('total_amount', 0)):,.2f}"
            )
            message.add_alternative(body, subtype="html")
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(message)
            return True, "Email summary sent through Google Workspace."
        except Exception as error:  # Order persistence must survive email outages.
            if not api_key:
                return False, f"Order saved; email could not be sent: {error}"

    payload = json.dumps(
        {
            "from": sender,
            "to": recipients,
            "subject": subject,
            "html": body,
        }
    ).encode("utf-8")
    request = Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            response.read()
        return True, "Email summary sent."
    except Exception as error:  # Order persistence must survive email outages.
        return False, f"Order saved; email could not be sent: {error}"


def submit_menu_order(
    customer: dict[str, Any],
    cart: dict[str, int],
    requested_delivery_date: str,
    notes: str,
) -> dict[str, Any]:
    if customer.get("customer_id") == "DEMO-CUSTOMER":
        return {
            "order_id": "DEMO-ORDER",
            "order_number": "DEMO-PREVIEW",
            "email_message": "Demo order preview created; nothing was saved or emailed.",
        }
    ensure_sales_menu_schema()
    requested = {
        str(product_id): int(case_count)
        for product_id, case_count in cart.items()
        if int(case_count or 0) > 0
    }
    if not requested:
        raise ValueError("Add at least one case before submitting your order.")
    customer_id = str(customer.get("customer_id", ""))
    order_id = "QCC-MENU-" + secrets.token_hex(12).upper()
    order_number = (
        "QCC-WEB-" + datetime.now().strftime("%Y%m%d-")
        + secrets.token_hex(3).upper()
    )
    items: list[dict[str, Any]] = []
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_sales_menu_schema(cursor)
            cursor.execute(
                "SELECT allowed_brands, minimum_order_cases FROM "
                "qcc_sales_menu_customers WHERE customer_id = %s AND is_active = TRUE "
                "FOR UPDATE",
                (customer_id,),
            )
            customer_row = cursor.fetchone()
            if not customer_row:
                raise ValueError("Your buyer access is no longer active.")
            allowed = set(customer_row[0] or MENU_BRANDS)
            minimum_cases = int(customer_row[1] or 0)
            for product_id, case_count in requested.items():
                cursor.execute(
                    "SELECT product_id, brand, package_size, product_type, strain, "
                    "unit_price, units_per_case, available_cases FROM "
                    "qcc_sales_menu_products WHERE product_id = %s AND is_active = TRUE "
                    "FOR UPDATE",
                    (product_id,),
                )
                product = cursor.fetchone()
                if not product:
                    raise ValueError("One selected product is no longer available.")
                if allowed and product[1] not in allowed:
                    raise ValueError("One selected product is not available to this account.")
                cursor.execute(
                    "SELECT COALESCE(SUM(oi.case_count), 0) FROM "
                    "qcc_sales_menu_order_items oi JOIN qcc_sales_menu_orders o "
                    "ON o.order_id = oi.order_id WHERE oi.product_id = %s "
                    "AND o.status = %s",
                    (product_id, ORDER_STATUS_PENDING),
                )
                held_cases = int(cursor.fetchone()[0] or 0)
                available = max(int(product[7] or 0) - held_cases, 0)
                if case_count > available:
                    raise ValueError(
                        f"{product[1]} {product[4]} now has only {available} cases available."
                    )
                cursor.execute(
                    "SELECT unit_price FROM qcc_sales_menu_customer_prices "
                    "WHERE customer_id = %s AND product_id = %s",
                    (customer_id, product_id),
                )
                override = cursor.fetchone()
                unit_price = float(override[0] if override else product[5])
                units_per_case = int(product[6])
                line_total = round(case_count * units_per_case * unit_price, 2)
                items.append(
                    {
                        "product_id": product[0], "brand": product[1],
                        "package_size": product[2], "product_type": product[3],
                        "strain": product[4], "unit_price": unit_price,
                        "units_per_case": units_per_case,
                        "case_count": case_count, "line_total": line_total,
                    }
                )
            total_cases = sum(item["case_count"] for item in items)
            if total_cases < minimum_cases:
                raise ValueError(
                    f"This account requires at least {minimum_cases} cases per order."
                )
            total_units = sum(
                item["case_count"] * item["units_per_case"] for item in items
            )
            total_amount = round(sum(item["line_total"] for item in items), 2)
            delivery_value = requested_delivery_date.strip() or None
            cursor.execute(
                "INSERT INTO qcc_sales_menu_orders (order_id, order_number, "
                "customer_id, status, total_cases, total_units, total_amount, "
                "requested_delivery_date, customer_notes) VALUES ("
                + ", ".join(["%s"] * 9) + ")",
                (
                    order_id, order_number, customer_id, ORDER_STATUS_PENDING,
                    total_cases, total_units, total_amount, delivery_value,
                    str(notes or "").strip(),
                ),
            )
            for item in items:
                cursor.execute(
                    "INSERT INTO qcc_sales_menu_order_items (order_item_id, order_id, "
                    "product_id, brand, package_size, product_type, strain, "
                    "case_count, units_per_case, unit_price, line_total) VALUES ("
                    + ", ".join(["%s"] * 11) + ")",
                    (
                        "QCC-MENU-ITEM-" + secrets.token_hex(10).upper(),
                        order_id, item["product_id"], item["brand"],
                        item["package_size"], item["product_type"], item["strain"],
                        item["case_count"], item["units_per_case"],
                        item["unit_price"], item["line_total"],
                    ),
                )
        connection.commit()
    order = _order_summary(order_id)
    _, email_message = send_order_email(order, "New order awaiting sales approval")
    return {
        "order_id": order_id,
        "order_number": order_number,
        "email_message": email_message,
    }


def load_menu_admin_data() -> dict[str, Any]:
    if not ensure_sales_menu_schema():
        products = sales_menu_seed_products()
        for product in products:
            product.update({
                "held_cases": 0,
                "available_to_order": int(product.get("available_cases", 0) or 0),
                "metrc_on_hand_units": 0,
                "metrc_case_equivalent": 0,
                "manual_override_cases": None,
                "manual_override_display": "",
                "inventory_match_status": "Preview mode",
                "inventory_match_detail": "Connect Supabase to load Metrc inventory.",
                "inventory_source": "Metrc snapshot",
                "sku_filter_label": _menu_sku_filter_label(product),
            })
        return {
            "database_ready": False,
            "products": products,
            "customers": [],
            "orders": [],
        }
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.product_id, p.brand, p.category, p.package_size, "
                "p.product_type, p.strain, p.thc_display, p.terpene_display, "
                "p.unit_price, p.units_per_case, p.available_cases, p.is_active, "
                "p.metrc_on_hand_units, p.metrc_case_equivalent, "
                "p.manual_override_cases, p.inventory_match_status, "
                "p.inventory_match_detail, p.inventory_synced_at, "
                "p.notes, p.sort_order, COALESCE((SELECT SUM(oi.case_count) "
                "FROM qcc_sales_menu_order_items oi JOIN qcc_sales_menu_orders o "
                "ON o.order_id = oi.order_id WHERE oi.product_id = p.product_id "
                "AND o.status = %s), 0) AS held_cases FROM qcc_sales_menu_products p "
                "ORDER BY p.sort_order, p.brand, p.strain",
                (ORDER_STATUS_PENDING,),
            )
            products = _records(cursor)
            cursor.execute(
                "SELECT customer_id, buyer_name, store_name, license_number, email, "
                "payment_terms, assigned_salesperson, minimum_order_cases, "
                "allowed_brands, is_active, source_system, metrc_synced_at, "
                "last_shipment, access_code_display, "
                "(access_code_hash IS NOT NULL) AS has_access_code, "
                "created_at FROM qcc_sales_menu_customers "
                "ORDER BY store_name, buyer_name"
            )
            customers = _records(cursor)
            cursor.execute(
            "SELECT o.order_id, o.order_number, o.status, o.total_cases, "
                "o.total_units, o.total_amount, o.requested_delivery_date, "
                "o.customer_notes, o.submitted_at, o.reviewed_at, o.reviewed_by, c.store_name, "
                "c.buyer_name, c.assigned_salesperson FROM qcc_sales_menu_orders o "
                "JOIN qcc_sales_menu_customers c ON c.customer_id = o.customer_id "
                "ORDER BY CASE WHEN o.status = %s THEN 0 ELSE 1 END, o.submitted_at DESC "
                "LIMIT 250",
                (ORDER_STATUS_PENDING,),
            )
            orders = _records(cursor)
    for product in products:
        product["unit_price"] = float(product.get("unit_price", 0) or 0)
        product["available_cases"] = int(product.get("available_cases", 0) or 0)
        product["metrc_on_hand_units"] = int(product.get("metrc_on_hand_units", 0) or 0)
        product["metrc_case_equivalent"] = int(product.get("metrc_case_equivalent", 0) or 0)
        product["manual_override_display"] = (
            "" if product.get("manual_override_cases") is None
            else str(int(product.get("manual_override_cases", 0) or 0))
        )
        product["inventory_source"] = (
            "Manual override" if product.get("manual_override_cases") is not None
            else "Metrc snapshot"
        )
        product["sku_filter_label"] = _menu_sku_filter_label(product)
        product["held_cases"] = int(product.get("held_cases", 0) or 0)
        product["available_to_order"] = max(
            product["available_cases"] - product["held_cases"], 0
        )
    for customer in customers:
        customer["allowed_brands"] = list(customer.get("allowed_brands") or [])
        customer["allowed_brands_display"] = ", ".join(customer["allowed_brands"])
    for order in orders:
        order["total_amount"] = float(order.get("total_amount", 0) or 0)
        order["total_amount_display"] = f"${order['total_amount']:,.2f}"
        order["total_units_display"] = f"{int(order.get('total_units', 0) or 0):,}"
        order["requested_delivery_display"] = str(
            order.get("requested_delivery_date") or "Not specified"
        )
    return {
        "database_ready": True,
        "products": products,
        "customers": customers,
        "orders": orders,
    }


def update_menu_availability(
    case_counts: dict[str, int], *, updated_by: str
) -> None:
    ensure_sales_menu_schema()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            for product_id, cases in case_counts.items():
                cursor.execute(
                    "UPDATE qcc_sales_menu_products SET available_cases = %s, "
                    "manual_override_cases = %s, "
                    "updated_by = %s, updated_at = NOW() WHERE product_id = %s",
                    (max(int(cases), 0), max(int(cases), 0), updated_by, product_id),
                )
        connection.commit()


def refresh_menu_inventory_from_metrc(*, updated_by: str) -> dict[str, Any]:
    """Publish full-case menu quantities from the latest inventory snapshot."""
    ensure_sales_menu_schema()
    snapshot, frame = load_latest_inventory_skus()
    if not snapshot:
        raise ValueError("No published Metrc inventory snapshot is available.")
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT product_id, brand, category, package_size, product_type, "
                "strain, unit_price, units_per_case, sort_order "
                "FROM qcc_sales_menu_products"
            )
            products = _records(cursor)
            inventory_rows = frame.to_dict("records")
            matches = match_menu_inventory(products, inventory_rows)
            discovered = _discover_metrc_menu_products(
                cursor, products, inventory_rows, matches
            )
            if discovered:
                cursor.execute(
                    "SELECT product_id, brand, category, package_size, product_type, "
                    "strain, unit_price, units_per_case, sort_order "
                    "FROM qcc_sales_menu_products"
                )
                products = _records(cursor)
                matches = match_menu_inventory(products, inventory_rows)
            for match in matches:
                cursor.execute(
                    "UPDATE qcc_sales_menu_products SET metrc_on_hand_units = %s, "
                    "metrc_case_equivalent = %s, inventory_match_status = %s, "
                    "inventory_match_detail = %s, inventory_synced_at = NOW(), "
                    "available_cases = CASE WHEN manual_override_cases IS NULL "
                    "THEN %s ELSE manual_override_cases END, updated_by = %s, "
                    "updated_at = NOW() WHERE product_id = %s",
                    (
                        match["metrc_on_hand_units"], match["metrc_case_equivalent"],
                        match["match_status"], match["match_detail"],
                        match["metrc_case_equivalent"], updated_by, match["product_id"],
                    ),
                )
        connection.commit()
    return {
        "snapshot_date": str(snapshot.get("business_date") or snapshot.get("published_at") or ""),
        "matched": sum(1 for row in matches if row["match_status"] == "Matched"),
        "review": sum(1 for row in matches if row["match_status"] != "Matched"),
        "discovered": discovered,
    }


def clear_menu_inventory_override(product_id: str, *, updated_by: str) -> None:
    ensure_sales_menu_schema()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE qcc_sales_menu_products SET manual_override_cases = NULL, "
                "available_cases = COALESCE(metrc_case_equivalent, 0), "
                "updated_by = %s, updated_at = NOW() WHERE product_id = %s",
                (updated_by, product_id),
            )
        connection.commit()


def sync_menu_customers_from_metrc() -> int:
    """Upsert transfer-derived Metrc retailers as inactive buyer accounts."""
    ensure_sales_menu_schema()
    frame = load_current_metrc_customers()
    rows = frame.to_dict("records")
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            for row in rows:
                license_number = str(row.get("license_number", "") or "").strip()
                if not license_number:
                    continue
                customer_id = "METRC-" + hashlib.sha256(
                    license_number.upper().encode("utf-8")
                ).hexdigest()[:18].upper()
                store_name = str(row.get("store_name", "") or license_number).strip()
                cursor.execute(
                    "INSERT INTO qcc_sales_menu_customers (customer_id, buyer_name, "
                    "store_name, license_number, allowed_brands, access_code_hash, "
                    "source_system, metrc_synced_at, last_shipment, is_active) "
                    "VALUES (%s, '', %s, %s, %s::jsonb, NULL, 'Metrc Transfers', "
                    "NOW(), %s, FALSE) ON CONFLICT (customer_id) DO UPDATE SET "
                    "store_name = CASE WHEN qcc_sales_menu_customers.is_active "
                    "THEN qcc_sales_menu_customers.store_name ELSE EXCLUDED.store_name END, "
                    "license_number = EXCLUDED.license_number, "
                    "source_system = 'Metrc Transfers', metrc_synced_at = NOW(), "
                    "last_shipment = EXCLUDED.last_shipment, updated_at = NOW()",
                    (
                        customer_id, store_name, license_number,
                        json.dumps(MENU_BRANDS), row.get("last_shipment"),
                    ),
                )
        connection.commit()
    return len(rows)


def activate_menu_customer(
    customer_id: str, *, buyer_name: str, store_name: str, email: str,
    payment_terms: str,
    assigned_salesperson: str, minimum_order_cases: int,
    allowed_brands: list[str], access_code: str, is_active: bool = True,
) -> None:
    if not buyer_name.strip():
        raise ValueError("Enter the buyer name for this account.")
    ensure_sales_menu_schema()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_code_hash FROM qcc_sales_menu_customers "
                "WHERE customer_id = %s",
                (customer_id,),
            )
            current = cursor.fetchone()
            if not current:
                raise ValueError("Select a Metrc customer to edit.")
            normalized_code = re.sub(r"\s+", "", access_code)
            if normalized_code and len(normalized_code) < 6:
                raise ValueError("Use an access code with at least six characters.")
            if is_active and not normalized_code and not current[0]:
                raise ValueError("Assign an access code before activating this buyer.")
            cursor.execute(
                "UPDATE qcc_sales_menu_customers SET buyer_name = %s, store_name = %s, "
                "email = %s, "
                "payment_terms = %s, assigned_salesperson = %s, "
                "minimum_order_cases = %s, allowed_brands = %s::jsonb, "
                "access_code_hash = CASE WHEN %s = '' THEN access_code_hash ELSE %s END, "
                "access_code_display = CASE WHEN %s = '' THEN access_code_display ELSE %s END, "
                "is_active = %s, updated_at = NOW() "
                "WHERE customer_id = %s",
                (
                    buyer_name.strip(), store_name.strip(), email.strip(), payment_terms.strip(),
                    assigned_salesperson.strip(), max(int(minimum_order_cases), 0),
                    json.dumps(allowed_brands or MENU_BRANDS),
                    normalized_code,
                    _access_code_hash(normalized_code) if normalized_code else "",
                    normalized_code, normalized_code, bool(is_active), customer_id,
                ),
            )
        connection.commit()


def create_menu_customer(
    *, buyer_name: str, store_name: str, license_number: str, email: str,
    payment_terms: str, assigned_salesperson: str, minimum_order_cases: int,
    allowed_brands: list[str], access_code: str,
) -> str:
    ensure_sales_menu_schema()
    if not buyer_name.strip() or not store_name.strip():
        raise ValueError("Buyer name and store name are required.")
    if len(re.sub(r"\s+", "", access_code)) < 6:
        raise ValueError("Use an access code with at least six characters.")
    customer_id = "QCC-CUSTOMER-" + secrets.token_hex(8).upper()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            _ensure_sales_menu_schema(cursor)
            cursor.execute(
                "INSERT INTO qcc_sales_menu_customers (customer_id, buyer_name, "
                "store_name, license_number, email, payment_terms, "
                "assigned_salesperson, minimum_order_cases, allowed_brands, "
                "access_code_hash, access_code_display, source_system, is_active) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, "
                "'Manual', TRUE)",
                (
                    customer_id, buyer_name.strip(), store_name.strip(),
                    license_number.strip(), email.strip(), payment_terms.strip(),
                    assigned_salesperson.strip(), max(int(minimum_order_cases), 0),
                    json.dumps(allowed_brands or MENU_BRANDS),
                    _access_code_hash(access_code),
                    re.sub(r"\s+", "", access_code),
                ),
            )
        connection.commit()
    return customer_id


def save_customer_price(
    customer_id: str, product_id: str, unit_price: float, *, updated_by: str
) -> None:
    ensure_sales_menu_schema()
    if unit_price <= 0:
        raise ValueError("Customer price must be greater than zero.")
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO qcc_sales_menu_customer_prices (customer_id, product_id, "
                "unit_price, updated_by) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (customer_id, product_id) DO UPDATE SET "
                "unit_price = EXCLUDED.unit_price, updated_by = EXCLUDED.updated_by, "
                "updated_at = NOW()",
                (customer_id, product_id, unit_price, updated_by),
            )
        connection.commit()


def update_pending_menu_order(
    order_id: str,
    case_counts: dict[str, int],
    *,
    requested_delivery_date: str,
    customer_notes: str,
    updated_by: str,
) -> dict[str, Any]:
    """Edit a pending order and recalculate all order totals atomically."""
    ensure_sales_menu_schema()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM qcc_sales_menu_orders WHERE order_id = %s FOR UPDATE",
                (order_id,),
            )
            order_row = cursor.fetchone()
            if not order_row:
                raise ValueError("Order not found.")
            if order_row[0] != ORDER_STATUS_PENDING:
                raise ValueError("Only pending orders can be edited.")
            cursor.execute(
                "SELECT oi.order_item_id, oi.product_id, oi.units_per_case, "
                "oi.unit_price, p.available_cases, COALESCE((SELECT SUM(other.case_count) "
                "FROM qcc_sales_menu_order_items other JOIN qcc_sales_menu_orders held "
                "ON held.order_id = other.order_id WHERE other.product_id = oi.product_id "
                "AND held.status = %s AND held.order_id <> %s), 0) AS other_holds "
                "FROM qcc_sales_menu_order_items oi JOIN qcc_sales_menu_products p "
                "ON p.product_id = oi.product_id WHERE oi.order_id = %s FOR UPDATE OF oi",
                (ORDER_STATUS_PENDING, order_id, order_id),
            )
            items = _records(cursor)
            total_cases = 0
            total_units = 0
            total_amount = 0.0
            retained = 0
            for item in items:
                item_id = str(item.get("order_item_id", ""))
                cases = max(int(case_counts.get(item_id, 0)), 0)
                available = int(item.get("available_cases", 0) or 0)
                other_holds = int(item.get("other_holds", 0) or 0)
                if cases > max(available - other_holds, 0):
                    raise ValueError(
                        "One or more edited quantities exceed the cases available after other order holds."
                    )
                if cases == 0:
                    cursor.execute(
                        "DELETE FROM qcc_sales_menu_order_items WHERE order_item_id = %s",
                        (item_id,),
                    )
                    continue
                units_per_case = int(item.get("units_per_case", 0) or 0)
                unit_price = float(item.get("unit_price", 0) or 0)
                line_total = round(cases * units_per_case * unit_price, 2)
                cursor.execute(
                    "UPDATE qcc_sales_menu_order_items SET case_count = %s, "
                    "line_total = %s WHERE order_item_id = %s",
                    (cases, line_total, item_id),
                )
                retained += 1
                total_cases += cases
                total_units += cases * units_per_case
                total_amount += line_total
            if not retained:
                raise ValueError("An order must contain at least one line item.")
            delivery_value = requested_delivery_date.strip() or None
            cursor.execute(
                "UPDATE qcc_sales_menu_orders SET total_cases = %s, total_units = %s, "
                "total_amount = %s, requested_delivery_date = %s, customer_notes = %s, "
                "updated_at = NOW() WHERE order_id = %s",
                (
                    total_cases, total_units, round(total_amount, 2), delivery_value,
                    customer_notes.strip(), order_id,
                ),
            )
        connection.commit()
    order = _order_summary(order_id)
    send_order_email(order, f"Order updated by {updated_by}")
    return order


def review_menu_order(order_id: str, status: str, *, reviewed_by: str) -> dict[str, Any]:
    if status not in {ORDER_STATUS_APPROVED, ORDER_STATUS_DECLINED}:
        raise ValueError("Choose Approve or Decline.")
    ensure_sales_menu_schema()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM qcc_sales_menu_orders WHERE order_id = %s FOR UPDATE",
                (order_id,),
            )
            current = cursor.fetchone()
            if not current:
                raise ValueError("Order not found.")
            if current[0] != ORDER_STATUS_PENDING:
                raise ValueError("Only pending orders can be reviewed.")
            if status == ORDER_STATUS_APPROVED:
                cursor.execute(
                    "SELECT product_id, case_count FROM qcc_sales_menu_order_items "
                    "WHERE order_id = %s",
                    (order_id,),
                )
                for product_id, case_count in cursor.fetchall():
                    cursor.execute(
                        "UPDATE qcc_sales_menu_products SET available_cases = "
                        "available_cases - %s, manual_override_cases = "
                        "available_cases - %s, updated_by = %s, updated_at = NOW() "
                        "WHERE product_id = %s AND available_cases >= %s",
                        (case_count, case_count, reviewed_by, product_id, case_count),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError(
                            "Available cases changed and this order can no longer be approved."
                        )
            cursor.execute(
                "UPDATE qcc_sales_menu_orders SET status = %s, reviewed_at = NOW(), "
                "reviewed_by = %s WHERE order_id = %s",
                (status, reviewed_by, order_id),
            )
        connection.commit()
    order = _order_summary(order_id)
    send_order_email(order, f"Order {status.lower()}")
    return order


class BuyerMenuState(rx.State):
    access_code: str = ""
    access_error: str = ""
    buyer_authenticated: bool = False
    customer: dict[str, Any] = {}
    products: list[dict[str, Any]] = []
    cart: dict[str, int] = {}
    brand_filter: str = "All Brands"
    category_filter: str = "All Categories"
    search_text: str = ""
    requested_delivery_date: str = ""
    order_notes: str = ""
    order_message: str = ""
    order_error: str = ""
    submitting: bool = False
    selected_menu_brand: str = ""

    @rx.event
    def set_access_code(self, value: str): self.access_code = value
    @rx.event
    def set_brand_filter(self, value: str): self.brand_filter = value
    @rx.event
    def set_category_filter(self, value: str): self.category_filter = value
    @rx.event
    def set_search_text(self, value: str): self.search_text = value
    @rx.event
    def set_requested_delivery_date(self, value: str): self.requested_delivery_date = value
    @rx.event
    def set_order_notes(self, value: str): self.order_notes = value

    @rx.event
    def load_public_menu(self):
        self.access_error = ""
        self.order_error = ""

    @rx.event
    def verify_access_code(self):
        self.access_error = ""
        try:
            self.customer = authenticate_menu_customer(self.access_code)
            self.products = load_customer_menu_products(self.customer)
            self.buyer_authenticated = True
            self.cart = {}
            self.selected_menu_brand = ""
        except Exception as error:
            self.access_error = str(error)
            self.buyer_authenticated = False

    @rx.event
    def leave_menu(self):
        self.access_code = ""
        self.buyer_authenticated = False
        self.customer = {}
        self.products = []
        self.cart = {}
        self.selected_menu_brand = ""

    @rx.event
    def select_menu_brand(self, brand: str):
        if brand not in {"Clade9", "Craft Kings", "Locals Only"}:
            return
        self.selected_menu_brand = brand
        self.brand_filter = "All Brands"
        self.category_filter = "All Categories"
        self.search_text = ""

    @rx.event
    def change_cart_cases(self, product_id: str, value: str):
        try:
            cases = max(int(float(value or 0)), 0)
        except (TypeError, ValueError):
            cases = 0
        product = next(
            (row for row in self.products if row.get("product_id") == product_id),
            {},
        )
        cases = min(cases, int(product.get("available_cases", 0) or 0))
        updated = dict(self.cart)
        if cases:
            updated[product_id] = cases
        else:
            updated.pop(product_id, None)
        self.cart = updated
        self.order_error = ""

    @rx.event
    def submit_order(self):
        self.submitting = True
        self.order_error = ""
        self.order_message = ""
        yield
        try:
            result = submit_menu_order(
                self.customer, self.cart, self.requested_delivery_date,
                self.order_notes,
            )
            self.order_message = (
                f"Order {result['order_number']} was submitted for sales approval. "
                + str(result.get("email_message", ""))
            )
            self.cart = {}
            self.order_notes = ""
            self.requested_delivery_date = ""
            self.products = load_customer_menu_products(self.customer)
        except Exception as error:
            self.order_error = str(error)
        finally:
            self.submitting = False

    @rx.var(cache=True)
    def brand_options(self) -> list[str]:
        return ["All Brands", *sorted({str(row.get("brand", "")) for row in self.products})]

    @rx.var(cache=True)
    def category_options(self) -> list[str]:
        return ["All Categories", *sorted({str(row.get("category", "")) for row in self.products})]

    @rx.var(cache=True)
    def filtered_products(self) -> list[dict[str, Any]]:
        search = self.search_text.strip().casefold()
        rows: list[dict[str, Any]] = []
        for product in self.products:
            if self.brand_filter != "All Brands" and product.get("brand") != self.brand_filter:
                continue
            if self.category_filter != "All Categories" and product.get("category") != self.category_filter:
                continue
            haystack = " ".join(
                str(product.get(key, ""))
                for key in ("brand", "category", "package_size", "product_type", "strain")
            ).casefold()
            if search and search not in haystack:
                continue
            record = dict(product)
            record["cart_cases"] = int(self.cart.get(str(product.get("product_id", "")), 0))
            record["sold_out"] = int(product.get("available_cases", 0) or 0) <= 0
            rows.append(record)
        return rows

    def _menu_group(
        self, brands: set[str], categories: set[str] | None = None
    ) -> list[dict[str, Any]]:
        return [
            row for row in self.filtered_products
            if str(row.get("brand", "")) in brands
            and (categories is None or str(row.get("category", "")) in categories)
        ]

    def _size_groups(self, products: list[dict[str, Any]]) -> list[MenuSizeGroup]:
        grouped: dict[str, list[MenuProductRow]] = {}
        for product in products:
            package_size = str(product.get("package_size", "") or "Other")
            grouped.setdefault(package_size, []).append(product)  # type: ignore[arg-type]
        return [
            {"package_size": package_size, "products": grouped[package_size]}
            for package_size in sorted(grouped, key=_package_size_sort)
        ]

    @rx.var(cache=True)
    def clade9_section_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Clade9", "Melt x Clade9"})

    @rx.var(cache=True)
    def clade9_flower_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Clade9"}, {"Flower"})

    @rx.var(cache=True)
    def clade9_preroll_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Clade9"}, {"Pre-Rolls"})

    @rx.var(cache=True)
    def clade9_vape_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Clade9"}, {"Vape Cartridges", "Disposables"})

    @rx.var(cache=True)
    def clade9_concentrate_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Melt x Clade9"})

    @rx.var(cache=True)
    def craft_kings_section_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Craft Kings"})

    @rx.var(cache=True)
    def craft_kings_flower_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Craft Kings"}, {"Flower"})

    @rx.var(cache=True)
    def craft_kings_preroll_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Craft Kings"}, {"Pre-Rolls"})

    @rx.var(cache=True)
    def craft_kings_edible_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Craft Kings"}, {"Edibles"})

    @rx.var(cache=True)
    def royal_smalls_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Royal Smalls"})

    @rx.var(cache=True)
    def locals_only_products(self) -> list[dict[str, Any]]:
        return self._menu_group({"Locals Only"})

    @rx.var(cache=True)
    def clade9_flower_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.clade9_flower_products)

    @rx.var(cache=True)
    def clade9_preroll_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.clade9_preroll_products)

    @rx.var(cache=True)
    def clade9_vape_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.clade9_vape_products)

    @rx.var(cache=True)
    def clade9_concentrate_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.clade9_concentrate_products)

    @rx.var(cache=True)
    def craft_kings_flower_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.craft_kings_flower_products)

    @rx.var(cache=True)
    def craft_kings_preroll_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.craft_kings_preroll_products)

    @rx.var(cache=True)
    def craft_kings_edible_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.craft_kings_edible_products)

    @rx.var(cache=True)
    def royal_smalls_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.royal_smalls_products)

    @rx.var(cache=True)
    def locals_only_size_groups(self) -> list[MenuSizeGroup]:
        return self._size_groups(self.locals_only_products)

    @rx.var(cache=True)
    def cart_items(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for product in self.products:
            cases = int(self.cart.get(str(product.get("product_id", "")), 0))
            if not cases:
                continue
            unit_price = float(product.get("unit_price", 0) or 0)
            units_per_case = int(product.get("units_per_case", 0) or 0)
            rows.append(
                {
                    "product_id": product.get("product_id", ""),
                    "brand": product.get("brand", ""),
                    "strain": product.get("strain", ""),
                    "product": f"{product.get('package_size', '')} {product.get('product_type', '')}",
                    "cases": cases,
                    "units": cases * units_per_case,
                    "line_total": round(cases * units_per_case * unit_price, 2),
                }
            )
        return rows

    @rx.var(cache=True)
    def cart_total_cases(self) -> int:
        return sum(int(row.get("cases", 0)) for row in self.cart_items)

    @rx.var(cache=True)
    def cart_total_units(self) -> int:
        return sum(int(row.get("units", 0)) for row in self.cart_items)

    @rx.var(cache=True)
    def cart_total_amount(self) -> float:
        return round(sum(float(row.get("line_total", 0)) for row in self.cart_items), 2)


class MenuAdminState(rx.State):
    auth_session_token: str = rx.Cookie(
        "", name="qcc_auth_session", path="/", same_site="lax"
    )
    loaded: bool = False
    loading: bool = False
    database_ready: bool = False
    error: str = ""
    message: str = ""
    products: list[dict[str, Any]] = []
    customers: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    availability_drafts: dict[str, str] = {}
    product_search: str = ""
    product_brand_filter: str = "All Brands"
    product_sku_filter: str = "All SKU Types"
    customer_buyer_name: str = ""
    customer_store_name: str = ""
    customer_license: str = ""
    customer_email: str = ""
    customer_payment_terms: str = "Net 30"
    customer_salesperson: str = ""
    customer_minimum_cases: str = "0"
    customer_allowed_brands: str = "Clade9, Craft Kings, Royal Smalls, Melt x Clade9, Locals Only"
    customer_access_code: str = ""
    customer_is_active: bool = True
    customer_selection: str = ""
    price_customer_selection: str = ""
    price_product_selection: str = ""
    price_override: str = ""
    editing_order_id: str = ""
    editing_order_number: str = ""
    editing_order_delivery_date: str = ""
    editing_order_notes: str = ""
    editing_order_items: list[dict[str, Any]] = []
    editing_order_case_drafts: dict[str, str] = {}

    @rx.event
    def set_product_search(self, value: str): self.product_search = value
    @rx.event
    def set_product_brand_filter(self, value: str): self.product_brand_filter = value
    @rx.event
    def set_product_sku_filter(self, value: str): self.product_sku_filter = value
    @rx.event
    def set_customer_buyer_name(self, value: str): self.customer_buyer_name = value
    @rx.event
    def set_customer_store_name(self, value: str): self.customer_store_name = value
    @rx.event
    def set_customer_license(self, value: str): self.customer_license = value
    @rx.event
    def set_customer_email(self, value: str): self.customer_email = value
    @rx.event
    def set_customer_payment_terms(self, value: str): self.customer_payment_terms = value
    @rx.event
    def set_customer_salesperson(self, value: str): self.customer_salesperson = value
    @rx.event
    def set_customer_minimum_cases(self, value: str): self.customer_minimum_cases = value
    @rx.event
    def set_customer_allowed_brands(self, value: str): self.customer_allowed_brands = value
    @rx.event
    def set_customer_access_code(self, value: str): self.customer_access_code = value
    @rx.event
    def set_customer_is_active(self, value: bool): self.customer_is_active = value
    @rx.event
    def set_customer_selection(self, value: str):
        self.customer_selection = value
        customer_id = value.split(" | ", 1)[0]
        customer = next(
            (row for row in self.customers if row.get("customer_id") == customer_id),
            None,
        )
        if not customer:
            return
        self.customer_buyer_name = str(customer.get("buyer_name", "") or "")
        self.customer_store_name = str(customer.get("store_name", "") or "")
        self.customer_license = str(customer.get("license_number", "") or "")
        self.customer_email = str(customer.get("email", "") or "")
        self.customer_payment_terms = str(customer.get("payment_terms", "") or "")
        self.customer_salesperson = str(customer.get("assigned_salesperson", "") or "")
        self.customer_minimum_cases = str(int(customer.get("minimum_order_cases", 0) or 0))
        self.customer_allowed_brands = ", ".join(customer.get("allowed_brands") or MENU_BRANDS)
        self.customer_access_code = str(customer.get("access_code_display", "") or "")
        self.customer_is_active = bool(customer.get("is_active", False))
    @rx.event
    def set_price_customer_selection(self, value: str): self.price_customer_selection = value
    @rx.event
    def set_price_product_selection(self, value: str): self.price_product_selection = value
    @rx.event
    def set_price_override(self, value: str): self.price_override = value
    @rx.event
    def set_editing_order_delivery_date(self, value: str): self.editing_order_delivery_date = value
    @rx.event
    def set_editing_order_notes(self, value: str): self.editing_order_notes = value

    def _employee(self) -> dict[str, Any]:
        employee = validate_app_session(str(self.auth_session_token or ""))
        if not employee:
            raise PermissionError("Your employee session expired. Sign in again.")
        return employee

    def _apply_payload(self, payload: dict[str, Any]) -> None:
        self.database_ready = bool(payload.get("database_ready"))
        self.products = list(payload.get("products", []))
        self.customers = list(payload.get("customers", []))
        self.orders = list(payload.get("orders", []))
        self.availability_drafts = {
            str(row.get("product_id", "")): str(int(row.get("available_cases", 0) or 0))
            for row in self.products
        }

    @rx.event
    def load_admin(self):
        if self.loading:
            return
        self.loading = True
        self.error = ""
        yield
        try:
            employee = self._employee()
            updated_by = str(employee.get("full_name") or employee.get("user_email"))
            initial = load_menu_admin_data()
            if not initial.get("database_ready"):
                self._apply_payload(initial)
                self.loaded = True
                self.message = "Preview mode: connect Supabase to save customers, quantities, and orders."
                return
            inventory = refresh_menu_inventory_from_metrc(updated_by=updated_by)
            synced_customers = sync_menu_customers_from_metrc()
            self._apply_payload(load_menu_admin_data())
            self.loaded = True
            self.message = (
                f"Metrc snapshot refreshed: {inventory['matched']} menu SKUs matched; "
                f"{inventory['review']} need review; {inventory['discovered']} new SKUs "
                f"were added to admin for review. {synced_customers} customer records synchronized."
            )
        except Exception as error:
            self.error = str(error)
        finally:
            self.loading = False

    @rx.event
    def change_availability(self, product_id: str, value: str):
        updated = dict(self.availability_drafts)
        updated[product_id] = value
        self.availability_drafts = updated

    @rx.event
    def save_all_availability(self):
        self.error = ""
        self.message = ""
        try:
            employee = self._employee()
            current = {
                str(row.get("product_id", "")): int(row.get("available_cases", 0) or 0)
                for row in self.products
            }
            counts: dict[str, int] = {}
            for product_id, value in self.availability_drafts.items():
                parsed = max(int(float(value or 0)), 0)
                if parsed != current.get(product_id, 0):
                    counts[product_id] = parsed
            if not counts:
                self.message = "No menu quantity overrides changed."
                return
            update_menu_availability(
                counts, updated_by=str(employee.get("full_name") or employee.get("user_email"))
            )
            self._apply_payload(load_menu_admin_data())
            self.message = "Published menu quantities were updated."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def reset_inventory_override(self, product_id: str):
        self.error = ""
        self.message = ""
        try:
            employee = self._employee()
            clear_menu_inventory_override(
                product_id,
                updated_by=str(employee.get("full_name") or employee.get("user_email")),
            )
            self._apply_payload(load_menu_admin_data())
            self.message = "The SKU now follows its Metrc case count."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def activate_selected_customer(self):
        self.error = ""
        self.message = ""
        try:
            self._employee()
            customer_id = self.customer_selection.split(" | ", 1)[0]
            if not customer_id:
                raise ValueError("Select a Metrc customer.")
            allowed = [
                brand.strip() for brand in self.customer_allowed_brands.split(",")
                if brand.strip() in MENU_BRANDS
            ]
            activate_menu_customer(
                customer_id,
                buyer_name=self.customer_buyer_name,
                store_name=self.customer_store_name,
                email=self.customer_email,
                payment_terms=self.customer_payment_terms,
                assigned_salesperson=self.customer_salesperson,
                minimum_order_cases=int(float(self.customer_minimum_cases or 0)),
                allowed_brands=allowed,
                access_code=self.customer_access_code,
                is_active=self.customer_is_active,
            )
            self._apply_payload(load_menu_admin_data())
            self.message = "Buyer account controls were saved."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def create_customer(self):
        self.error = ""
        self.message = ""
        try:
            self._employee()
            allowed = [
                brand.strip() for brand in self.customer_allowed_brands.split(",")
                if brand.strip() in MENU_BRANDS
            ]
            create_menu_customer(
                buyer_name=self.customer_buyer_name,
                store_name=self.customer_store_name,
                license_number=self.customer_license,
                email=self.customer_email,
                payment_terms=self.customer_payment_terms,
                assigned_salesperson=self.customer_salesperson,
                minimum_order_cases=int(float(self.customer_minimum_cases or 0)),
                allowed_brands=allowed,
                access_code=self.customer_access_code,
            )
            self.customer_buyer_name = ""
            self.customer_store_name = ""
            self.customer_license = ""
            self.customer_email = ""
            self.customer_access_code = ""
            self._apply_payload(load_menu_admin_data())
            self.message = "Buyer account and access code were created."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def save_price_override(self):
        self.error = ""
        self.message = ""
        try:
            employee = self._employee()
            customer_id = self.price_customer_selection.split(" | ", 1)[0]
            product_id = self.price_product_selection.split(" | ", 1)[0]
            if not customer_id or not product_id:
                raise ValueError("Select a customer and product.")
            save_customer_price(
                customer_id, product_id, float(self.price_override),
                updated_by=str(employee.get("full_name") or employee.get("user_email")),
            )
            self.price_override = ""
            self.message = "Customer-specific SKU price saved."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def begin_edit_order(self, order_id: str):
        self.error = ""
        try:
            self._employee()
            order = _order_summary(order_id)
            if not order or order.get("status") != ORDER_STATUS_PENDING:
                raise ValueError("Only pending orders can be edited.")
            self.editing_order_id = order_id
            self.editing_order_number = str(order.get("order_number", ""))
            self.editing_order_delivery_date = str(order.get("requested_delivery_date") or "")
            self.editing_order_notes = str(order.get("customer_notes", "") or "")
            self.editing_order_items = [
                {**item, "draft_cases": str(int(item.get("case_count", 0) or 0))}
                for item in order.get("items", [])
            ]
            self.editing_order_case_drafts = {
                str(item.get("order_item_id", "")): str(int(item.get("case_count", 0) or 0))
                for item in self.editing_order_items
            }
        except Exception as error:
            self.error = str(error)

    @rx.event
    def change_editing_order_cases(self, order_item_id: str, value: str):
        drafts = dict(self.editing_order_case_drafts)
        drafts[order_item_id] = value
        self.editing_order_case_drafts = drafts
        self.editing_order_items = [
            {**item, "draft_cases": value}
            if str(item.get("order_item_id", "")) == order_item_id else item
            for item in self.editing_order_items
        ]

    @rx.event
    def cancel_edit_order(self):
        self.editing_order_id = ""
        self.editing_order_number = ""
        self.editing_order_items = []
        self.editing_order_case_drafts = {}

    @rx.event
    def save_order_edits(self):
        self.error = ""
        self.message = ""
        try:
            employee = self._employee()
            counts = {
                item_id: max(int(float(value or 0)), 0)
                for item_id, value in self.editing_order_case_drafts.items()
            }
            update_pending_menu_order(
                self.editing_order_id,
                counts,
                requested_delivery_date=self.editing_order_delivery_date,
                customer_notes=self.editing_order_notes,
                updated_by=str(employee.get("full_name") or employee.get("user_email")),
            )
            self.editing_order_id = ""
            self.editing_order_number = ""
            self.editing_order_items = []
            self.editing_order_case_drafts = {}
            self._apply_payload(load_menu_admin_data())
            self.message = "Order changes were saved and an updated email was sent."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def review_order(self, order_id: str, status: str):
        self.error = ""
        self.message = ""
        try:
            employee = self._employee()
            review_menu_order(
                order_id, status,
                reviewed_by=str(employee.get("full_name") or employee.get("user_email")),
            )
            self._apply_payload(load_menu_admin_data())
            self.message = f"Order {status.lower()} and the buyer was notified."
        except Exception as error:
            self.error = str(error)

    @rx.var(cache=True)
    def filtered_products(self) -> list[dict[str, Any]]:
        search = self.product_search.strip().casefold()
        rows: list[dict[str, Any]] = []
        for row in self.products:
            if self.product_brand_filter != "All Brands" and row.get("brand") != self.product_brand_filter:
                continue
            if self.product_sku_filter != "All SKU Types" and row.get("sku_filter_label") != self.product_sku_filter:
                continue
            haystack = " ".join(
                str(row.get(key, "")) for key in
                ("brand", "category", "package_size", "product_type", "strain")
            ).casefold()
            if search and search not in haystack:
                continue
            record = dict(row)
            record["draft_cases"] = self.availability_drafts.get(
                str(row.get("product_id", "")), str(row.get("available_cases", 0))
            )
            rows.append(record)
        return rows

    @rx.var(cache=True)
    def pending_orders(self) -> list[dict[str, Any]]:
        return [row for row in self.orders if row.get("status") == ORDER_STATUS_PENDING]

    @rx.var(cache=True)
    def customer_options(self) -> list[str]:
        return [
            f"{row.get('customer_id', '')} | {row.get('store_name', '')} ({row.get('license_number', '')})"
            for row in self.customers
        ]

    @rx.var(cache=True)
    def product_options(self) -> list[str]:
        return [
            f"{row.get('product_id', '')} | {row.get('brand', '')} | {row.get('package_size', '')} {row.get('strain', '')}"
            for row in self.products
        ]

    @rx.var(cache=True)
    def menu_sku_filter_options(self) -> list[str]:
        values = sorted({
            str(row.get("sku_filter_label", ""))
            for row in self.products if row.get("sku_filter_label")
        })
        return ["All SKU Types", *values]

    @rx.var(cache=True)
    def active_product_count(self) -> int:
        return len([row for row in self.products if bool(row.get("is_active", True))])

    @rx.var(cache=True)
    def published_case_count(self) -> int:
        return sum(int(row.get("available_cases", 0) or 0) for row in self.products)

    @rx.var(cache=True)
    def held_case_count(self) -> int:
        return sum(int(row.get("held_cases", 0) or 0) for row in self.products)


def _clade9_logo() -> rx.Component:
    return rx.image(
        src="/sales-menu/clade9-logo-black.png",
        alt="Clade9",
        class_name="qcc-menu-section-logo qcc-menu-clade9-logo",
    )


def _qcc_access_logo() -> rx.Component:
    return rx.image(
        src="/sales-menu/qcc-group.png",
        alt="Powered by The QCC Group",
        class_name="qcc-menu-access-qcc-logo",
    )


def _access_gate() -> rx.Component:
    return rx.center(
        rx.vstack(
            _qcc_access_logo(),
            rx.badge("NEW JERSEY WHOLESALE", color_scheme="amber", variant="soft"),
            rx.heading("The QCC Buyer Menu", size="8", text_align="center"),
            rx.text(
                "A curated wholesale menu for licensed retail partners.",
                color="#5f5a52", text_align="center", size="3",
            ),
            rx.input(
                placeholder="Buyer access code",
                value=BuyerMenuState.access_code,
                on_change=BuyerMenuState.set_access_code,
                width="100%", size="3", class_name="qcc-menu-access-input",
            ),
            rx.button(
                "Enter Buyer Menu", on_click=BuyerMenuState.verify_access_code,
                width="100%", size="3", class_name="qcc-menu-primary-button",
            ),
            rx.cond(
                BuyerMenuState.access_error != "",
                rx.callout(BuyerMenuState.access_error, icon="triangle_alert", color_scheme="red", width="100%"),
            ),
            rx.text(
                "Authorized licensed buyers only. Local preview code: DEMO2026",
                size="1", color="#77716a", text_align="center",
            ),
            spacing="4", align="center", width="100%", max_width="520px",
            padding="3rem", class_name="qcc-menu-access-card",
        ),
        min_height="100vh", padding="1.5rem", class_name="qcc-public-menu qcc-menu-access-page",
    )


def _product_list_row(product: rx.Var[MenuProductRow]) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.vstack(
                rx.heading(product["strain"], size="4", color="#171512"),
                rx.hstack(
                    rx.badge(product["package_size"], color_scheme="amber"),
                    rx.text(product["product_type"], size="1", color="#756d62", weight="bold"),
                    gap="2", align="center", wrap="wrap",
                ),
                rx.cond(
                    product["notes"] != "",
                    rx.text(product["notes"], size="1", color="#9a6418"),
                ),
                spacing="1", align="start",
            ),
            class_name="qcc-menu-product-name-cell",
        ),
        rx.table.cell(
            rx.vstack(
                rx.text("THC ", rx.cond(product["thc_display"] != "", product["thc_display"], "-"), weight="bold"),
                rx.text("Terpenes ", rx.cond(product["terpene_display"] != "", product["terpene_display"], "-"), size="1", color="#756d62"),
                spacing="1", align="start",
            )
        ),
        rx.table.cell(rx.text(product["units_per_case"], weight="bold")),
        rx.table.cell(rx.text("$", product["unit_price"], weight="bold")),
        rx.table.cell(rx.text(product["available_cases"], " cases", weight="bold")),
        rx.table.cell(
            rx.input(
                type="number", min="0", max=product["available_cases"], step="1",
                value=product["cart_cases"].to_string(),
                on_change=lambda value: BuyerMenuState.change_cart_cases(
                    product["product_id"], value
                ),
                width="86px", size="2", text_align="center",
                disabled=product["sold_out"],
            )
        ),
        class_name="qcc-menu-product-list-row",
    )


def _product_size_group(group: rx.Var[MenuSizeGroup]) -> rx.Component:
    return rx.box(
        rx.box(
            rx.badge(group["package_size"], color_scheme="amber", size="2"),
            class_name="qcc-menu-size-label",
        ),
        rx.box(
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Product & Strain"),
                        rx.table.column_header_cell("Potency"),
                        rx.table.column_header_cell("Units / Case"),
                        rx.table.column_header_cell("Unit Price"),
                        rx.table.column_header_cell("Available"),
                        rx.table.column_header_cell("Order Cases"),
                    )
                ),
                rx.table.body(rx.foreach(group["products"], _product_list_row)),
                variant="surface", size="2", width="100%",
                class_name="qcc-menu-product-list",
            ),
            width="100%", overflow_x="auto",
        ),
        class_name="qcc-menu-size-group",
        width="100%",
    )


def _menu_category_group(
    title: str, description: str, size_groups: rx.Var
) -> rx.Component:
    return rx.cond(
        size_groups.length() > 0,
        rx.vstack(
            rx.box(
                rx.heading(title, size="6"),
                rx.text(description, color="#756d62", size="2"),
                class_name="qcc-menu-category-heading",
                width="100%",
            ),
            rx.vstack(
                rx.foreach(size_groups, _product_size_group),
                spacing="4", width="100%",
            ),
            spacing="4", width="100%",
        ),
    )


def _brand_navigation() -> rx.Component:
    return rx.flex(
        rx.box(
            _clade9_logo(),
            on_click=BuyerMenuState.select_menu_brand("Clade9"),
            role="button", tab_index=0,
            aria_label="Go to the Clade9 menu",
            class_name="qcc-menu-brand-nav-link",
        ),
        rx.box(
            rx.image(
                src="/sales-menu/craft-kings.png",
                alt="Craft Kings",
                class_name="qcc-menu-brand-nav-logo qcc-menu-brand-nav-craft-kings",
            ),
            on_click=BuyerMenuState.select_menu_brand("Craft Kings"),
            role="button", tab_index=0,
            aria_label="Go to the Craft Kings menu",
            class_name="qcc-menu-brand-nav-link",
        ),
        rx.box(
            rx.image(
                src="/sales-menu/locals-only.png",
                alt="Locals Only Concentrates",
                class_name="qcc-menu-brand-nav-logo qcc-menu-brand-nav-locals",
            ),
            on_click=BuyerMenuState.select_menu_brand("Locals Only"),
            role="button", tab_index=0,
            aria_label="Go to the Locals Only menu",
            class_name="qcc-menu-brand-nav-link",
        ),
        align="center", justify="between", wrap="wrap", gap="7",
        class_name="qcc-menu-brand-navigation", width="100%",
    )


def _brand_section(
    section_id: str, brand_mark: rx.Component, products: rx.Var,
    *groups: rx.Component
) -> rx.Component:
    return rx.cond(
        products.length() > 0,
        rx.box(
            rx.vstack(
                rx.center(brand_mark, width="100%", class_name="qcc-menu-brand-heading"),
                *groups,
                spacing="7", width="100%",
            ),
            class_name="qcc-menu-brand-section",
            id=section_id,
            width="100%",
        ),
    )


def _menu_catalog() -> rx.Component:
    clade9_menu = _brand_section(
            "clade9-menu",
            _clade9_logo(),
            BuyerMenuState.clade9_section_products,
            _menu_category_group(
                "Flower",
                "Premium Clade9 flower formats.",
                BuyerMenuState.clade9_flower_size_groups,
            ),
            _menu_category_group(
                "Pre-Rolls",
                "Ready-to-enjoy Clade9 pre-roll formats.",
                BuyerMenuState.clade9_preroll_size_groups,
            ),
            _menu_category_group(
                "Vapes",
                "Clade9 cartridges and disposable formats.",
                BuyerMenuState.clade9_vape_size_groups,
            ),
            _menu_category_group(
                "Concentrates",
                "Melt x Clade9 solventless concentrates and disposables.",
                BuyerMenuState.clade9_concentrate_size_groups,
            ),
        )
    craft_kings_menu = rx.vstack(
        _brand_section(
            "craft-kings-menu",
            rx.image(
                src="/sales-menu/craft-kings.png", alt="Craft Kings",
                class_name="qcc-menu-section-logo qcc-menu-craft-kings-logo",
            ),
            BuyerMenuState.craft_kings_section_products,
            _menu_category_group(
                "Flower",
                "Craft Kings flower selections.",
                BuyerMenuState.craft_kings_flower_size_groups,
            ),
            _menu_category_group(
                "Pre-Rolls",
                "Craft Kings pre-roll selections.",
                BuyerMenuState.craft_kings_preroll_size_groups,
            ),
            _menu_category_group(
                "Edibles",
                "Craft Kings infused edible products.",
                BuyerMenuState.craft_kings_edible_size_groups,
            ),
        ),
        _brand_section(
            "royal-smalls-menu",
            rx.heading("ROYAL SMALLS", class_name="qcc-menu-royal-wordmark"),
            BuyerMenuState.royal_smalls_products,
            _menu_category_group(
                "Flower",
                "Royal Smalls flower selections.",
                BuyerMenuState.royal_smalls_size_groups,
            ),
        ),
        spacing="7", width="100%",
    )
    locals_only_menu = _brand_section(
            "locals-only-menu",
            rx.image(
                src="/sales-menu/locals-only.png", alt="Locals Only Concentrates",
                class_name="qcc-menu-section-logo qcc-menu-locals-logo",
            ),
            BuyerMenuState.locals_only_products,
            _menu_category_group(
                "Concentrates",
                "Locals Only concentrates produced for New Jersey.",
                BuyerMenuState.locals_only_size_groups,
            ),
        )
    return rx.match(
        BuyerMenuState.selected_menu_brand,
        ("Clade9", clade9_menu),
        ("Craft Kings", craft_kings_menu),
        ("Locals Only", locals_only_menu),
        rx.box(),
    )


def _cart_line(item: rx.Var) -> rx.Component:
    return rx.box(
        rx.flex(
            rx.box(
                rx.text(item["strain"], weight="bold"),
                rx.text(item["brand"], " - ", item["product"], size="1", color="#756d62"),
            ),
            rx.spacer(),
            rx.text(item["cases"], " cs", weight="bold"),
            width="100%", align="center",
        ),
        rx.text("$", item["line_total"], size="1", color="#756d62", text_align="right"),
        width="100%", padding_y="0.65rem", border_bottom="1px solid #ded8ce",
    )


def _buyer_shop() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.flex(
                rx.spacer(),
                rx.vstack(
                    rx.text(BuyerMenuState.customer["store_name"], weight="bold"),
                    rx.text(BuyerMenuState.customer["buyer_name"], size="1", color="#756d62"),
                    rx.button("Leave Menu", on_click=BuyerMenuState.leave_menu, variant="ghost", size="1"),
                    align="end", spacing="0",
                ),
                width="100%", align="center", class_name="qcc-menu-header",
            ),
            rx.box(
                rx.vstack(
                    rx.badge("CURATED WHOLESALE COLLECTION", color_scheme="amber"),
                    rx.heading("Built for the best shelves in New Jersey.", size="9", max_width="820px"),
                    rx.text(
                        "Explore current releases, build your order by case, and submit it for fast sales approval.",
                        size="3", color="#d8d2c7", max_width="720px",
                    ),
                    spacing="4", align="start",
                ),
                width="100%", class_name="qcc-menu-hero",
            ),
            rx.flex(
                rx.input(
                    placeholder="Search strain or product...",
                    value=BuyerMenuState.search_text,
                    on_change=BuyerMenuState.set_search_text,
                    width=rx.breakpoints(initial="100%", md="320px"),
                ),
                rx.select(
                    BuyerMenuState.brand_options,
                    value=BuyerMenuState.brand_filter,
                    on_change=BuyerMenuState.set_brand_filter,
                    width=rx.breakpoints(initial="100%", md="210px"),
                ),
                rx.select(
                    BuyerMenuState.category_options,
                    value=BuyerMenuState.category_filter,
                    on_change=BuyerMenuState.set_category_filter,
                    width=rx.breakpoints(initial="100%", md="210px"),
                ),
                gap="3", wrap="wrap", width="100%", class_name="qcc-menu-filter-bar",
            ),
            rx.heading(
                "Click on the brand to order",
                size="6", text_align="center", width="100%",
                class_name="qcc-menu-brand-instruction",
            ),
            _brand_navigation(),
            rx.cond(
                BuyerMenuState.selected_menu_brand != "",
                rx.grid(
                    rx.box(
                        _menu_catalog(),
                        width="100%",
                    ),
                    rx.card(
                    rx.vstack(
                        rx.box(
                            rx.text("ORDER REQUEST", class_name="qcc-menu-eyebrow"),
                            rx.heading("Your Selection", size="5"),
                        ),
                        rx.cond(
                            BuyerMenuState.cart_items.length() > 0,
                            rx.vstack(rx.foreach(BuyerMenuState.cart_items, _cart_line), width="100%", spacing="0"),
                            rx.text("Add cases from the menu to begin.", color="#756d62", size="2"),
                        ),
                        rx.separator(width="100%"),
                        rx.flex(rx.text("Cases"), rx.spacer(), rx.text(BuyerMenuState.cart_total_cases, weight="bold"), width="100%"),
                        rx.flex(rx.text("Units"), rx.spacer(), rx.text(BuyerMenuState.cart_total_units, weight="bold"), width="100%"),
                        rx.flex(rx.text("Order total", weight="bold"), rx.spacer(), rx.heading("$", BuyerMenuState.cart_total_amount, size="5"), width="100%", align="center"),
                        rx.input(type="date", value=BuyerMenuState.requested_delivery_date, on_change=BuyerMenuState.set_requested_delivery_date, width="100%"),
                        rx.text_area(placeholder="Delivery notes or buyer comments", value=BuyerMenuState.order_notes, on_change=BuyerMenuState.set_order_notes, width="100%"),
                        rx.callout(
                            "Submission places a temporary hold. Your sales representative must approve the order before it is finalized.",
                            icon="clock", color_scheme="amber", size="1", width="100%",
                        ),
                        rx.button(
                            "Submit for Sales Approval", on_click=BuyerMenuState.submit_order,
                            loading=BuyerMenuState.submitting,
                            disabled=BuyerMenuState.cart_total_cases == 0,
                            width="100%", size="3", class_name="qcc-menu-primary-button",
                        ),
                        rx.cond(BuyerMenuState.order_error != "", rx.callout(BuyerMenuState.order_error, icon="triangle_alert", color_scheme="red", width="100%")),
                        rx.cond(BuyerMenuState.order_message != "", rx.callout(BuyerMenuState.order_message, icon="circle_check", color_scheme="green", width="100%")),
                        spacing="3", width="100%",
                    ),
                        class_name="qcc-menu-cart", width="100%",
                    ),
                    columns=rx.breakpoints(initial="1", xl="minmax(0, 1fr) 360px"),
                    gap="5", width="100%", align_items="start",
                ),
            ),
            rx.flex(
                rx.hstack(
                    rx.image(src="/sales-menu/qcc-group.png", alt="The QCC Group", class_name="qcc-menu-qcc-logo"),
                    rx.text("Powered by The QCC Group", weight="bold"),
                    gap="2", align="center",
                ),
                rx.text("For licensed New Jersey cannabis retailers.", size="1", color="#756d62"),
                justify="between", wrap="wrap", gap="2", width="100%", class_name="qcc-menu-footer",
            ),
            width="100%", max_width="1760px", margin="0 auto", padding="1.25rem", spacing="5",
        ),
        min_height="100vh", class_name="qcc-public-menu",
    )


def buyer_menu_page() -> rx.Component:
    return rx.cond(BuyerMenuState.buyer_authenticated, _buyer_shop(), _access_gate())


def _admin_metric(label: str, value: rx.Var, color: str) -> rx.Component:
    return rx.card(
        rx.vstack(rx.text(label, size="1", weight="bold", color="#64748b"), rx.heading(value, size="6"), spacing="1"),
        border_top=f"4px solid {color}", width="100%",
    )


def _admin_product_row(product: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(product["brand"], weight="bold")),
        rx.table.cell(product["package_size"]),
        rx.table.cell(product["product_type"]),
        rx.table.cell(product["strain"]),
        rx.table.cell(rx.text("$", product["unit_price"])),
        rx.table.cell(product["units_per_case"]),
        rx.table.cell(product["metrc_on_hand_units"]),
        rx.table.cell(product["metrc_case_equivalent"]),
        rx.table.cell(
            rx.vstack(
                rx.badge(
                    product["inventory_match_status"],
                    color_scheme=rx.cond(
                        product["inventory_match_status"] == "Matched", "green", "orange"
                    ),
                    variant="soft",
                ),
                rx.text(product["inventory_match_detail"], size="1", color="#64748b"),
                spacing="1", align="start",
            )
        ),
        rx.table.cell(
            rx.badge(
                rx.cond(product["is_active"], "Published", "Admin review"),
                color_scheme=rx.cond(product["is_active"], "green", "purple"),
                variant="soft",
            )
        ),
        rx.table.cell(product["held_cases"]),
        rx.table.cell(
            rx.input(
                type="number", min="0", step="1", value=product["draft_cases"],
                on_change=lambda value: MenuAdminState.change_availability(product["product_id"], value),
                width="92px", size="1", text_align="center",
            )
        ),
        rx.table.cell(
            rx.vstack(
                rx.text(product["inventory_source"], size="1", weight="bold"),
                rx.cond(
                    product["inventory_source"] == "Manual override",
                    rx.button(
                        "Use Metrc", size="1", variant="outline",
                        on_click=MenuAdminState.reset_inventory_override(product["product_id"]),
                    ),
                ),
                spacing="1", align="start",
            )
        ),
        rx.table.cell(product["available_to_order"]),
    )


def _admin_customer_row(customer: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(customer["store_name"], weight="bold")),
        rx.table.cell(customer["buyer_name"]),
        rx.table.cell(customer["license_number"]),
        rx.table.cell(customer["payment_terms"]),
        rx.table.cell(customer["minimum_order_cases"]),
        rx.table.cell(customer["assigned_salesperson"]),
        rx.table.cell(customer["allowed_brands_display"]),
        rx.table.cell(customer["source_system"]),
        rx.table.cell(
            rx.badge(
                rx.cond(customer["is_active"], "Active", "Needs access code"),
                color_scheme=rx.cond(customer["is_active"], "green", "orange"),
                variant="soft",
            )
        ),
        rx.table.cell(
            rx.code(
                rx.cond(
                    customer["access_code_display"] != "",
                    customer["access_code_display"],
                    "Reset required",
                ),
                size="2",
            )
        ),
        rx.table.cell(
            rx.button(
                "Edit",
                size="1",
                variant="outline",
                on_click=MenuAdminState.set_customer_selection(
                    customer["customer_id"].to_string()
                    + " | "
                    + customer["store_name"].to_string()
                ),
            )
        ),
    )


def _admin_order_card(order: rx.Var) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.flex(
                rx.box(
                    rx.text(order["order_number"], weight="bold", size="3"),
                    rx.text(order["store_name"], " - ", order["buyer_name"], color="#64748b"),
                ),
                rx.spacer(),
                rx.badge(order["status"], color_scheme=rx.cond(order["status"] == ORDER_STATUS_PENDING, "orange", rx.cond(order["status"] == ORDER_STATUS_APPROVED, "green", "red"))),
                width="100%", align="center",
            ),
            rx.grid(
                *[
                    rx.box(
                        rx.text(label, size="1", color="#64748b", weight="bold"),
                        rx.text(value, size="3", weight="bold"),
                        padding="0.7rem 0.9rem",
                        border="1px solid #e2e8f0",
                        border_radius="8px",
                        min_width="145px",
                    )
                    for label, value in [
                        ("CASES", order["total_cases"]),
                        ("UNITS", order["total_units_display"]),
                        ("ORDER TOTAL", order["total_amount_display"]),
                        ("REQUESTED DELIVERY", order["requested_delivery_display"]),
                    ]
                ],
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                gap="3", width="100%",
            ),
            rx.cond(
                order["status"] == ORDER_STATUS_PENDING,
                rx.hstack(
                    rx.button(
                        "Edit Order",
                        on_click=MenuAdminState.begin_edit_order(order["order_id"]),
                        variant="outline",
                    ),
                    rx.button("Approve", on_click=MenuAdminState.review_order(order["order_id"], ORDER_STATUS_APPROVED), color_scheme="green"),
                    rx.button("Decline", on_click=MenuAdminState.review_order(order["order_id"], ORDER_STATUS_DECLINED), color_scheme="red", variant="outline"),
                    gap="2",
                ),
            ),
            spacing="3", width="100%",
        ),
        width="100%",
    )


def _admin_order_edit_item(item: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(item["brand"], weight="bold")),
        rx.table.cell(
            item["package_size"].to_string()
            + " "
            + item["product_type"].to_string()
        ),
        rx.table.cell(item["strain"]),
        rx.table.cell(
            rx.input(
                type="number", min="0", step="1", width="100px",
                value=item["draft_cases"],
                on_change=lambda value: MenuAdminState.change_editing_order_cases(
                    item["order_item_id"], value
                ),
            )
        ),
    )


def _admin_order_editor() -> rx.Component:
    return rx.cond(
        MenuAdminState.editing_order_id != "",
        rx.card(
            rx.vstack(
                rx.heading("Edit " + MenuAdminState.editing_order_number, size="5"),
                rx.text(
                    "Set a line to zero to remove it. Availability is rechecked before saving.",
                    color="#64748b", size="2",
                ),
                rx.box(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(*[
                                rx.table.column_header_cell(label)
                                for label in ["Brand", "Product", "Strain", "Cases"]
                            ])
                        ),
                        rx.table.body(
                            rx.foreach(MenuAdminState.editing_order_items, _admin_order_edit_item)
                        ),
                        width="100%", min_width="720px",
                    ),
                    width="100%", overflow_x="auto",
                ),
                rx.grid(
                    rx.box(
                        rx.text("Requested delivery", size="1", weight="bold"),
                        rx.input(
                            type="date", value=MenuAdminState.editing_order_delivery_date,
                            on_change=MenuAdminState.set_editing_order_delivery_date,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Order notes", size="1", weight="bold"),
                        rx.text_area(
                            value=MenuAdminState.editing_order_notes,
                            on_change=MenuAdminState.set_editing_order_notes,
                            width="100%",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", md="2"), gap="3", width="100%",
                ),
                rx.hstack(
                    rx.button("Save Changes", on_click=MenuAdminState.save_order_edits, color_scheme="teal"),
                    rx.button("Cancel", on_click=MenuAdminState.cancel_edit_order, variant="outline"),
                    spacing="2",
                ),
                spacing="3", width="100%",
            ),
            border_left="5px solid #7c3aed", width="100%",
        ),
    )


def sales_menu_admin_panel() -> rx.Component:
    return rx.vstack(
        rx.card(
            rx.flex(
                rx.box(
                    rx.heading("Buyer Menu Administration", size="5"),
                    rx.text("Publish case availability, manage buyer controls, and approve submitted orders.", color="#64748b"),
                ),
                rx.spacer(),
                rx.button("Refresh", on_click=MenuAdminState.load_admin, loading=MenuAdminState.loading, variant="outline"),
                width="100%", align="center", wrap="wrap", gap="3",
            ),
            border_left="5px solid #c7a55b", width="100%",
        ),
        rx.cond(MenuAdminState.error != "", rx.callout(MenuAdminState.error, icon="triangle_alert", color_scheme="red", width="100%")),
        rx.cond(MenuAdminState.message != "", rx.callout(MenuAdminState.message, icon="info", color_scheme="blue", width="100%")),
        rx.grid(
            _admin_metric("Active Menu SKUs", MenuAdminState.active_product_count, "#111827"),
            _admin_metric("Published Cases", MenuAdminState.published_case_count, "#0f766e"),
            _admin_metric("Cases on Hold", MenuAdminState.held_case_count, "#c2410c"),
            _admin_metric("Pending Orders", MenuAdminState.pending_orders.length(), "#7c3aed"),
            columns=rx.breakpoints(initial="1", sm="2", lg="4"), gap="3", width="100%",
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Menu Quantities", value="inventory"),
                rx.tabs.trigger("Buyer Accounts", value="customers"),
                rx.tabs.trigger("Order Approvals", value="orders"),
                class_name="qcc-tabs", width="100%",
            ),
            rx.tabs.content(
                rx.vstack(
                    rx.flex(
                        rx.input(placeholder="Search menu products...", value=MenuAdminState.product_search, on_change=MenuAdminState.set_product_search, width="310px"),
                        rx.select(["All Brands", *MENU_BRANDS], value=MenuAdminState.product_brand_filter, on_change=MenuAdminState.set_product_brand_filter, width="210px"),
                        rx.select(MenuAdminState.menu_sku_filter_options, value=MenuAdminState.product_sku_filter, on_change=MenuAdminState.set_product_sku_filter, width="235px"),
                        rx.spacer(),
                        rx.button("Refresh Metrc Inventory", on_click=MenuAdminState.load_admin, variant="outline"),
                        rx.button("Save Quantity Overrides", on_click=MenuAdminState.save_all_availability, color_scheme="teal"),
                        gap="3", wrap="wrap", width="100%", align="end",
                    ),
                    rx.box(
                        rx.table.root(
                            rx.table.header(rx.table.row(*[rx.table.column_header_cell(value) for value in ["Brand", "Size", "Product", "Strain / Variety", "Unit Price", "Units / Case", "Metrc Units", "Metrc Full Cases", "Metrc Match", "Menu Status", "Held", "Published Cases", "Quantity Source", "Available to Order"]])),
                            rx.table.body(rx.foreach(MenuAdminState.filtered_products, _admin_product_row)),
                            width="100%", min_width="1840px", variant="surface",
                        ),
                        width="100%", max_height="640px", overflow="auto",
                    ),
                    spacing="3", width="100%",
                ),
                value="inventory", padding_top="1rem",
            ),
            rx.tabs.content(
                rx.vstack(
                    rx.card(
                        rx.vstack(
                            rx.heading("Manage Buyer Account", size="4"),
                            rx.text(
                                "Customers are synchronized from accepted Metrc retail transfers. Select an account to edit its buyer controls or reset its access code.",
                                color="#64748b", size="2",
                            ),
                            rx.select(
                                MenuAdminState.customer_options,
                                placeholder="Select Metrc customer",
                                value=MenuAdminState.customer_selection,
                                on_change=MenuAdminState.set_customer_selection,
                                width="100%",
                            ),
                            rx.grid(
                                rx.input(placeholder="Store name", value=MenuAdminState.customer_store_name, on_change=MenuAdminState.set_customer_store_name),
                                rx.input(placeholder="NJ license number", value=MenuAdminState.customer_license, disabled=True),
                                rx.input(placeholder="Buyer name", value=MenuAdminState.customer_buyer_name, on_change=MenuAdminState.set_customer_buyer_name),
                                rx.input(placeholder="Buyer email", value=MenuAdminState.customer_email, on_change=MenuAdminState.set_customer_email),
                                rx.input(placeholder="Payment terms", value=MenuAdminState.customer_payment_terms, on_change=MenuAdminState.set_customer_payment_terms),
                                rx.input(placeholder="Assigned salesperson", value=MenuAdminState.customer_salesperson, on_change=MenuAdminState.set_customer_salesperson),
                                rx.input(type="number", min="0", placeholder="Minimum cases", value=MenuAdminState.customer_minimum_cases, on_change=MenuAdminState.set_customer_minimum_cases),
                                rx.input(placeholder="Unique access code", value=MenuAdminState.customer_access_code, on_change=MenuAdminState.set_customer_access_code),
                                columns=rx.breakpoints(initial="1", md="2", xl="4"), gap="3", width="100%",
                            ),
                            rx.input(placeholder="Allowed brands, comma separated", value=MenuAdminState.customer_allowed_brands, on_change=MenuAdminState.set_customer_allowed_brands, width="100%"),
                            rx.hstack(
                                rx.switch(
                                    checked=MenuAdminState.customer_is_active,
                                    on_change=MenuAdminState.set_customer_is_active,
                                ),
                                rx.text("Buyer account active", weight="bold"),
                                spacing="2",
                            ),
                            rx.button("Save Buyer Account", on_click=MenuAdminState.activate_selected_customer, color_scheme="teal"),
                            spacing="3", width="100%",
                        ), width="100%",
                    ),
                    rx.card(
                        rx.vstack(
                            rx.heading("Customer-Specific SKU Price", size="4"),
                            rx.grid(
                                rx.select(MenuAdminState.customer_options, placeholder="Select customer", value=MenuAdminState.price_customer_selection, on_change=MenuAdminState.set_price_customer_selection, width="100%"),
                                rx.select(MenuAdminState.product_options, placeholder="Select SKU", value=MenuAdminState.price_product_selection, on_change=MenuAdminState.set_price_product_selection, width="100%"),
                                rx.input(type="number", min="0.01", step="0.01", placeholder="Override unit price", value=MenuAdminState.price_override, on_change=MenuAdminState.set_price_override),
                                rx.button("Save Price", on_click=MenuAdminState.save_price_override),
                                columns=rx.breakpoints(initial="1", lg="4"), gap="3", width="100%", align_items="end",
                            ), spacing="3", width="100%",
                        ), width="100%",
                    ),
                    rx.box(
                        rx.table.root(
                            rx.table.header(rx.table.row(*[rx.table.column_header_cell(value) for value in ["Store", "Buyer", "License", "Terms", "Minimum Cases", "Salesperson", "Allowed Brands", "Source", "Status", "Access Code", "Edit"]])),
                            rx.table.body(rx.foreach(MenuAdminState.customers, _admin_customer_row)),
                            width="100%", min_width="1540px", variant="surface",
                        ), width="100%", overflow_x="auto",
                    ),
                    spacing="4", width="100%",
                ),
                value="customers", padding_top="1rem",
            ),
            rx.tabs.content(
                rx.vstack(
                    _admin_order_editor(),
                    rx.cond(
                        MenuAdminState.orders.length() > 0,
                        rx.vstack(rx.foreach(MenuAdminState.orders, _admin_order_card), spacing="3", width="100%"),
                        rx.callout("No buyer orders have been submitted yet.", icon="shopping_bag", width="100%"),
                    ),
                    spacing="3", width="100%",
                ),
                value="orders", padding_top="1rem",
            ),
            default_value="inventory", width="100%",
        ),
        width="100%", spacing="4", on_mount=MenuAdminState.load_admin,
    )
