"""Validated Zebra ZPL generation for cultivation flower and pre-roll labels."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any


PACKAGE_FORMATS: dict[str, dict[str, str]] = {
    "3.5g Flower": {
        "suffix": "A", "net_weight": "3.5g", "serving_size": "0.12oz",
        "kind": "flower",
    },
    "7g Flower": {
        "suffix": "B", "net_weight": "7g", "serving_size": "0.25oz",
        "kind": "flower",
    },
    "1g Flower": {
        "suffix": "C", "net_weight": "1g", "serving_size": "0.035oz",
        "kind": "flower",
    },
    "1g Pre-Roll": {
        "suffix": "D", "net_weight": "1g", "serving_size": "0.035oz",
        "kind": "pre-roll",
    },
    "5pk Pre-Roll": {
        "suffix": "E", "net_weight": "3.5g", "serving_size": "0.12oz",
        "kind": "pre-roll",
    },
    "14g Flower": {
        "suffix": "F", "net_weight": "14g", "serving_size": "0.50oz",
        "kind": "flower",
    },
    "28g Flower": {
        "suffix": "G", "net_weight": "28g", "serving_size": "1.00oz",
        "kind": "flower",
    },
}

PACKAGE_FORMAT_OPTIONS = list(PACKAGE_FORMATS)
ZEBRA_PRINTER_OPTIONS = [
    "ZDesigner ZD621-203DPI ZPL",
    "ZDesigner ZD620-203DPI ZPL",
]


def _clean_text(value: Any, limit: int = 80) -> str:
    """Return ZPL-safe printable text with a conservative length limit."""
    text = re.sub(r"[\^~\\\r\n]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def extract_metrc_tags(value: Any) -> list[str]:
    """Extract unique METRC package tags in source order."""
    matches = re.findall(r"1A[A-Z0-9]{20,30}", str(value or "").upper())
    return list(dict.fromkeys(matches))


def extract_harvest_date(value: Any) -> date | None:
    """Read the first common date representation from a harvest/lot name."""
    text = str(value or "")
    patterns = [
        (r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)", "%m/%d/%Y"),
        (r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", "%Y/%m/%d"),
    ]
    for pattern, order in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            if order == "%m/%d/%Y":
                month, day, year = (int(part) for part in match.groups())
            else:
                year, month, day = (int(part) for part in match.groups())
            return date(year, month, day)
        except ValueError:
            continue
    return None


def parse_iso_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return extract_harvest_date(value)


def expiration_from_harvest(harvest_date: date) -> date:
    """Apply QCC's rule: six calendar months, then forty-five days."""
    month_index = harvest_date.month - 1 + 6
    year = harvest_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(harvest_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) + timedelta(days=45)


def default_package_format(sku_type: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", str(sku_type or "").lower()).strip()
    if "5" in key and ("pack" in key or "pk" in key) and "roll" in key:
        return "5pk Pre-Roll"
    if "pre roll" in key or "preroll" in key:
        return "1g Pre-Roll"
    for token, result in [
        ("28g", "28g Flower"), ("28 g", "28g Flower"),
        ("14g", "14g Flower"), ("14 g", "14g Flower"),
        ("7g", "7g Flower"), ("7 g", "7g Flower"),
        ("3 5g", "3.5g Flower"), ("3 5 g", "3.5g Flower"),
        ("1g", "1g Flower"), ("1 g", "1g Flower"),
    ]:
        if token in key:
            return result
    return "3.5g Flower"


def label_layout(brand: Any, package_format: str) -> str:
    definition = PACKAGE_FORMATS.get(package_format, {})
    if definition.get("kind") == "pre-roll":
        return "Pre-Roll Horizontal"
    if str(brand or "").strip().lower() == "clade9" and package_format == "3.5g Flower":
        return "Flower Vertical"
    return "Flower Horizontal"


def _normalized_analyte_name(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("delta-9", "d9").replace("delta 9", "d9")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _analyte_map(rows: list[dict[str, Any]]) -> dict[str, tuple[str, float]]:
    result: dict[str, tuple[str, float]] = {}
    for row in rows:
        name = str(row.get("Test", row.get("test_name", "")) or "").strip()
        number = _finite_number(row.get("Result", row.get("result")))
        key = _normalized_analyte_name(name)
        if key and number is not None and key not in result:
            result[key] = (name, number)
    return result


def _find_value(
    analytes: dict[str, tuple[str, float]], *patterns: str
) -> float | None:
    for pattern in patterns:
        exact = analytes.get(pattern)
        if exact:
            return exact[1]
    for key, (_name, value) in analytes.items():
        # Metrc exports can prefix the analyte with its panel/category and can
        # suffix it with the measurement basis (for example,
        # "Cannabinoids Total THC Percent"). Match the complete normalized
        # analyte phrase on token boundaries without allowing partial matches
        # such as THC matching THCA.
        if any(
            re.search(
                rf"(?:^|\s){re.escape(pattern)}(?:\s|$)",
                key,
            )
            for pattern in patterns
        ):
            return value
    return None


_TERPENE_TERMS = (
    "terpene", "myrcene", "limonene", "pinene", "caryophyllene", "linalool",
    "humulene", "terpinolene", "ocimene", "bisabolol", "camphene", "borneol",
    "eucalyptol", "farnesene", "geraniol", "guaiol", "nerolidol", "pulegone",
    "sabinene", "terpineol",
)


def _display_analyte_name(name: str) -> str:
    text = re.sub(r"\s*\(%\)\s*", " ", name, flags=re.I)
    text = re.sub(r"\braw\s+plant\s+material\b", " ", text, flags=re.I)
    text = re.sub(
        r"^\s*(?:terpenes?|cannabinoids?)\s*[-:]\s*",
        "",
        text,
        flags=re.I,
    )
    return _clean_text(text, 20).strip(" -:")


def _terpene_identity(key: str, display_name: str) -> str:
    """Collapse alternate Metrc names for the same individual terpene."""
    for phrase in ("alpha pinene", "beta pinene"):
        if phrase in key:
            return phrase
    for term in _TERPENE_TERMS:
        if term != "terpene" and term in key:
            return term
    return _normalized_analyte_name(display_name)


_TERPENE_DISPLAY_NAMES = {
    "limonene": "Limonene",
    "linalool": "Linalool",
    "alpha pinene": "Alpha-Pinene",
    "beta pinene": "Beta-Pinene",
    "pinene": "Pinene",
    "caryophyllene": "Caryophyllene",
    "myrcene": "Myrcene",
    "humulene": "Humulene",
    "terpinolene": "Terpinolene",
    "ocimene": "Ocimene",
    "bisabolol": "Bisabolol",
    "camphene": "Camphene",
    "borneol": "Borneol",
    "eucalyptol": "Eucalyptol",
    "farnesene": "Farnesene",
    "geraniol": "Geraniol",
    "guaiol": "Guaiol",
    "nerolidol": "Nerolidol",
    "pulegone": "Pulegone",
    "sabinene": "Sabinene",
    "terpineol": "Terpineol",
}


def label_analytes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    analytes = _analyte_map(rows)
    cbga = _find_value(
        analytes,
        "cbga",
        "cbga raw plant material",
    )
    cbg = _find_value(
        analytes,
        "cbg",
        "cbg raw plant material",
    )
    total_cbg = _find_value(analytes, "total cbg")
    if total_cbg is None and cbga is not None and cbg is not None:
        total_cbg = cbga * 0.877 + cbg
    values = {
        "total_cannabinoids": _find_value(analytes, "total cannabinoids"),
        "total_thc": _find_value(analytes, "total thc"),
        "thca": _find_value(analytes, "thca"),
        "total_cbd": _find_value(analytes, "total cbd"),
        "d9_thc": _find_value(
            analytes,
            "d9 thc",
            "delta 9 thc",
            "thc raw plant material",
        ),
        "total_cbg": total_cbg,
        "total_terpenes": _find_value(analytes, "total terpenes"),
    }
    terpene_by_identity: dict[str, tuple[str, float]] = {}
    for key, (name, value) in analytes.items():
        if (
            re.search(r"(?:^|\s)total terpenes(?:\s|$)", key)
            or not any(term in key for term in _TERPENE_TERMS)
        ):
            continue
        display_name = _display_analyte_name(name)
        identity = _terpene_identity(key, display_name)
        display_name = _TERPENE_DISPLAY_NAMES.get(identity, display_name)
        if display_name and identity and identity not in terpene_by_identity:
            terpene_by_identity[identity] = (display_name, value)
    terpene_rows = list(terpene_by_identity.values())
    terpene_rows.sort(key=lambda item: item[1], reverse=True)
    values["top_terpenes"] = terpene_rows[:3]
    total = values.get("total_terpenes")
    values["other_terpenes"] = (
        max(float(total) - sum(value for _name, value in terpene_rows[:3]), 0.0)
        if total is not None else None
    )
    return values


def _pct(value: Any) -> str:
    number = _finite_number(value)
    return f"{number:.2f}%" if number is not None else ""


def _short_date(value: date) -> str:
    return value.strftime("%m/%d/%y")


def prepare_label_context(
    package: dict[str, Any], analyte_rows: list[dict[str, Any]],
    package_format: str, bulk_uid: str = "", harvest_date: str = "",
    lot_number: str = "", quantity: int = 1,
) -> tuple[dict[str, Any], list[str]]:
    """Normalize and validate all data before any production ZPL is emitted."""
    errors: list[str] = []
    definition = PACKAGE_FORMATS.get(package_format)
    if not definition:
        errors.append("Choose a supported package format.")
        definition = PACKAGE_FORMATS["3.5g Flower"]

    source_tags = extract_metrc_tags(package.get("source_package_labels", ""))
    selected_uid = (extract_metrc_tags(bulk_uid) or source_tags or [""])[0]
    lab_tag = (extract_metrc_tags(package.get("package_tag", "")) or [""])[0]
    if not selected_uid:
        errors.append("The associated bulk source tag is missing.")
    elif selected_uid == lab_tag:
        errors.append("The printed UID must be the bulk source tag, not the laboratory sample tag.")
    if len(source_tags) > 1 and not bulk_uid:
        errors.append("Multiple source package tags were found; select the correct bulk source tag.")

    harvest = parse_iso_date(harvest_date) or extract_harvest_date(
        package.get("source_harvest_names", "")
    )
    if not harvest:
        errors.append("The harvest date is missing or could not be recognized.")
    expiration = expiration_from_harvest(harvest) if harvest else None
    analytes = label_analytes(analyte_rows)
    required = {
        "Total Cannabinoids": analytes.get("total_cannabinoids"),
        "Total THC": analytes.get("total_thc"),
        "THCA": analytes.get("thca"),
        "Total CBD": analytes.get("total_cbd"),
        "D9-THC": analytes.get("d9_thc"),
        "Total CBG": analytes.get("total_cbg"),
        "Total Terpenes": analytes.get("total_terpenes"),
    }
    missing = [label for label, value in required.items() if value is None]
    if missing:
        errors.append("Missing required lab values: " + ", ".join(missing) + ".")

    strain = _clean_text(package.get("strain", ""), 32)
    brand = _clean_text(package.get("brand", ""), 24)
    lot = _clean_text(
        lot_number
        or package.get("production_batch_number", "")
        or package.get("source_harvest_names", ""),
        42,
    )
    if not strain:
        errors.append("The strain/product name is missing.")
    if not lot:
        errors.append("The lot number is missing.")
    if str(package.get("qa_outcome", "")).lower() != "passed":
        errors.append("Only a passed compliance record can generate a production label.")

    try:
        print_quantity = max(1, min(int(quantity), 9999))
    except (TypeError, ValueError):
        print_quantity = 1
        errors.append("Print quantity must be a whole number.")

    total_thc = analytes.get("total_thc")
    total_cbd = analytes.get("total_cbd")
    chemotype = (
        "High THC, Low CBD"
        if total_thc is not None and total_cbd is not None and total_thc >= total_cbd
        else "Review Required"
    )
    return {
        "brand": brand,
        "strain": strain,
        "lab_tag": lab_tag,
        "bulk_uid": selected_uid,
        "suffix": definition["suffix"],
        "barcode_value": f"{selected_uid}-{definition['suffix']}" if selected_uid else "",
        "package_format": package_format,
        "layout": label_layout(brand, package_format),
        "net_weight": definition["net_weight"],
        "serving_size": definition["serving_size"],
        "harvest_date": harvest.isoformat() if harvest else "",
        "harvest_date_short": _short_date(harvest) if harvest else "",
        "expiration_date": expiration.isoformat() if expiration else "",
        "expiration_date_short": _short_date(expiration) if expiration else "",
        "lot_number": lot,
        "quantity": print_quantity,
        "pesticides": "NONE" if not errors and str(package.get("qa_outcome", "")).lower() == "passed" else "REVIEW",
        "chemotype": chemotype,
        "grow_method": "Indoor",
        "analytes": analytes,
    }, errors


def _header(width: int, length: int) -> str:
    return (
        "^XA\n^CI28\n^MMT\n^MTT\n^PR4,4\n~SD15\n^PW" + str(width)
        + "\n^LL" + str(length).zfill(4) + "\n^LS0\n"
    )


def _horizontal_flower_zpl(context: dict[str, Any]) -> str:
    a = context["analytes"]
    terpenes = list(a.get("top_terpenes", []))
    while len(terpenes) < 3:
        terpenes.append(("", None))
    return _header(457, 254) + f"""
^FT145,26^A0N,25,28^FD{_clean_text(context['strain'], 24)}^FS
^FT5,49^A0N,16,13^FDTotal Cannabinoids: {_pct(a.get('total_cannabinoids'))}^FS
^FT246,49^A0N,16,13^FDTotal Terpenes: {_pct(a.get('total_terpenes'))}^FS
^FT15,70^A0N,14,14^FDTotal THC: {_pct(a.get('total_thc'))}^FS
^FT15,87^A0N,14,14^FDTHCA: {_pct(a.get('thca'))}^FS
^FT15,104^A0N,14,14^FDTotal CBD: {_pct(a.get('total_cbd'))}^FS
^FT15,121^A0N,14,14^FDD9-THC: {_pct(a.get('d9_thc'))}^FS
^FT15,138^A0N,14,14^FDTotal CBG: {_pct(a.get('total_cbg'))}^FS
^FT220,70^A0N,14,12^FD{_clean_text(terpenes[0][0], 22)}: {_pct(terpenes[0][1])}^FS
^FT220,87^A0N,14,12^FD{_clean_text(terpenes[1][0], 22)}: {_pct(terpenes[1][1])}^FS
^FT220,104^A0N,14,12^FD{_clean_text(terpenes[2][0], 22)}: {_pct(terpenes[2][1])}^FS
^FT220,121^A0N,14,12^FDOther: {_pct(a.get('other_terpenes'))}^FS
^FT5,156^A0N,13,9^FDHarvest Date: {context['harvest_date_short']}  Expiration Date: {context['expiration_date_short']}^FS
^FT5,174^A0N,13,8^FDPesticides: {context['pesticides']}  Chemotype: {context['chemotype']}^FS
^FT5,193^A0N,13,11^FDLot #: {_clean_text(context['lot_number'], 40)}^FS
^FT5,211^A0N,13,11^FDUID: {context['bulk_uid']}^FS
^FT255,157^A0N,12,12^FDPKG by: The QCC Group LLC^FS
^FT269,174^A0N,12,12^FDGrow Method: {context['grow_method']}^FS
^FT269,191^A0N,12,12^FDClass 1 - Cultivator^FS
^FT260,208^A0N,12,12^FDLicense Number: C000313^FS
^BY1,3,20^FT185,227^BCN,,Y,N^FD{context['barcode_value']}^FS
^FT5,244^A0N,13,13^FDNet WT: {context['net_weight']}  Serving Size: {context['serving_size']}^FS
^PQ{context['quantity']},0,1,Y
^XZ
"""


def _vertical_flower_zpl(context: dict[str, Any]) -> str:
    a = context["analytes"]
    terpenes = list(a.get("top_terpenes", []))
    while len(terpenes) < 3:
        terpenes.append(("", None))
    terp_line_1 = (
        f"{_clean_text(terpenes[0][0], 22)}: {_pct(terpenes[0][1])}    "
        f"{_clean_text(terpenes[1][0], 22)}: {_pct(terpenes[1][1])}"
    )
    terp_line_2 = (
        f"{_clean_text(terpenes[2][0], 22)}: {_pct(terpenes[2][1])}    "
        f"Other: {_pct(a.get('other_terpenes'))}"
    )
    perforation_guide = "\n".join(
        f"^FO80,{offset}^GB5,16,5,B,0^FS" for offset in range(8, 249, 24)
    )
    return _header(457, 254) + f"""
^FT47,209^A0B,27,27^FD{_clean_text(context['strain'], 24)}^FS
{perforation_guide}
^FO100,2^A0B,19,14^FB250,1,0,C,0^FDTotal Cannabinoids: {_pct(a.get('total_cannabinoids'))}^FS
^FO101,2^A0B,19,14^FB250,1,0,C,0^FDTotal Cannabinoids: {_pct(a.get('total_cannabinoids'))}^FS
^FT133,248^A0B,16,12^FDTotal THC: {_pct(a.get('total_thc'))}  THCA: {_pct(a.get('thca'))}^FS
^FT153,248^A0B,16,12^FDTotal CBD: {_pct(a.get('total_cbd'))}  D9-THC: {_pct(a.get('d9_thc'))}^FS
^FT173,248^A0B,16,12^FDTotal CBG: {_pct(a.get('total_cbg'))}^FS
^FO194,2^A0B,17,14^FB250,1,0,C,0^FDTotal Terpenes: {_pct(a.get('total_terpenes'))}^FS
^FT220,244^A0B,15,6^FD{terp_line_1[:58]}^FS
^FT240,244^A0B,15,6^FD{terp_line_2[:58]}^FS
^FT269,238^A0B,15,8^FDHarvest Date: {context['harvest_date_short']}  Expiration Date: {context['expiration_date_short']}^FS
^FT290,250^A0B,15,7^FDPesticides: {context['pesticides']}  Chemotype: {context['chemotype']}^FS
^FT315,250^A0B,13,11^FDLot #: {_clean_text(context['lot_number'], 40)}^FS
^FT332,250^A0B,13,11^FDUID: {context['bulk_uid']}^FS
^FT357,206^A0B,13,11^FDPKG by: The QCC Group LLC^FS
^FT372,185^A0B,13,11^FDGrow Method: {context['grow_method']}^FS
^FT387,193^A0B,13,13^FDClass 1 - Cultivator^FS
^BY1,3,21^FT412,254^BCB,,Y,N^FD{context['barcode_value']}^FS
^FT437,212^A0B,13,13^FDLicense Number: C000313^FS
^FT451,212^A0B,13,11^FDNet WT: {context['net_weight']}  Serving Size: {context['serving_size']}^FS
^PQ{context['quantity']},0,1,Y
^XZ
"""


def _preroll_zpl(context: dict[str, Any]) -> str:
    a = context["analytes"]
    terpenes = list(a.get("top_terpenes", []))
    while len(terpenes) < 3:
        terpenes.append(("", None))
    return _header(812, 121) + f"""
^FT790,105^A0I,18,18^FD{_clean_text(context['strain'], 22)} - {context['package_format']}^FS
^FT790,84^A0I,13,11^FDTotal Cannabinoids: {_pct(a.get('total_cannabinoids'))}  Total Terpenes: {_pct(a.get('total_terpenes'))}^FS
^FT790,67^A0I,12,10^FDTotal THC: {_pct(a.get('total_thc'))}  THCA: {_pct(a.get('thca'))}  Total CBD: {_pct(a.get('total_cbd'))}^FS
^FT790,51^A0I,12,9^FDD9-THC: {_pct(a.get('d9_thc'))}  Total CBG: {_pct(a.get('total_cbg'))}^FS
^FT505,105^A0I,12,8^FD{_clean_text(terpenes[0][0], 20)}: {_pct(terpenes[0][1])}  {terpenes[1][0]}: {_pct(terpenes[1][1])}^FS
^FT505,88^A0I,12,8^FD{_clean_text(terpenes[2][0], 20)}: {_pct(terpenes[2][1])}  Other: {_pct(a.get('other_terpenes'))}^FS
^FT505,68^A0I,11,8^FDHarvest: {context['harvest_date_short']}  Expires: {context['expiration_date_short']}^FS
^FT505,51^A0I,11,8^FDPesticides: {context['pesticides']}  Chemotype: {context['chemotype']}^FS
^FT425,77^A0I,11,10^FDLot #: {_clean_text(context['lot_number'], 36)}^FS
^FT425,62^A0I,11,10^FDUID: {context['bulk_uid']}^FS
^BY1,3,17^FT274,26^BCI,,Y,N^FD{context['barcode_value']}^FS
^FT220,104^A0I,11,10^FDNet WT: {context['net_weight']}  Serving Size: {context['serving_size']}^FS
^FT190,87^A0I,10,11^FDClass 1 - Cultivator^FS
^FT190,72^A0I,10,10^FDPKG by: The QCC Group LLC^FS
^FT190,57^A0I,10,10^FDGrow Method: {context['grow_method']}^FS
^FT220,42^A0I,10,12^FDLicense Number: C000313^FS
^PQ{context['quantity']},0,1,Y
^XZ
"""


def build_zpl(context: dict[str, Any], errors: list[str]) -> str:
    if errors:
        raise ValueError(" ".join(errors))
    layout = context.get("layout")
    if layout == "Flower Vertical":
        return _vertical_flower_zpl(context)
    if layout == "Flower Horizontal":
        return _horizontal_flower_zpl(context)
    if layout == "Pre-Roll Horizontal":
        return _preroll_zpl(context)
    raise ValueError("A supported Zebra label layout could not be selected.")
