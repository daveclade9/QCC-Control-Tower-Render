"""QCC Control Tower Reflex 0.9.5 - inventory, planning, and QA workspace."""

from __future__ import annotations

import csv
import asyncio
import io
import json
import math
import re
from html import escape
from calendar import month_name
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any, TypedDict
from urllib.parse import parse_qs, quote_plus

import reflex as rx
import pandas as pd

from .data import (
    calculate_flower_batch_mix,
    calculate_single_output_yield,
    create_clone_allocation,
    create_reflex_production_template,
    create_reflex_production_plan,
    database_url,
    delete_reflex_production_plans,
    demo_dashboard_data,
    get_dashboard_data,
    get_distribution_operations_data,
    get_sales_dashboard_data,
    import_lab_results_bytes,
    import_lab_summary_bytes,
    load_adjusted_coa,
    load_qa_analytes,
    load_qa_module_data,
    load_clone_allocations,
    load_clone_plans,
    load_fresh_frozen_adjustments,
    load_latest_metrc_plant_snapshot,
    load_package_lineage,
    save_clone_plan,
    save_creative_use_adjustment,
    save_fresh_frozen_adjustment,
    save_metrc_plant_snapshot,
    load_production_module_data,
    log_qa_label_download,
    save_adjusted_coa,
    potential_wip_for_sku,
    PRODUCTION_LINE_OPTIONS,
    PRODUCTION_PLAN_STATUSES,
    QA_LABEL_FIELDS,
    production_recipe_type,
    production_unit_weight_grams,
)
from .auth import (
    EMPLOYEE_ROLES,
    SESSION_HOURS,
    auth_is_configured,
    create_employee_profile,
    create_app_session,
    list_employee_directory,
    load_active_employee,
    oauth_authorize_url,
    public_app_url,
    revoke_app_session,
    update_employee_access,
    validate_app_session,
    verify_supabase_access_token,
)
from .label_catalog import NICE_LABEL_CATALOG
from .zebra_labels import (
    PACKAGE_FORMAT_OPTIONS,
    ZEBRA_PRINTER_OPTIONS,
    build_zpl,
    default_package_format,
    extract_harvest_date,
    extract_metrc_tags,
    label_analytes,
    chop_percent,
    adjusted_other_terpenes,
    prepare_label_context,
)
from .retailer_directory import (
    find_clade9_location,
    normalized_retailer_name,
)
from .rules import (
    UNFINISHED_INVENTORY_STAGES,
    compatible_inventory_brand,
    normalize_strain_name,
)
from .cultivation import (
    BenchPlan,
    CLADE9_CLONE_STRAINS,
    CLONE_PLANNING_FIRST_CROP,
    CLONE_PLANNING_FIRST_CUT_DATE,
    CRAFT_KINGS_CLONE_STRAINS,
    DEFAULT_POST_HARVEST_DAYS,
    HISTORICAL_CLONE_ALLOCATIONS,
    SCHEDULED_SUPPLY_EXPIRY_DAYS,
    UPCOMING_CROP_ALLOCATIONS,
    active_plant_harvest_date,
    bench_plant_capacity,
    approved_clone_plan_for_crop,
    cultivation_timeline,
    clone_plan_edit_window,
    clone_plan_is_editable,
    clone_planning_periods,
    prior_clone_planning_periods,
    crop_is_scheduled_supply,
    cultivation_flower_supply_bucket,
    default_split_percentages,
    exact_bench_allocations,
    estimated_yield_pounds,
    inventory_counts_as_current_cultivation_supply,
    normalized_strain,
    projected_harvest_dates,
    projected_risk,
    scheduled_supply_reconciliation,
    recommend_clone_trays,
    room_bench_plans,
    sku_fill_grams,
    valid_bench_equivalent,
)
from .historical_yield import (
    HISTORICAL_CYCLE_COLUMNS,
    HISTORICAL_HARVEST_COLUMNS,
    HISTORICAL_ROOM_COLUMNS,
    HISTORICAL_STRAIN_COLUMNS,
    HISTORICAL_STRAIN_OBSERVATIONS,
    historical_cycle_table_data,
    historical_cycle_rows,
    historical_harvest_table_data,
    historical_harvest_rows,
    historical_kpis,
    historical_room_chart_rows,
    historical_room_table_data,
    historical_room_rows,
    historical_strain_chart_rows,
    historical_strain_options,
    historical_strain_rows,
    historical_strain_table_data,
)
from .cultivation_registry import (
    DEFAULT_FUTURE_CROPS,
    OVERHEAD_LIGHTING_TYPES,
    SUPPLEMENTAL_LIGHTING_TYPES,
    calculate_bench_metrics,
    calculate_lighting_total,
    calculate_room_metrics,
    current_schedule_row,
    default_bench_rows,
    default_cycle_program,
    default_room_rows,
    default_schedule,
    fresh_frozen_canopy,
    generate_schedule,
    load_registry,
    rename_schedule_crop,
    save_bench,
    save_cycle_program,
    save_historical_yield,
    save_room,
    save_schedule_rows,
    set_current_schedule,
)
from .plant_data import crop_code, parse_metrc_plant_exports, plant_crop_reconciliation
from .sales_menu import BuyerMenuState, buyer_menu_page, sales_menu_admin_panel
from .ai_demand import ai_two_week_demand_forecast
from .wip_report import build_wip_rollforward_workbook


PILOT_VERSION = "0.9.6.26-staging"
ACCENT = "#14969b"
DARK = "#111827"
MUTED = "#64748b"
SURFACE = "#ffffff"
BACKGROUND = "#f4f7fa"

QA_ANALYTE_CATEGORIES = [
    "All Categories", "Cannabinoids", "Terpenes", "Mycotoxins",
    "Heavy Metals", "Pesticides", "Microbials", "Water Activity",
    "Other / Needs Review",
]


def _cultivation_registry_setter(field_name: str, numeric_kind: str = ""):
    """Build small typed Reflex events for the registry editor controls."""
    def setter(self, value: str):
        if numeric_kind == "int":
            try:
                setattr(self, field_name, int(float(value or 0)))
            except (TypeError, ValueError):
                setattr(self, field_name, 0)
        elif numeric_kind == "float":
            try:
                setattr(self, field_name, float(value or 0))
            except (TypeError, ValueError):
                setattr(self, field_name, 0.0)
        else:
            setattr(self, field_name, str(value or ""))
    setter.__name__ = f"set_{field_name}"
    return setter


def _retail_map_document(
    stores: list[dict[str, Any]], starting_address: str
) -> str:
    """Build the self-contained multi-marker retail availability map."""
    store_json = json.dumps(stores, ensure_ascii=True).replace("<", "\\u003c")
    origin_json = json.dumps(starting_address, ensure_ascii=True).replace(
        "<", "\\u003c"
    )
    document = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    *{box-sizing:border-box} body{margin:0;font-family:Arial,sans-serif;color:#111827;background:#fff}
    #layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);height:560px}
    #map{min-height:360px} #side{overflow:auto;border-left:1px solid #dbe5ec;padding:14px;background:#f8fafc}
    #status{font-size:13px;color:#64748b;margin:0 0 12px}.shop{background:#fff;border:1px solid #dbe5ec;border-radius:9px;padding:10px;margin-bottom:9px}
    .shop h3{font-size:14px;margin:0 0 5px}.shop p{font-size:12px;color:#64748b;margin:3px 0;line-height:1.35}
    .distance{font-weight:700;color:#0f766e!important}.shop a{display:inline-block;margin-top:6px;color:#0f766e;font-size:12px;font-weight:700;text-decoration:none}
    .leaflet-popup-content h3{margin:0 0 5px;font-size:14px}.leaflet-popup-content p{margin:3px 0;font-size:12px}
    @media(max-width:720px){#layout{grid-template-columns:1fr;grid-template-rows:360px auto;height:auto}#side{border-left:0;border-top:1px solid #dbe5ec;max-height:360px}}
  </style>
</head>
<body>
<div id="layout"><div id="map"></div><aside id="side"><p id="status">Preparing matching shop map…</p><div id="shops"></div></aside></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const stores=__STORE_DATA__;
const originQuery=__ORIGIN_DATA__;
const statusNode=document.getElementById('status');
const shopsNode=document.getElementById('shops');
const map=L.map('map',{scrollWheelZoom:false}).setView([40.15,-74.55],8);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
const bounds=[];
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let lastLookupAt=0;
const clean=value=>String(value||'').replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
async function geocode(query,useCache=false){
  if(!query)return null;
  const cacheKey='qcc-retail-geo:'+query.toLowerCase();
  if(useCache){
    try{const cached=localStorage.getItem(cacheKey);if(cached)return JSON.parse(cached);}catch(error){}
  }
  const wait=Math.max(0,1050-(Date.now()-lastLookupAt));
  if(wait)await pause(wait);
  const url='https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=us&q='+encodeURIComponent(query);
  lastLookupAt=Date.now();
  const response=await fetch(url,{headers:{'Accept':'application/json','Accept-Language':'en-US'}});
  if(!response.ok)throw new Error('Address lookup failed');
  const result=await response.json();
  if(!result.length)return null;
  const point={lat:Number(result[0].lat),lng:Number(result[0].lon)};
  if(useCache){try{localStorage.setItem(cacheKey,JSON.stringify(point));}catch(error){}}
  return point;
}
function miles(a,b){
  const rad=value=>value*Math.PI/180, earth=3958.8;
  const dLat=rad(b.lat-a.lat),dLng=rad(b.lng-a.lng);
  const value=Math.sin(dLat/2)**2+Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dLng/2)**2;
  return 2*earth*Math.asin(Math.sqrt(value));
}
function directionsUrl(store){
  const base='https://www.google.com/maps/dir/?api=1&destination='+encodeURIComponent(store.route_address);
  return originQuery?base+'&origin='+encodeURIComponent(originQuery):base;
}
function render(rows){
  shopsNode.innerHTML=rows.map(row=>{
    const distance=row.distance==null?'':`<p class="distance">${row.distance.toFixed(1)} miles away</p>`;
    return `<div class="shop"><h3>${clean(row.retailer)}</h3>${distance}<p>${clean(row.address)}</p><p>${clean(row.date_label)}: ${clean(row.last_delivery)}</p><a target="_blank" rel="noopener" href="${directionsUrl(row)}">Directions to this shop</a></div>`;
  }).join('');
}
function addStore(store,point,origin,mapped){
  const row={...store,...point,distance:origin?miles(origin,point):null};
  mapped.push(row); bounds.push([point.lat,point.lng]);
  const distance=row.distance==null?'':`<p><strong>${row.distance.toFixed(1)} miles from the starting location</strong></p>`;
  L.marker([point.lat,point.lng]).addTo(map).bindPopup(`<h3>${clean(row.retailer)}</h3>${distance}<p>${clean(row.address)}</p><p><a target="_blank" rel="noopener" href="${directionsUrl(row)}">Directions</a></p>`);
}
async function load(){
  if(!stores.length){statusNode.textContent='No matching retailer locations are available.';return;}
  let origin=null;
  if(originQuery){
    statusNode.textContent='Locating the starting address…';
    try{origin=await geocode(originQuery,false);}catch(error){origin=null;}
    if(origin){
      L.circleMarker([origin.lat,origin.lng],{radius:9,color:'#0f766e',fillColor:'#14b8a6',fillOpacity:1}).addTo(map).bindPopup('<strong>Starting location</strong><br>'+clean(originQuery));
      bounds.push([origin.lat,origin.lng]);
    }
  }
  const mapped=[];
  const unresolved=[];
  for(const store of stores){
    const hasSavedCoordinates=store.latitude!==null&&store.latitude!==''&&store.longitude!==null&&store.longitude!=='';
    const lat=Number(store.latitude),lng=Number(store.longitude);
    if(hasSavedCoordinates&&Number.isFinite(lat)&&Number.isFinite(lng)&&Math.abs(lat)<=90&&Math.abs(lng)<=180){
      addStore(store,{lat,lng},origin,mapped);
    }else{unresolved.push(store);}
  }
  render([...mapped].sort((a,b)=>(a.distance??99999)-(b.distance??99999)||a.retailer.localeCompare(b.retailer)));
  const fallback=unresolved.slice(0,15);
  for(let index=0;index<fallback.length;index++){
    const store=fallback[index];
    statusNode.textContent=`Mapping saved locations and checking address ${index+1} of ${fallback.length}…`;
    try{
      const point=await geocode(store.route_address,true);
      if(point)addStore(store,point,origin,mapped);
    }catch(error){}
    render([...mapped].sort((a,b)=>(a.distance??99999)-(b.distance??99999)||a.retailer.localeCompare(b.retailer)));
  }
  if(bounds.length)map.fitBounds(bounds,{padding:[28,28],maxZoom:13});
  const needsReview=Math.max(0,stores.length-mapped.length);
  const reviewText=needsReview?` ${needsReview} still need location review.`:'';
  if(originQuery&&!origin){statusNode.textContent=`The starting address could not be located. ${mapped.length} matching shops are shown without distances.${reviewText}`;}
  else if(origin){statusNode.textContent=`${mapped.length} matching shops located and sorted by straight-line distance.${reviewText}`;}
  else{statusNode.textContent=`${mapped.length} matching shops located. Enter a starting address to calculate distance.${reviewText}`;}
}
load();
</script>
</body>
</html>"""
    return document.replace("__STORE_DATA__", store_json).replace(
        "__ORIGIN_DATA__", origin_json
    )


CalendarEvent = TypedDict(
    "CalendarEvent",
    {
        "Target Date": str,
        "Plan ID": str,
        "Plan Name": str,
        "Status": str,
        "Department": str,
        "Production Line": str,
        "Line Color": str,
        "Line Background": str,
        "Brand": str,
        "Strain": str,
        "SKU Type": str,
        "Output Summary": str,
    },
)
EmployeeDirectoryRow = TypedDict(
    "EmployeeDirectoryRow",
    {
        "Employee ID": str,
        "Name": str,
        "Title": str,
        "Role": str,
        "Active": bool,
        "Primary Email": str,
        "Login Emails": str,
        "Phone": str,
        "Contact Email": str,
    },
)
RetailMapLocation = TypedDict(
    "RetailMapLocation",
    {
        "Retailer": str,
        "Destination License": str,
        "Address": str,
        "Latest Metrc Date": str,
        "Date Label": str,
        "Website": str,
        "Map URL": str,
        "Route Address": str,
        "Latitude": float | None,
        "Longitude": float | None,
        "Match Method": str,
        "Coordinate Status": str,
        "Verified": bool,
        "Location Status": str,
        "Notes": str,
    },
)
CalendarDay = TypedDict(
    "CalendarDay",
    {
        "Day": str,
        "Date": str,
        "In Month": bool,
        "Plans": list[CalendarEvent],
    },
)
SavedPlanCard = TypedDict(
    "SavedPlanCard",
    {
        "Plan ID": str,
        "Plan Name": str,
        "Status": str,
        "Target Date": str,
        "Department": str,
        "Production Line": str,
        "Line Color": str,
        "Line Background": str,
        "Batch Weight (g)": float,
        "Created By": str,
        "Brand": str,
        "Strain": str,
        "SKU Type": str,
        "Output Summary": str,
        "Outputs": list[list[Any]],
        "Sources": list[list[Any]],
        "Source Count": int,
        "Target Brand": str,
        "Target Strain": str,
        "Target SKU Type": str,
        "Recipe Type": str,
        "Notes": str,
        "Process Loss %": float,
        "Overfill %": float,
        "QA Retention (g)": float,
        "Smalls/Shake %": float,
        "Unit Fill Weight (g)": float,
        "Formulation Details": str,
    },
)
CultivationAllocationRow = TypedDict(
    "CultivationAllocationRow",
    {
        "id": str,
        "bench": str,
        "strain": str,
        "square_feet": float,
        "target_plants": int,
        "requested_overage_percent": int,
        "trays": int,
        "recommended_clones": int,
        "actual_overage_percent": float,
    },
)
ScheduledSupplyDetail = TypedDict(
    "ScheduledSupplyDetail",
    {
        "crop": str,
        "room": str,
        "strain": str,
        "harvest_date": str,
        "available_date": str,
        "gross_projected_lbs": float,
        "planned_fresh_frozen_plants": int,
        "actual_fresh_frozen_plants": int,
        "actual_fresh_frozen_detected": bool,
        "actual_fresh_frozen_wet_lbs": float,
        "actual_fresh_frozen_batches": str,
        "fresh_frozen_source": str,
        "fresh_frozen_plants": int,
        "planted_plants": int,
        "fresh_frozen_percent": float,
        "fresh_frozen_reduction_lbs": float,
        "creative_use_reduction_lbs": float,
        "net_projected_lbs": float,
        "actual_processed_lbs": float,
        "unconfirmed_remainder_lbs": float,
        "forecast_counted_lbs": float,
        "expired": bool,
        "actual_detected": bool,
        "status": str,
        "can_edit_fresh_frozen": bool,
        "can_edit_creative_use": bool,
    },
)
ClonePlanMatrixValue = TypedDict(
    "ClonePlanMatrixValue",
    {
        "value": float,
        "highlight": bool,
        "show_breakdown": bool,
        "cpg_lbs": float,
        "wip_lbs": float,
        "pre_wip_lbs": float,
        "current_total_lbs": float,
        "available": bool,
        "editable_allocation": bool,
        "historical_allocation": bool,
        "historical_editable": bool,
        "crop": str,
        "scheduled_details": list[ScheduledSupplyDetail],
    },
)
ClonePlanMatrixRow = TypedDict(
    "ClonePlanMatrixRow",
    {
        "strain": str,
        "metric": str,
        "allocation": float,
        "weekly_demand": float,
        "values": list[ClonePlanMatrixValue],
    },
)


class DashboardState(rx.State):
    """Per-user dashboard state loaded from the shared Supabase database."""

    auth_session_token: str = rx.Cookie(
        "",
        name="qcc_auth_session",
        max_age=SESSION_HOURS * 60 * 60,
        path="/",
        same_site="strict",
        secure=public_app_url().startswith("https://"),
    )
    auth_checked: bool = False
    authenticated: bool = False
    auth_configured: bool = auth_is_configured()
    auth_message: str = ""
    auth_email: str = ""
    auth_employee_id: str = ""
    auth_name: str = ""
    auth_title: str = ""
    auth_role: str = ""
    auth_provider: str = ""
    auth_redirecting: bool = False
    auth_failed: bool = False
    team_members: list[EmployeeDirectoryRow] = []
    admin_message: str = ""
    admin_error: str = ""
    admin_new_name: str = ""
    admin_new_title: str = ""
    admin_new_primary_email: str = ""
    admin_new_alternate_email: str = ""
    admin_new_role: str = "Sales"
    admin_selected_employee_id: str = ""
    admin_selected_role: str = "Sales"
    admin_selected_active: bool = True
    admin_selected_name: str = ""
    admin_selected_title: str = ""
    admin_selected_primary_email: str = ""
    admin_selected_login_emails: str = ""
    admin_additional_email: str = ""

    loading: bool = False
    sales_background_loading: bool = False
    error_message: str = ""
    using_demo_data: bool = False
    initial_data_loading: bool = False
    loaded_at: str = "Not loaded"
    rule_version: str = "QCC Control Tower 81.4 shared inventory rules"
    brand_filter: str = "All Brands"
    strain_filter: str = "All Strains"
    sku_filter: str = "All SKU Types"
    search_text: str = ""
    global_filters_resetting: bool = False
    qa_view: str = "cultivation"
    quality_view: str = "cultivation"
    distribution_view: str = "customers"
    # Large source collections stay backend-only. Only filtered display data
    # is synchronized through the websocket.
    _qa_packages: list[dict[str, Any]] = []
    qa_templates: list[dict[str, Any]] = []
    qa_import_log: list[list[Any]] = []
    qa_lab_direct_summary: list[list[Any]] = []
    qa_record_count: int = 0
    qa_analyte_count: int = 0
    qa_cultivation_test_type: str = "All Test Types"
    qa_manufacturing_test_type: str = "All Test Types"
    qa_cultivation_pass_rows_per_page: str = "10"
    qa_cultivation_potency_rows_per_page: str = "10"
    qa_cultivation_detail_rows_per_page: str = "10"
    qa_manufacturing_pass_rows_per_page: str = "10"
    qa_manufacturing_potency_rows_per_page: str = "10"
    qa_manufacturing_detail_rows_per_page: str = "10"
    qa_cultivation_consistency_strain: str = ""
    qa_manufacturing_consistency_strain: str = ""
    qa_lookup_draft: str = ""
    qa_lookup_search: str = ""
    qa_lookup_selection: str = ""
    qa_selected_package: dict[str, Any] = {}
    qa_preview_open: bool = False
    qa_selected_analytes: list[dict[str, Any]] = []
    qa_selected_analytes_loading: bool = False
    qa_analyte_message: str = ""
    qa_analyte_category_filter: str = "All Categories"
    qa_lookup_loading: bool = False
    qa_selected_template: str = ""
    qa_override_expiration: bool = False
    qa_manual_expiration: str = ""
    qa_message: str = ""
    qa_error: str = ""
    qa_importing: bool = False
    qa_import_results: list[list[Any]] = []
    qa_loading: bool = False
    qa_loaded: bool = False
    qa_label_catalog: list[dict[str, Any]] = NICE_LABEL_CATALOG
    qa_label_operation_filter: str = "All Operations"
    qa_label_brand_filter: str = "All Brands"
    qa_label_strain_filter: str = "All Strains"
    qa_label_sku_filter: str = "All SKU Types"
    qa_label_catalog_search: str = ""
    qa_selected_native_template: str = ""
    qa_recent_selections: list[dict[str, str]] = []
    qa_zebra_package_format: str = "3.5g Flower"
    qa_zebra_bulk_uid: str = ""
    qa_zebra_harvest_date: str = ""
    qa_zebra_lot_number: str = ""
    qa_zebra_printer: str = ZEBRA_PRINTER_OPTIONS[0]
    qa_zebra_quantity: int = 1
    qa_zebra_message: str = ""
    qa_zebra_error: str = ""
    qa_adjusted_coa_open: bool = False
    qa_adjusted_coa_saving: bool = False
    qa_adjusted_coa: dict[str, Any] = {}
    qa_adjusted_total_terpenes: str = ""
    qa_adjusted_total_cbg: str = ""
    qa_adjusted_terpene_names: list[str] = ["", "", ""]
    qa_adjusted_terpene_values: list[str] = ["", "", ""]
    qa_adjusted_metrc_total_terpenes: str = ""
    qa_adjusted_metrc_total_cbg: str = ""
    qa_adjusted_metrc_terpene_values: list[str] = ["", "", ""]
    qa_adjusted_source_label: str = "Metrc"
    qa_adjusted_coa_message: str = ""
    qa_adjusted_coa_error: str = ""
    inventory_stage_filter: str = "All Production Stages"
    inventory_license_filter: str = "All Licenses"
    inventory_qa_filter: str = "All QA Statuses"
    inventory_category_filter: str = "All Categories"
    inventory_location_filter: str = "All Locations"
    inventory_ownership_filter: str = "All Ownership Statuses"
    inventory_include_retention: bool = False
    summarize_cpg_inventory: bool = False
    summarize_bulk_inventory: bool = False
    summarize_wip_inventory: bool = False
    summarize_aging_cpg: bool = False
    summarize_aging_bulk: bool = False
    summarize_all_inventory: bool = False
    summarize_needs_review: bool = False
    inventory_weight_unit: str = "Pounds"
    aging_cpg_band_filter: str = "All Risk Bands"
    aging_bulk_band_filter: str = "All Age Bands"
    executive_facility_filter: str = "All Facilities"
    executive_ownership_filter: str = "QCC-Owned Inventory"
    executive_view: str = "overview"
    executive_report_message: str = ""
    executive_report_error: str = ""
    executive_report_building: bool = False
    inventory_lookup_text: str = ""
    inventory_lookup_message: str = "Enter a complete Metrc tag to inspect one package."
    selected_inventory_details: list[list[str]] = []
    sku_detail_open: bool = False
    sku_detail_title: str = "SKU Package Details"
    sku_detail_message: str = ""
    selected_sku_package_details: list[list[str]] = []

    production_brand: str = ""
    production_strain: str = ""
    production_sku: str = ""
    production_selected_tags: list[str] = []
    production_batch_weight: float = 0.0
    production_mix_28: float = 0.0
    production_mix_14: float = 0.0
    production_mix_7: float = 20.0
    production_mix_35: float = 60.0
    production_mix_1: float = 10.0
    production_mix_smalls: float = 7.0
    production_mix_loss: float = 3.0
    production_output_brand_28: str = "Royal Smalls"
    production_output_brand_14: str = "Craft Kings"
    production_output_brand_7: str = "Craft Kings"
    production_output_brand_35: str = "Royal Smalls"
    production_output_brand_1: str = "Craft Kings"
    production_unit_weight: float = 1.0
    production_overfill_percent: float = 2.0
    production_process_loss_percent: float = 3.0
    production_qa_retention_grams: float = 1.0
    production_gummy_piece_weight: float = 4.0
    production_gummies_per_package: int = 10
    production_plan_name: str = ""
    production_target_date: str = (date.today() + timedelta(days=7)).isoformat()
    production_plan_status: str = "Planned"
    production_plan_notes: str = ""
    production_assigned_department: str = "Production"
    production_line: str = "Flower Line 1"
    production_scenario_name: str = "Current Mix"
    production_scenarios: list[list[Any]] = []
    production_save_message: str = ""
    production_save_error: str = ""
    production_saving: bool = False
    production_data_loading: bool = False
    production_module_loaded: bool = False
    production_last_saved_plan_id: str = ""
    production_edit_plan_id: str = ""
    production_view: str = "build"
    production_material_filter: str = "All Eligible Materials"
    production_source_strain_filter: str = "All Source Strains"
    production_selected_source_strains: list[str] = []
    production_source_location_filter: str = "All Source Locations"
    production_source_sort: str = "Oldest First"
    production_source_search: str = ""
    production_source_min_weight: float = 0.0
    production_template_choice: str = "No Template"
    production_action_message: str = ""
    production_action_error: str = ""
    production_selected_plan_ids: list[str] = []
    workspace_view: str = "executive"
    sales_demand_view: str = "overview"
    inventory_view_name: str = "cpg"
    inventory_page: int = 1
    inventory_page_size: int = 10
    executive_action_rows_per_page: str = "10"
    top_sku_rows_per_page: str = "10"
    stockout_rows_per_page: str = "10"
    customer_rows_per_page: str = "10"
    transfer_import_rows_per_page: str = "10"
    transfer_rows_per_page: str = "10"
    exception_rows_per_page: str = "10"
    transfer_page: int = 1
    transfer_page_size: int = 10
    transfer_server_total: int = 0
    exception_page: int = 1
    exception_page_size: int = 10
    exception_server_total: int = 0
    exception_server_manifests: int = 0
    exception_server_value: float = 0.0
    distribution_loading: bool = False
    distribution_error: str = ""
    distribution_request_revision: int = 0
    sku_planning_page: int = 1
    sku_planning_page_size: int = 10
    sku_planning_sort: str = "Avg Weekly Units - High to Low"
    sku_velocity_period: str = "All Time"
    sku_use_availability_adjusted: bool = False
    demand_lifecycle_filter: str = "Active Products Only"
    sales_loaded_views: list[str] = []
    velocity_windows: dict[str, list[dict[str, Any]]] = {}
    availability_adjusted_velocity_windows: dict[
        str, list[dict[str, Any]]
    ] = {}
    saved_plan_search: str = ""
    saved_plan_status_filter: str = "All Plan Statuses"
    retail_timeframe: str = "4 Weeks"
    retail_show_pending: bool = False
    retail_brand_filter: str = "All Brands"
    retail_strain_filter: str = "All Strains"
    retail_sku_filter: str = "All SKU Types"
    retail_customer_filter: str = "All Retailers"
    retail_start_address_input: str = ""
    retail_start_address: str = ""

    cultivation_view: str = "clone_planning"
    cultivation_history_room_filter: str = "All Flower Rooms"
    cultivation_history_table_view: str = "Individual Harvests"
    cultivation_history_rows_per_page: str = "10"
    cultivation_history_strain_filter: str = "All Strains"
    cultivation_demand_brand_filter: str = "All Brands"
    cultivation_demand_strain_filter: str = "All Strains"
    cultivation_demand_sku_filter: str = "All Compared SKUs"
    cultivation_demand_rows_per_page: str = "10"
    cultivation_clone_strain_scope: str = "Clade9 Strains"
    cultivation_new_strain_name: str = ""
    cultivation_provisional_strains: list[str] = ["Hood Candy", "Jelly Cake"]
    cultivation_new_strain_message: str = ""
    cultivation_new_strain_error: str = ""
    cultivation_clone_plan_demand_model: str = "Availability-Adjusted"
    cultivation_clone_plan_product_scope: str = "Flower + Pre-Rolls"
    cultivation_clone_plan_include_pre_wip: bool = False
    cultivation_clone_plan_demand_revision: int = 0
    cultivation_clone_plan_allocations: dict[str, float] = {}
    cultivation_clone_plan_entry_version: int = 0
    cultivation_clone_plan_status: str = "Draft"
    cultivation_clone_plan_dirty: bool = False
    cultivation_clone_plan_override: bool = False
    cultivation_clone_plan_override_reason: str = ""
    cultivation_clone_plan_saving: bool = False
    cultivation_clone_plan_message: str = ""
    cultivation_clone_plan_error: str = ""
    cultivation_clone_plan_history: list[dict[str, Any]] = []
    cultivation_clone_plan_history_loaded: bool = False
    cultivation_clone_plan_lookback: str = "No Historical Crops"
    cultivation_fresh_frozen_adjustments: dict[str, int] = {}
    cultivation_creative_use_adjustments: dict[str, float] = {}
    cultivation_fresh_frozen_saving: bool = False
    cultivation_historical_plan_crop: str = ""
    cultivation_historical_plan_allocations: dict[str, float] = {}
    cultivation_historical_plan_edit_id: str = ""
    cultivation_historical_plan_edit_status: str = "Approved"
    cultivation_historical_plan_editing: bool = False
    cultivation_historical_plan_saving: bool = False
    cultivation_historical_plan_entry_version: int = 0
    _cultivation_plant_snapshot: dict[str, Any] = {}
    cultivation_plant_snapshot_loaded: bool = False
    # The normalized snapshot stays backend-only because it can contain tens of
    # thousands of plant records.  Cached computed vars cannot observe changes
    # to a backend-only var, so this lightweight public revision invalidates the
    # plant tables after a load or import without sending the full snapshot to
    # the browser.
    cultivation_plant_snapshot_revision: int = 0
    cultivation_plant_importing: bool = False
    cultivation_plant_message: str = ""
    cultivation_plant_error: str = ""
    cultivation_plant_facility_filter: str = "All Facilities"
    cultivation_plant_phase_filter: str = "All Phases"
    cultivation_plant_location_filter: str = "All Locations"
    cultivation_plant_strain_filter: str = "All Strains"
    cultivation_plant_view: str = "active_plants"
    cultivation_plant_rows_per_page: str = "10"
    cultivation_flower_room: str = "Flower Room 1"
    cultivation_cycle_name: str = ""
    cultivation_flower_entry_date: str = (
        date.today() + timedelta(days=40)
    ).isoformat()
    cultivation_plant_density: float = 0.75
    cultivation_overage_percent: int = 30
    cultivation_post_harvest_days: int = DEFAULT_POST_HARVEST_DAYS
    cultivation_layout_editing: bool = False
    cultivation_bench_plans: list[BenchPlan] = room_bench_plans("Flower Room 1")
    cultivation_saved_allocations: list[dict[str, Any]] = []
    cultivation_saved_loaded: bool = False
    cultivation_saving: bool = False
    cultivation_bench_label: str = ""
    cultivation_strain: str = ""
    cultivation_bench_square_feet: float = 0.0
    cultivation_allocations: list[CultivationAllocationRow] = []
    cultivation_message: str = ""
    cultivation_error: str = ""
    _cultivation_registry: dict[str, list[dict[str, Any]]] = {}
    cultivation_registry_loaded: bool = False
    cultivation_registry_revision: int = 0
    cultivation_registry_message: str = ""
    cultivation_registry_error: str = ""
    cultivation_schedule_program: str = "main-five-room"
    cultivation_schedule_start_crop: str = CLONE_PLANNING_FIRST_CROP
    cultivation_schedule_first_cut: str = CLONE_PLANNING_FIRST_CUT_DATE.isoformat()
    cultivation_schedule_count: int = DEFAULT_FUTURE_CROPS
    cultivation_schedule_preview: list[dict[str, Any]] = []
    cultivation_current_schedule_id: str = ""
    cultivation_current_crop_draft: str = ""
    cultivation_schedule_saving: bool = False
    cultivation_program_name: str = "Main F1-F5 Rotation"
    cultivation_program_code_prefix: str = "F"
    cultivation_program_cadence_days: int = 14
    cultivation_program_rooting_days: int = 21
    cultivation_program_veg_days: int = 19
    cultivation_program_flowering_days: int = 68
    cultivation_program_processing_days: int = 30
    cultivation_program_target_crops: int = DEFAULT_FUTURE_CROPS
    cultivation_program_room_rotation: str = "Flower Room 1, Flower Room 2, Flower Room 3, Flower Room 4, Flower Room 5"
    cultivation_room_edit_id: str = ""
    cultivation_room_code: str = ""
    cultivation_room_name: str = ""
    cultivation_room_building: str = "1A"
    cultivation_room_program: str = "main-five-room"
    cultivation_room_length: float = 0.0
    cultivation_room_width: float = 0.0
    cultivation_room_height: float = 0.0
    cultivation_room_overhead_type: str = "LED"
    cultivation_room_overhead_other: str = ""
    cultivation_room_fixture_count: int = 0
    cultivation_room_watts_fixture: float = 0.0
    cultivation_room_watts_override: float = 0.0
    cultivation_room_effective_date: str = ""
    cultivation_room_notes: str = ""
    cultivation_bench_edit_id: str = ""
    cultivation_bench_room_id: str = "flower-room-1"
    cultivation_bench_name: str = ""
    cultivation_bench_length: float = 0.0
    cultivation_bench_width: float = 0.0
    cultivation_bench_density: float = 0.75
    cultivation_bench_supplemental_type: str = "None"
    cultivation_bench_supplemental_rows: int = 0
    cultivation_bench_watts_row: float = 0.0
    cultivation_bench_watts_override: float = 0.0
    cultivation_bench_effective_date: str = ""
    cultivation_bench_notes: str = ""
    cultivation_yield_edit_id: str = ""
    cultivation_yield_crop: str = ""
    cultivation_yield_room: str = "Flower Room 1"
    cultivation_yield_strain: str = ""
    cultivation_yield_harvest_date: str = ""
    cultivation_yield_physical_canopy: float = 0.0
    cultivation_yield_planted_canopy: float = 0.0
    cultivation_yield_planted_plants: int = 0
    cultivation_yield_planned_ff_plants: int = 0
    cultivation_yield_actual_ff_plants: int = 0
    cultivation_yield_actual_ff_canopy: float = 0.0
    cultivation_yield_wet_lbs: float = 0.0
    cultivation_yield_dry_lbs: float = 0.0
    cultivation_yield_ab_lbs: float = 0.0
    cultivation_yield_c_lbs: float = 0.0
    cultivation_yield_trim_lbs: float = 0.0
    cultivation_yield_quality: float = 0.0
    cultivation_yield_notes: str = ""

    units_metric: str = "0"
    value_metric: str = "$0"
    customers_metric: str = "0"
    manifests_metric: str = "0"
    weighted_price_metric: str = "$0.00"
    stockouts_metric: str = "0"
    open_manifests_metric: str = "0"
    exception_manifests_metric: str = "0"
    exception_rows_metric: str = "0"
    transfer_rows_metric: str = "0"
    latest_shipment: str = "—"
    snapshot_date: str = "—"
    snapshot_packages: str = "0"
    snapshot_skus: str = "0"
    snapshot_detail: str = "0"
    snapshot_cpg_eligible: str = "0"
    inventory_ready: bool = False
    authoritative_cpg_ready: bool = False

    brands: list[str] = []
    strains: list[str] = []
    sku_types: list[str] = []
    monthly: list[dict[str, Any]] = []
    top_skus: list[dict[str, Any]] = []
    business_pulse: list[dict[str, Any]] = []
    velocity: list[dict[str, Any]] = []
    availability_demand_summary: list[dict[str, Any]] = []
    availability_demand_weekly: list[dict[str, Any]] = []
    stockouts: list[dict[str, Any]] = []
    saved_plans: list[dict[str, Any]] = []
    saved_plan_cards: list[SavedPlanCard] = []
    production_templates: list[dict[str, Any]] = []
    calendar: list[CalendarEvent] = []
    customers: list[dict[str, Any]] = []
    retail_delivery_history: list[dict[str, Any]] = []
    retailer_locations: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    exception_packages: list[dict[str, Any]] = []
    shipment_exception_view: str = "Open Transfers"
    shipment_exception_view_options: list[str] = [
        "Open Transfers", "Rejected Transfers", "Returned Transfers"
    ]
    shipment_exception_show_manifest_summary: bool = False
    package_lineage_draft: str = ""
    package_lineage_query: str = ""
    package_lineage_loading: bool = False
    package_lineage_message: str = (
        "Search a Metrc package tag or manifest to reconstruct its stored history."
    )
    package_lineage_error: str = ""
    package_lineage_packages: str = "0"
    package_lineage_sources: str = "0"
    package_lineage_snapshots: str = "0"
    package_lineage_transfers: str = "0"
    package_lineage_records: list[dict[str, Any]] = []
    package_lineage_timeline: list[dict[str, Any]] = []
    _transfer_data: list[dict[str, Any]] = []
    transfer_import_log: list[dict[str, Any]] = []
    cpg_inventory: list[dict[str, Any]] = []
    bulk_inventory: list[dict[str, Any]] = []
    wip_inventory: list[dict[str, Any]] = []
    potential_wip_inventory: list[dict[str, Any]] = []
    aging_cpg: list[dict[str, Any]] = []
    aging_bulk: list[dict[str, Any]] = []
    all_inventory: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    calendar_year: int = date.today().year
    calendar_month: int = date.today().month
    calendar_title: str = ""
    calendar_view_mode: str = "Month"
    calendar_focus_date: str = date.today().isoformat()
    calendar_days: list[CalendarDay] = []

    # Reflex 0.9 does not synthesize public setters.  Registry forms use these
    # compact generated event handlers instead of dozens of repetitive methods.
    for _registry_text_field in (
        "cultivation_schedule_program", "cultivation_schedule_start_crop",
        "cultivation_schedule_first_cut", "cultivation_program_name",
        "cultivation_current_crop_draft",
        "cultivation_program_code_prefix", "cultivation_program_room_rotation",
        "cultivation_room_edit_id", "cultivation_room_code", "cultivation_room_name",
        "cultivation_room_building", "cultivation_room_program",
        "cultivation_room_overhead_type", "cultivation_room_overhead_other",
        "cultivation_room_effective_date", "cultivation_room_notes",
        "cultivation_bench_edit_id", "cultivation_bench_room_id",
        "cultivation_bench_name", "cultivation_bench_supplemental_type",
        "cultivation_bench_effective_date", "cultivation_bench_notes",
        "cultivation_yield_edit_id", "cultivation_yield_crop",
        "cultivation_yield_room", "cultivation_yield_strain",
        "cultivation_yield_harvest_date", "cultivation_yield_notes",
    ):
        locals()[f"set_{_registry_text_field}"] = rx.event(
            _cultivation_registry_setter(_registry_text_field)
        )
    for _registry_int_field in (
        "cultivation_schedule_count", "cultivation_program_cadence_days",
        "cultivation_program_rooting_days", "cultivation_program_veg_days",
        "cultivation_program_flowering_days", "cultivation_program_processing_days",
        "cultivation_program_target_crops", "cultivation_room_fixture_count",
        "cultivation_bench_supplemental_rows", "cultivation_yield_planted_plants",
        "cultivation_yield_planned_ff_plants", "cultivation_yield_actual_ff_plants",
    ):
        locals()[f"set_{_registry_int_field}"] = rx.event(
            _cultivation_registry_setter(_registry_int_field, "int")
        )
    for _registry_float_field in (
        "cultivation_room_length", "cultivation_room_width", "cultivation_room_height",
        "cultivation_room_watts_fixture", "cultivation_room_watts_override",
        "cultivation_bench_length", "cultivation_bench_width", "cultivation_bench_density",
        "cultivation_bench_watts_row", "cultivation_bench_watts_override",
        "cultivation_yield_physical_canopy", "cultivation_yield_planted_canopy",
        "cultivation_yield_actual_ff_canopy", "cultivation_yield_wet_lbs",
        "cultivation_yield_dry_lbs", "cultivation_yield_ab_lbs",
        "cultivation_yield_c_lbs", "cultivation_yield_trim_lbs",
        "cultivation_yield_quality",
    ):
        locals()[f"set_{_registry_float_field}"] = rx.event(
            _cultivation_registry_setter(_registry_float_field, "float")
        )
    del _registry_text_field, _registry_int_field, _registry_float_field

    def _apply_employee(self, employee: dict[str, Any]) -> None:
        self.authenticated = True
        self.auth_email = str(employee.get("user_email", ""))
        self.auth_employee_id = str(employee.get("employee_id", ""))
        self.auth_name = str(employee.get("full_name", ""))
        self.auth_title = str(employee.get("title", ""))
        self.auth_role = str(employee.get("user_role", ""))
        self.auth_provider = str(employee.get("auth_provider", ""))

    def _clear_employee(self) -> None:
        self.authenticated = False
        self.auth_email = ""
        self.auth_employee_id = ""
        self.auth_name = ""
        self.auth_title = ""
        self.auth_role = ""
        self.auth_provider = ""

    def _refresh_team_directory(self) -> None:
        if self.auth_role != "Admin":
            self.team_members = []
            return
        self.team_members = list_employee_directory()
        employee_ids = [str(row.get("Employee ID", "")) for row in self.team_members]
        if self.admin_selected_employee_id not in employee_ids:
            self.admin_selected_employee_id = employee_ids[0] if employee_ids else ""
        self._load_selected_employee_fields()

    def _load_selected_employee_fields(self) -> None:
        selected = next(
            (
                row for row in self.team_members
                if str(row.get("Employee ID", "")) == self.admin_selected_employee_id
            ),
            None,
        )
        if selected:
            self.admin_selected_role = str(selected.get("Role", "Sales"))
            self.admin_selected_active = bool(selected.get("Active", False))
            self.admin_selected_name = str(selected.get("Name", ""))
            self.admin_selected_title = str(selected.get("Title", ""))
            self.admin_selected_primary_email = str(
                selected.get("Primary Email", "")
            )
            self.admin_selected_login_emails = str(
                selected.get("Login Emails", "")
            )
        else:
            self.admin_selected_name = ""
            self.admin_selected_title = ""
            self.admin_selected_primary_email = ""
            self.admin_selected_login_emails = ""

    @rx.var(cache=True)
    def is_administrator(self) -> bool:
        return self.authenticated and self.auth_role == "Admin"

    @rx.var(cache=True)
    def admin_employee_options(self) -> list[str]:
        return [str(row.get("Employee ID", "")) for row in self.team_members]

    @rx.event
    def load_team_access(self):
        self.admin_message = ""
        self.admin_error = ""
        if not self._require_active_session() or self.auth_role != "Admin":
            self.admin_error = "Administrator access is required."
            return
        try:
            self._refresh_team_directory()
        except Exception as error:
            self.admin_error = f"Team & Access could not be loaded: {error}"

    @rx.event
    def change_admin_selected_employee(self, employee_id: str):
        self.admin_selected_employee_id = employee_id
        self.admin_additional_email = ""
        self.admin_message = ""
        self.admin_error = ""
        self._load_selected_employee_fields()

    @rx.event
    def change_admin_new_name(self, value: str):
        self.admin_new_name = value

    @rx.event
    def change_admin_new_title(self, value: str):
        self.admin_new_title = value

    @rx.event
    def change_admin_new_primary_email(self, value: str):
        self.admin_new_primary_email = value

    @rx.event
    def change_admin_new_alternate_email(self, value: str):
        self.admin_new_alternate_email = value

    @rx.event
    def change_admin_new_role(self, value: str):
        self.admin_new_role = value

    @rx.event
    def change_admin_selected_role(self, value: str):
        self.admin_selected_role = value

    @rx.event
    def change_admin_selected_active(self, value: bool):
        self.admin_selected_active = value

    @rx.event
    def change_admin_additional_email(self, value: str):
        self.admin_additional_email = value

    @rx.event
    def create_team_member(self):
        self.admin_message = ""
        self.admin_error = ""
        if not self._require_active_session() or self.auth_role != "Admin":
            self.admin_error = "Administrator access is required."
            return
        try:
            employee_id = create_employee_profile(
                self.auth_employee_id,
                self.admin_new_name,
                self.admin_new_title,
                self.admin_new_primary_email,
                self.admin_new_role,
                self.admin_new_alternate_email,
            )
            self.admin_new_name = ""
            self.admin_new_title = ""
            self.admin_new_primary_email = ""
            self.admin_new_alternate_email = ""
            self.admin_new_role = "Sales"
            self.admin_selected_employee_id = employee_id
            self._refresh_team_directory()
            self.admin_message = "Employee account created. Both existing employees and this new account were preserved."
        except Exception as error:
            self.admin_error = str(error)

    @rx.event
    def save_team_member_access(self):
        self.admin_message = ""
        self.admin_error = ""
        if not self._require_active_session() or self.auth_role != "Admin":
            self.admin_error = "Administrator access is required."
            return
        if not self.admin_selected_employee_id:
            self.admin_error = "Select an employee first."
            return
        try:
            update_employee_access(
                self.auth_employee_id,
                self.admin_selected_employee_id,
                self.admin_selected_role,
                self.admin_selected_active,
                self.admin_additional_email,
            )
            self.admin_additional_email = ""
            self._refresh_team_directory()
            self.admin_message = "Employee access saved without changing any other employee record or historical data."
        except Exception as error:
            self.admin_error = str(error)

    def _require_active_session(self) -> bool:
        employee = validate_app_session(str(self.auth_session_token or ""))
        if employee is None:
            self._clear_employee()
            self.auth_message = "Your session expired. Sign in again to continue."
            return False
        self._apply_employee(employee)
        return True

    @rx.event
    def begin_google_sign_in(self):
        self.auth_redirecting = True
        self.auth_checked = False
        self.auth_failed = False
        self.auth_message = "Connecting to Google Workspace..."
        yield
        try:
            destination = json.dumps(oauth_authorize_url("google"))
            yield rx.call_script(
                f"window.setTimeout(() => window.location.assign({destination}), 450)"
            )
        except Exception as error:
            self.auth_checked = True
            self.auth_redirecting = False
            self.auth_failed = True
            self.auth_message = f"Google sign-in is not ready: {error}"

    @rx.event
    def begin_microsoft_sign_in(self):
        self.auth_redirecting = True
        self.auth_checked = False
        self.auth_failed = False
        self.auth_message = "Connecting to Microsoft 365..."
        yield
        try:
            destination = json.dumps(oauth_authorize_url("azure"))
            yield rx.call_script(
                f"window.setTimeout(() => window.location.assign({destination}), 450)"
            )
        except Exception as error:
            self.auth_checked = True
            self.auth_redirecting = False
            self.auth_failed = True
            self.auth_message = f"Microsoft sign-in is not ready: {error}"

    @rx.event
    def complete_oauth_callback(self, fragment: str):
        """Validate Supabase's implicit-flow token and create a server session."""
        self.auth_checked = True
        self.auth_redirecting = True
        self.auth_failed = False
        self.auth_message = "Verifying identity and QCC employee access..."
        try:
            values = parse_qs(str(fragment or "").lstrip("#"))
            if values.get("error_description"):
                raise ValueError(values["error_description"][0])
            access_token = values.get("access_token", [""])[0]
            user = verify_supabase_access_token(access_token)
            email = str(user.get("email", "")).strip().lower()
            employee = load_active_employee(email)
            if employee is None:
                raise PermissionError(
                    "This email does not match an active QCC employee account. "
                    "Ask an Administrator to add or reactivate it."
                )
            identities = user.get("identities") or []
            provider = "supabase"
            if identities and isinstance(identities[0], dict):
                provider = str(identities[0].get("provider", provider))
            self.auth_session_token = create_app_session(email, provider)
            employee["auth_provider"] = provider
            self._apply_employee(employee)
            self.auth_message = "Sign-in verified. Loading QCC Control Tower..."
            # Persist the secure session cookie in one state update before the
            # route changes. Redirecting in the same update caused a brief
            # dashboard flash followed by another authentication check.
            yield
            # Let the callback state update finish before the browser loads the
            # dashboard. This gives the secure cookie time to persist and avoids
            # briefly showing the app before sending the user through OAuth again.
            yield rx.call_script(
                "window.setTimeout(() => window.location.replace('/'), 650)"
            )
        except Exception as error:
            self.auth_redirecting = False
            self.auth_failed = True
            self.auth_session_token = ""
            self._clear_employee()
            self.auth_message = f"Sign-in could not be completed: {error}"

    @rx.event
    def sign_out(self):
        revoke_app_session(str(self.auth_session_token or ""))
        self.auth_session_token = ""
        self._clear_employee()
        self.auth_checked = True
        self.auth_message = "You have signed out safely."
        return rx.redirect("/")

    @rx.event
    def change_brand_filter(self, value: str):
        """Update the brand filter from the Reflex select component."""
        self.brand_filter = value
        self.sku_planning_page = 1
        self.inventory_page = 1
        self.transfer_page = 1
        self.exception_page = 1
        self._sync_qa_consistency_filters()
        if self.workspace_view == "distribution" and self.distribution_view in {"transfers", "exceptions"}:
            yield DashboardState.load_distribution_operations_background

    @rx.event
    def change_strain_filter(self, value: str):
        self.strain_filter = value
        self.sku_planning_page = 1
        self.inventory_page = 1
        self.transfer_page = 1
        self.exception_page = 1
        self._sync_qa_consistency_filters()
        if self.workspace_view == "distribution" and self.distribution_view in {"transfers", "exceptions"}:
            yield DashboardState.load_distribution_operations_background

    @rx.event
    def change_sku_filter(self, value: str):
        self.sku_filter = value
        self.sku_planning_page = 1
        self.inventory_page = 1
        self.transfer_page = 1
        self.exception_page = 1
        self._sync_qa_consistency_filters()
        if self.workspace_view == "distribution" and self.distribution_view in {"transfers", "exceptions"}:
            yield DashboardState.load_distribution_operations_background

    @rx.event
    def change_search_text(self, value: str):
        """Update the shared search text from the Reflex input component."""
        self.search_text = value
        self.sku_planning_page = 1
        self.inventory_page = 1
        self.transfer_page = 1
        self.exception_page = 1
        if self.workspace_view == "distribution" and self.distribution_view in {"transfers", "exceptions"}:
            yield DashboardState.load_distribution_operations_background

    @staticmethod
    def _validated_table_row_limit(value: str) -> str:
        return value if value in {"10", "25", "50"} else "10"

    @rx.event
    def change_executive_action_rows_per_page(self, value: str):
        self.executive_action_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_top_sku_rows_per_page(self, value: str):
        self.top_sku_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_stockout_rows_per_page(self, value: str):
        self.stockout_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_customer_rows_per_page(self, value: str):
        self.customer_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_transfer_import_rows_per_page(self, value: str):
        self.transfer_import_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_transfer_rows_per_page(self, value: str):
        self.transfer_rows_per_page = self._validated_table_row_limit(value)
        self.transfer_page_size = int(self.transfer_rows_per_page)
        self.transfer_page = 1
        yield DashboardState.load_distribution_operations_background

    @rx.event
    def change_exception_rows_per_page(self, value: str):
        self.exception_rows_per_page = self._validated_table_row_limit(value)
        self.exception_page_size = int(self.exception_rows_per_page)
        self.exception_page = 1
        yield DashboardState.load_distribution_operations_background

    @rx.event
    def change_retail_timeframe(self, value: str):
        self.retail_timeframe = value

    @rx.event
    def change_retail_show_pending(self, value: bool):
        self.retail_show_pending = value

    @rx.event
    def change_retail_brand_filter(self, value: str):
        self.retail_brand_filter = value
        valid_strains = self._retail_strains_for_brand(value)
        if (
            self.retail_strain_filter != "All Strains"
            and self.retail_strain_filter not in valid_strains
        ):
            self.retail_strain_filter = "All Strains"

    @rx.event
    def change_retail_strain_filter(self, value: str):
        self.retail_strain_filter = value

    @rx.event
    def change_retail_sku_filter(self, value: str):
        self.retail_sku_filter = value

    @rx.event
    def change_retail_customer_filter(self, value: str):
        self.retail_customer_filter = value

    @rx.event
    def change_retail_start_address(self, value: str):
        self.retail_start_address_input = value

    @rx.event
    def apply_retail_start_address(self):
        self.retail_start_address = self.retail_start_address_input.strip()

    @rx.event
    def clear_retail_start_address(self):
        self.retail_start_address_input = ""
        self.retail_start_address = ""

    def _apply_qa_payload(self, payload: dict[str, Any]) -> None:
        self._qa_packages = payload.get("packages", [])
        self.qa_templates = payload.get("templates", [])
        import_columns = [
            "File", "Source Rows", "Stored Rows", "Inserted", "Updated",
            "Test Min", "Test Max", "Imported At",
        ]
        summary_columns = [
            "Imported At", "File", "Sample Tag", "Parent Package", "Product",
            "Result Status", "Active Source", "Total THC %", "Total Terpenes %",
        ]
        self.qa_import_log = [
            [row.get(column, "") for column in import_columns]
            for row in payload.get("import_log", [])
        ]
        self.qa_lab_direct_summary = [
            [row.get(column, "") for column in summary_columns]
            for row in payload.get("lab_direct_summary", [])
        ]
        self.qa_record_count = int(payload.get("record_count", 0) or 0)
        self.qa_analyte_count = int(payload.get("analyte_count", 0) or 0)
        self.brands = sorted(set(self.brands).union(
            str(row.get("brand", "")) for row in self._qa_packages
            if str(row.get("brand", ""))
        ))
        self.strains = sorted(set(self.strains).union(
            str(row.get("strain", "")) for row in self._qa_packages
            if str(row.get("strain", ""))
        ))
        self._sync_qa_consistency_filters()
        self.qa_loaded = True

    def _sync_qa_consistency_filters(self) -> None:
        cultivation_options = self.qa_cultivation_consistency_options
        manufacturing_options = self.qa_manufacturing_consistency_options
        if self.qa_cultivation_consistency_strain not in cultivation_options:
            self.qa_cultivation_consistency_strain = (
                cultivation_options[0] if cultivation_options else ""
            )
        if self.qa_manufacturing_consistency_strain not in manufacturing_options:
            self.qa_manufacturing_consistency_strain = (
                manufacturing_options[0] if manufacturing_options else ""
            )

    def _load_qa_payload(self, force_refresh: bool = False) -> None:
        self._apply_qa_payload(
            load_qa_module_data(force_refresh=force_refresh)
        )

    @rx.event(background=True)
    async def load_qa_background(self, force_refresh: bool = False):
        """Load QA without blocking navigation for this signed-in user."""
        async with self:
            if self.qa_loading:
                return
            self.qa_loading = True
            self.qa_error = ""
            self.qa_message = "Loading Quality & Compliance data..."
        try:
            payload = await rx.run_in_thread(
                lambda: load_qa_module_data(force_refresh=force_refresh)
            )
            async with self:
                self._apply_qa_payload(payload)
                self.qa_message = "Quality & Compliance data is ready."
        except Exception as error:
            async with self:
                self.qa_error = f"Quality & Compliance could not be loaded: {error}"
                self.qa_message = ""
        finally:
            async with self:
                self.qa_loading = False

    @rx.event
    def change_qa_view(self, value: str):
        self.qa_view = value
        if value not in {"customers", "retail", "transfers", "exceptions"}:
            return
        self.sales_demand_view = value
        self.transfer_page = 1
        self.exception_page = 1
        # Release the navigation update before hydrating a potentially large
        # transfer-backed distribution view.
        yield
        if value in {"transfers", "exceptions"}:
            yield DashboardState.load_distribution_operations_background
            return
        if value not in self.sales_loaded_views:
            yield DashboardState.load_sales_background

    @rx.event
    def change_quality_view(self, value: str):
        """Change the Quality & Compliance subtab without affecting Distribution."""
        if value not in {"cultivation", "manufacturing", "labels"}:
            return
        self.quality_view = value
        self.qa_view = value

    @rx.event
    def change_distribution_view(self, value: str):
        """Change the Distribution & Customer Service subtab."""
        if value not in {"customers", "retail", "exceptions", "lineage", "transfers"}:
            return
        self.distribution_view = value
        self.qa_view = value
        if value not in {"customers", "retail", "transfers", "exceptions"}:
            return
        self.sales_demand_view = value
        self.transfer_page = 1
        self.exception_page = 1
        yield
        if value in {"transfers", "exceptions"}:
            yield DashboardState.load_distribution_operations_background
            return
        if value not in self.sales_loaded_views:
            yield DashboardState.load_sales_background

    @rx.event
    def change_qa_cultivation_test_type(self, value: str):
        self.qa_cultivation_test_type = value
        options = self.qa_cultivation_consistency_options
        self.qa_cultivation_consistency_strain = options[0] if options else ""

    @rx.event
    def change_qa_manufacturing_test_type(self, value: str):
        self.qa_manufacturing_test_type = value
        options = self.qa_manufacturing_consistency_options
        self.qa_manufacturing_consistency_strain = options[0] if options else ""

    @rx.event
    def change_qa_cultivation_consistency_strain(self, value: str):
        self.qa_cultivation_consistency_strain = value

    @rx.event
    def change_qa_manufacturing_consistency_strain(self, value: str):
        self.qa_manufacturing_consistency_strain = value

    @rx.event
    def change_qa_cultivation_pass_rows_per_page(self, value: str):
        self.qa_cultivation_pass_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_qa_cultivation_potency_rows_per_page(self, value: str):
        self.qa_cultivation_potency_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_qa_cultivation_detail_rows_per_page(self, value: str):
        self.qa_cultivation_detail_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_qa_manufacturing_pass_rows_per_page(self, value: str):
        self.qa_manufacturing_pass_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_qa_manufacturing_potency_rows_per_page(self, value: str):
        self.qa_manufacturing_potency_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_qa_manufacturing_detail_rows_per_page(self, value: str):
        self.qa_manufacturing_detail_rows_per_page = self._validated_table_row_limit(value)

    @rx.event
    def change_qa_lookup_search(self, value: str):
        # Typing must not evaluate or render matching database rows. The draft
        # is submitted only when Find and Preview is pressed.
        self.qa_lookup_draft = value
        self.qa_message = ""
        self.qa_error = ""

    @rx.event
    def change_qa_preview_open(self, value: bool):
        self.qa_preview_open = value

    @rx.event
    def change_qa_adjusted_coa_open(self, value: bool):
        self.qa_adjusted_coa_open = value

    @rx.event
    def open_qa_adjusted_coa(self):
        if self.qa_selected_analytes_loading:
            self.qa_zebra_error = "Wait for the laboratory analytes to finish loading."
            return
        if len(self.qa_adjusted_terpene_names) < 3 or not all(
            self.qa_adjusted_terpene_names
        ):
            self.qa_zebra_error = (
                "Three individual terpene results are required before an Adjusted COA can be entered."
            )
            return
        self.qa_adjusted_coa_error = ""
        self.qa_adjusted_coa_message = ""
        self.qa_adjusted_coa_open = True

    @rx.event
    def change_qa_label_operation_filter(self, value: str):
        self.qa_label_operation_filter = value

    @rx.event
    def change_qa_label_brand_filter(self, value: str):
        self.qa_label_brand_filter = value

    @rx.event
    def change_qa_label_strain_filter(self, value: str):
        self.qa_label_strain_filter = value

    @rx.event
    def change_qa_label_sku_filter(self, value: str):
        self.qa_label_sku_filter = value

    @rx.event
    def change_qa_label_catalog_search(self, value: str):
        self.qa_label_catalog_search = value

    @rx.event
    def change_qa_analyte_category_filter(self, value: str):
        self.qa_analyte_category_filter = value

    @staticmethod
    def _qa_lookup_label(row: dict[str, Any]) -> str:
        return " | ".join([
            str(row.get("package_tag", "")),
            str(row.get("strain", "")),
            str(row.get("qa_test_type", "")),
            str(row.get("lab_testing_status", "")),
        ])

    @rx.event
    def select_qa_lookup_record(self, value: str):
        self.qa_lookup_selection = value
        selected = next(
            (row for row in self.qa_lookup_matches if self._qa_lookup_label(row) == value),
            None,
        )
        self._select_qa_record(selected)
        self.qa_preview_open = selected is not None
        if selected is not None:
            yield DashboardState.load_selected_qa_analytes(
                str(selected.get("package_tag", "")),
                str(selected.get("packaged_license", "")),
            )

    @staticmethod
    def _inventory_qa_record(row: dict[str, Any]) -> dict[str, Any]:
        sku_type = str(row.get("SKU Type", "") or "")
        sku_key = sku_type.lower()
        if re.search(r"\bpre[- ]?roll\b|\bpreroll\b", sku_key):
            test_type = "Pre-Rolls"
        elif re.search(r"\bvape\b|\bcartridge\b|\bdisposable\b", sku_key):
            test_type = "Vapes"
        elif re.search(r"\bedible\b|\bgumm(?:y|ies)\b", sku_key):
            test_type = "Edibles"
        elif re.search(r"\bconcentrate\b|\brosin\b|\bbadder\b|\bdiamonds?\b", sku_key):
            test_type = "Concentrates"
        elif re.search(r"\bflower\b|\bsmalls?\b", sku_key):
            test_type = "Flower"
        else:
            test_type = "Other / Needs Review"
        operation = (
            "Manufacturing"
            if test_type in {"Vapes", "Edibles", "Concentrates"}
            or "infused" in sku_key
            else "Cultivation"
        )
        qa_status = str(row.get("QA Status", "") or "")
        return {
            "package_tag": str(row.get("Metrc Tag", "") or ""),
            "packaged_license": "",
            "packaged_facility": str(
                row.get("Current Facility", row.get("Facility", "")) or ""
            ),
            "brand": str(row.get("Brand", "") or ""),
            "strain": str(row.get("Strain", "") or ""),
            "sku_type": sku_type,
            "qa_test_type": test_type,
            "operation": operation,
            "lab_testing_status": qa_status or "Status unavailable",
            "qa_outcome": (
                "Passed" if "pass" in qa_status.lower()
                else "Failed" if "fail" in qa_status.lower() else "Pending"
            ),
            "source_harvest_names": str(row.get("Source Harvest", "") or ""),
            "source_package_labels": "",
            "item": str(row.get("Item", "") or ""),
            "category": str(row.get("Category", "") or ""),
            "location": str(row.get("Location", "") or ""),
            "expiration_date": "",
            "test_date": "",
            "total_thc": None,
            "total_terpenes": None,
            "lab_facility": "",
            "record_origin": "Current Inventory — no associated COA found",
        }

    @rx.event
    def find_qa_lookup_record(self):
        """Select the compact compliance record before loading analyte detail."""
        self.qa_lookup_loading = True
        self.qa_error = ""
        self.qa_analyte_message = ""
        yield
        selected: dict[str, Any] | None = None
        try:
            search = self.qa_lookup_draft.strip()
            if not search:
                self.qa_error = "Enter a Metrc package tag or harvest name first."
                return
            matches = self._qa_lookup_rows(search.lower())
            normalized = search.lower()
            selected = next(
                (
                    row for row in matches
                    if str(row.get("package_tag", "")).strip().lower() == normalized
                ),
                matches[0] if matches else None,
            )
            if selected is None:
                self.qa_error = (
                    "No compliance or current Inventory record matched that tag or harvest."
                )
                self.qa_message = ""
                return
            self.qa_lookup_selection = self._qa_lookup_label(selected)
            self._select_qa_record(selected)
            self.qa_preview_open = True
            if str(selected.get("record_origin", "")).startswith("Current Inventory"):
                self.qa_message = (
                    "Current Inventory record found. No associated COA was found, so a "
                    "general printable compliance summary was prepared."
                )
                self.qa_analyte_message = "No associated laboratory analytes were found."
            else:
                self.qa_message = (
                    "Lab Direct passed result found. Verify the printed label against "
                    "the final COA; Metrc will replace this source automatically."
                    if str(selected.get("record_origin", "")).startswith("Lab Direct")
                    else "Compliance record found and printable summary prepared."
                )
                self.qa_analyte_message = "Loading the complete analyte history..."
        except Exception as error:
            self.qa_error = f"That compliance record could not be prepared: {error}"
            self.qa_message = ""
            selected = None
        finally:
            self.qa_lookup_loading = False
        yield
        if selected is not None and not str(
            selected.get("record_origin", "")
        ).startswith("Current Inventory"):
            yield DashboardState.load_selected_qa_analytes(
                str(selected.get("package_tag", "")),
                str(selected.get("packaged_license", "")),
            )

    def _select_qa_record(self, selected: dict[str, Any] | None) -> None:
        self.qa_analyte_category_filter = "All Categories"
        if selected is None:
            self.qa_selected_package = {}
            self.qa_selected_analytes = []
            self.qa_zebra_message = ""
            self.qa_zebra_error = ""
            self.qa_adjusted_coa = {}
            self.qa_adjusted_coa_open = False
            return
        self.qa_selected_package = dict(selected)
        self.qa_selected_package.setdefault("record_origin", "Lab Results / COA")
        if str(self.qa_selected_package.get("record_origin", "")).startswith(
            "Current Inventory"
        ):
            self.qa_selected_analytes = []
        else:
            # Full analyte history is intentionally loaded by a protected
            # background event after the preview is already visible.
            self.qa_selected_analytes = []
        expiration = str(selected.get("expiration_date", "") or "")
        self.qa_override_expiration = not bool(expiration)
        self.qa_manual_expiration = expiration
        template_options = self.qa_template_options
        if self.qa_selected_template not in template_options:
            self.qa_selected_template = template_options[0] if template_options else ""
        brand = str(selected.get("brand", ""))
        strain = str(selected.get("strain", ""))
        sku = str(selected.get("sku_type", selected.get("qa_test_type", "")))
        candidates = [
            row for row in self.qa_label_catalog
            if str(row.get("Brand", "")) == brand
            and str(row.get("Strain", "")) == strain
        ]
        exact = next(
            (row for row in candidates if str(row.get("SKU Type", "")) == sku),
            candidates[0] if len(candidates) == 1 else None,
        )
        self.qa_selected_native_template = (
            str(exact.get("Template Name", ""))
            if exact else "General Compliance Summary"
        )
        recent = {
            "Package Tag": str(selected.get("package_tag", "")),
            "Brand": brand,
            "Strain": strain,
            "SKU Type": sku,
        }
        self.qa_recent_selections = [
            recent,
            *[
                row for row in self.qa_recent_selections
                if row.get("Package Tag") != recent["Package Tag"]
            ],
        ][:8]
        self._initialize_zebra_label_inputs(selected)

    def _initialize_zebra_label_inputs(self, selected: dict[str, Any]) -> None:
        """Prepare editable production-label values from the selected COA."""
        source_tags = extract_metrc_tags(selected.get("source_package_labels", ""))
        self.qa_zebra_bulk_uid = source_tags[0] if len(source_tags) == 1 else ""
        harvest = extract_harvest_date(selected.get("source_harvest_names", ""))
        self.qa_zebra_harvest_date = harvest.isoformat() if harvest else ""
        brand = str(selected.get("brand", "") or "").strip().lower()
        partner_flower = (
            brand in {"craft kings", "royal smalls"}
            and "flower" in self.qa_zebra_package_format.lower()
        )
        if partner_flower:
            lot_number = (
                selected.get("source_production_batch", "")
                or selected.get("production_batch_number", "")
                or selected.get("source_harvest_names", "")
            )
        else:
            lot_number = (
                selected.get("production_batch_number", "")
                or selected.get("source_harvest_names", "")
            )
        self.qa_zebra_lot_number = str(lot_number or "")
        # A COA describes the tested material, not necessarily the finished
        # package size that will be printed from it. Preserve the operator's
        # explicit Package Format selection when switching laboratory records.
        # Only infer a default if state contains no supported selection.
        if self.qa_zebra_package_format not in PACKAGE_FORMAT_OPTIONS:
            self.qa_zebra_package_format = default_package_format(
                selected.get("sku_type", selected.get("qa_test_type", ""))
            )
        self.qa_zebra_quantity = 1
        self.qa_zebra_message = ""
        self.qa_zebra_error = ""
        self.qa_adjusted_coa = {}
        self.qa_adjusted_coa_open = False
        self.qa_adjusted_coa_message = ""
        self.qa_adjusted_coa_error = ""
        self.qa_adjusted_total_terpenes = ""
        self.qa_adjusted_total_cbg = ""
        self.qa_adjusted_terpene_names = ["", "", ""]
        self.qa_adjusted_terpene_values = ["", "", ""]
        self.qa_adjusted_metrc_total_terpenes = ""
        self.qa_adjusted_metrc_total_cbg = ""
        self.qa_adjusted_metrc_terpene_values = ["", "", ""]
        self.qa_adjusted_source_label = "Metrc"

    @rx.event
    def select_qa_package(self, package_tag: str, packaged_license: str):
        selected = next(
            (
                row for row in self.qa_lookup_matches
                if str(row.get("package_tag", "")) == str(package_tag)
                and str(row.get("packaged_license", "")) == str(packaged_license)
            ),
            None,
        )
        self.qa_lookup_selection = self._qa_lookup_label(selected) if selected else ""
        self._select_qa_record(selected)
        self.qa_preview_open = selected is not None
        if selected is not None:
            yield DashboardState.load_selected_qa_analytes(
                str(selected.get("package_tag", "")),
                str(selected.get("packaged_license", "")),
            )

    @rx.event(background=True)
    async def load_selected_qa_analytes(
        self, package_tag: str, packaged_license: str
    ):
        """Load optional analyte detail without risking the QA search event."""
        async with self:
            current_tag = str(self.qa_selected_package.get("package_tag", ""))
            if not package_tag or current_tag != package_tag:
                return
            self.qa_selected_analytes_loading = True
            self.qa_analyte_message = "Loading the complete analyte history..."
        try:
            rows = await rx.run_in_thread(
                lambda: load_qa_analytes(package_tag, packaged_license)
            )
            try:
                adjusted_coa = await rx.run_in_thread(
                    lambda: load_adjusted_coa(package_tag, packaged_license)
                )
            except Exception:
                # An adjustment is optional; a temporary persistence outage must
                # not hide the underlying Metrc analytes or block label review.
                adjusted_coa = {}
            async with self:
                if str(self.qa_selected_package.get("package_tag", "")) == package_tag:
                    self.qa_selected_analytes = rows
                    self._initialize_adjusted_coa_inputs(rows, adjusted_coa)
                    self.qa_analyte_message = (
                        f"{len(rows):,} analyte result(s) loaded."
                        if rows else "No detailed analyte rows were found for this record."
                    )
        except Exception as error:
            async with self:
                if str(self.qa_selected_package.get("package_tag", "")) == package_tag:
                    self.qa_selected_analytes = []
                    self.qa_analyte_message = (
                        "The printable compliance summary is ready, but detailed "
                        f"analytes are temporarily unavailable: {error}"
                    )
        finally:
            async with self:
                if str(self.qa_selected_package.get("package_tag", "")) == package_tag:
                    self.qa_selected_analytes_loading = False

    @staticmethod
    def _adjusted_number(value: Any) -> float | None:
        text = str(value or "").strip().replace("%", "")
        if not text:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _initialize_adjusted_coa_inputs(
        self, rows: list[dict[str, Any]], adjusted_coa: dict[str, Any]
    ) -> None:
        metrc = label_analytes(rows)
        sources = {
            str(row.get("Source", "") or "").strip() for row in rows
            if str(row.get("Source", "") or "").strip()
        }
        is_lab_direct = "Lab Direct" in sources and "Metrc" not in sources
        self.qa_adjusted_source_label = "Lab Direct" if is_lab_direct else "Metrc"
        top = list(metrc.get("top_terpenes", []))[:3]
        while len(top) < 3:
            top.append(("", None))
        self.qa_adjusted_terpene_names = [str(name or "") for name, _ in top]
        self.qa_adjusted_metrc_terpene_values = [
            "" if value is None else f"{float(value):.3f}" for _, value in top
        ]
        metrc_total = self._adjusted_number(metrc.get("total_terpenes"))
        metrc_cbg = self._adjusted_number(metrc.get("total_cbg"))
        self.qa_adjusted_metrc_total_terpenes = (
            "" if metrc_total is None else f"{metrc_total:.3f}"
        )
        self.qa_adjusted_metrc_total_cbg = (
            "" if metrc_cbg is None else f"{metrc_cbg:.2f}"
        )
        self.qa_adjusted_coa = dict(adjusted_coa or {})
        if adjusted_coa:
            saved_names = list(adjusted_coa.get("terpene_names", []))
            saved_values = list(adjusted_coa.get("terpene_values", []))
            saved_by_name = {
                str(name): value for name, value in zip(saved_names, saved_values)
            }
            self.qa_adjusted_total_terpenes = f"{float(adjusted_coa['total_terpenes']):.3f}"
            self.qa_adjusted_total_cbg = f"{float(adjusted_coa['total_cbg']):.2f}"
            self.qa_adjusted_terpene_values = [
                f"{float(saved_by_name[name]):.3f}" if name in saved_by_name else ""
                for name in self.qa_adjusted_terpene_names
            ]
        else:
            self.qa_adjusted_total_terpenes = (
                self.qa_adjusted_metrc_total_terpenes if is_lab_direct else ""
            )
            self.qa_adjusted_total_cbg = (
                self.qa_adjusted_metrc_total_cbg if is_lab_direct else ""
            )
            self.qa_adjusted_terpene_values = (
                list(self.qa_adjusted_metrc_terpene_values)
                if is_lab_direct else ["", "", ""]
            )

    @rx.event
    def change_qa_adjusted_total_terpenes(self, value: str):
        self.qa_adjusted_total_terpenes = value
        self.qa_adjusted_coa_message = ""
        self.qa_adjusted_coa_error = ""

    @rx.event
    def change_qa_adjusted_total_cbg(self, value: str):
        self.qa_adjusted_total_cbg = value
        self.qa_adjusted_coa_message = ""
        self.qa_adjusted_coa_error = ""

    @rx.event
    def change_qa_adjusted_terpene_1(self, value: str):
        self.qa_adjusted_terpene_values[0] = value
        self.qa_adjusted_coa_message = ""
        self.qa_adjusted_coa_error = ""

    @rx.event
    def change_qa_adjusted_terpene_2(self, value: str):
        self.qa_adjusted_terpene_values[1] = value
        self.qa_adjusted_coa_message = ""
        self.qa_adjusted_coa_error = ""

    @rx.event
    def change_qa_adjusted_terpene_3(self, value: str):
        self.qa_adjusted_terpene_values[2] = value
        self.qa_adjusted_coa_message = ""
        self.qa_adjusted_coa_error = ""

    def _adjusted_coa_payload(
        self,
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        total = self._adjusted_number(self.qa_adjusted_total_terpenes)
        total_cbg = self._adjusted_number(self.qa_adjusted_total_cbg)
        terpene_values = [
            self._adjusted_number(value) for value in self.qa_adjusted_terpene_values
        ]
        errors: list[str] = []
        if total is None:
            errors.append("Enter Total Terpenes as a percentage.")
        if total_cbg is None:
            errors.append("Enter Total CBG exactly as shown on the COA.")
        if any(value is None for value in terpene_values):
            errors.append("Enter all three individual terpene percentages.")
        numbers = [float(value) for value in terpene_values if value is not None]
        if any(value < 0 for value in [total, total_cbg, *numbers] if value is not None):
            errors.append("Adjusted COA percentages cannot be negative.")
        if total is not None and total > 20:
            errors.append("Total Terpenes is outside the supported percentage range.")
        if total_cbg is not None and total_cbg > 20:
            errors.append("Total CBG is outside the supported percentage range.")
        if any(value > 20 for value in numbers):
            errors.append("An individual terpene is outside the supported percentage range.")
        if total is not None and len(numbers) == 3 and sum(numbers) > total + 0.0005:
            errors.append("The three terpene values cannot exceed Total Terpenes.")

        suspects: list[str] = []
        metrc_total = self._adjusted_number(self.qa_adjusted_metrc_total_terpenes)
        if total is not None and metrc_total is not None and abs(total - metrc_total) > 0.011:
            suspects.append(
                f"Total Terpenes differs from {self.qa_adjusted_source_label} by more than 0.01%."
            )
        metrc_cbg = self._adjusted_number(self.qa_adjusted_metrc_total_cbg)
        if total_cbg is not None and metrc_cbg is not None and abs(total_cbg - metrc_cbg) > 0.021:
            suspects.append(
                f"Total CBG differs from {self.qa_adjusted_source_label} by more than 0.02%."
            )
        for index, (name, value, metrc_text) in enumerate(zip(
            self.qa_adjusted_terpene_names,
            terpene_values,
            self.qa_adjusted_metrc_terpene_values,
        ), start=1):
            metrc_value = self._adjusted_number(metrc_text)
            if value is not None and metrc_value is not None and abs(value - metrc_value) > 0.011:
                suspects.append(
                    f"{name or f'Terpene {index}'} differs from "
                    f"{self.qa_adjusted_source_label} by more than 0.01%."
                )
        payload = {
            "total_terpenes": total,
            "total_cbg": total_cbg,
            "terpene_names": list(self.qa_adjusted_terpene_names),
            "terpene_values": numbers,
        }
        return payload, errors, suspects

    @rx.event
    async def save_qa_adjusted_coa(self):
        payload, errors, suspects = self._adjusted_coa_payload()
        if errors:
            self.qa_adjusted_coa_error = " ".join(errors)
            self.qa_adjusted_coa_message = ""
            return
        if not self._require_active_session():
            self.qa_adjusted_coa_error = "Your session expired. Sign in again to save this COA."
            return
        self.qa_adjusted_coa_saving = True
        self.qa_adjusted_coa_error = ""
        self.qa_adjusted_coa_message = ""
        try:
            package = self.qa_selected_package
            saved = await rx.run_in_thread(
                lambda: save_adjusted_coa(
                    str(package.get("package_tag", "")),
                    str(package.get("packaged_license", "")),
                    str(package.get("test_date", "")),
                    float(payload["total_terpenes"]),
                    float(payload["total_cbg"]),
                    list(payload["terpene_names"]),
                    list(payload["terpene_values"]),
                    suspects,
                    self.auth_email or self.auth_name,
                )
            )
            self.qa_adjusted_coa = saved
            self.qa_adjusted_coa_message = (
                "Adjusted COA saved. The Zebra preview and future labels now use these verified values."
            )
            self.qa_zebra_message = "Adjusted COA — verified values applied."
        except Exception as error:
            self.qa_adjusted_coa_error = f"The Adjusted COA could not be saved: {error}"
        finally:
            self.qa_adjusted_coa_saving = False

    @rx.event
    def select_native_template(self, value: str):
        self.qa_selected_native_template = value

    @rx.event
    def change_qa_selected_template(self, value: str):
        self.qa_selected_template = value

    @rx.event
    def change_qa_override_expiration(self, value: bool):
        self.qa_override_expiration = value
        if value and not self.qa_manual_expiration:
            self.qa_manual_expiration = str(
                self.qa_selected_package.get("expiration_date", "") or ""
            )

    @rx.event
    def change_qa_manual_expiration(self, value: str):
        self.qa_manual_expiration = value

    @rx.event
    def change_qa_zebra_package_format(self, value: str):
        self.qa_zebra_package_format = value
        self.qa_zebra_message = ""
        self.qa_zebra_error = ""

    @rx.event
    def change_qa_zebra_bulk_uid(self, value: str):
        self.qa_zebra_bulk_uid = value.strip().upper()
        self.qa_zebra_message = ""
        self.qa_zebra_error = ""

    @rx.event
    def change_qa_zebra_harvest_date(self, value: str):
        self.qa_zebra_harvest_date = value
        self.qa_zebra_message = ""
        self.qa_zebra_error = ""

    @rx.event
    def change_qa_zebra_lot_number(self, value: str):
        self.qa_zebra_lot_number = value
        self.qa_zebra_message = ""
        self.qa_zebra_error = ""

    @rx.event
    def change_qa_zebra_printer(self, value: str):
        self.qa_zebra_printer = value

    @rx.event
    def change_qa_zebra_quantity(self, value: str):
        try:
            self.qa_zebra_quantity = max(1, min(int(value), 9999))
        except (TypeError, ValueError):
            self.qa_zebra_quantity = 1

    @rx.event
    def refresh_qa(self):
        self.qa_error = ""
        self.qa_message = ""
        yield DashboardState.load_qa_background(True)

    @rx.event
    async def import_qa_lab_files(self, files: list[rx.UploadFile]):
        self.qa_error = ""
        self.qa_message = ""
        self.qa_import_results = []
        if not self._require_active_session():
            self.qa_error = "Your session expired. Sign in again to import lab data."
            return
        if not files:
            self.qa_error = "Choose at least one Metrc LabResultsReport CSV file."
            return
        self.qa_importing = True
        yield
        results: list[dict[str, Any]] = []
        for file in files:
            try:
                results.append(import_lab_results_bytes(file.name, await file.read()))
            except Exception as error:
                results.append({
                    "File": file.name, "Status": "Error",
                    "Source Rows": 0, "Stored Rows": 0,
                    "Inserted": 0, "Updated": 0, "Details": str(error),
                })
        result_columns = [
            "File", "Status", "Source Rows", "Stored Rows", "Inserted",
            "Updated", "Details",
        ]
        self.qa_import_results = [
            [row.get(column, "") for column in result_columns] for row in results
        ]
        try:
            payload = await rx.run_in_thread(
                lambda: load_qa_module_data(force_refresh=True)
            )
            self._apply_qa_payload(payload)
            self.qa_message = "Lab import finished. Duplicate files were skipped safely."
        except Exception as error:
            self.qa_error = f"The files were processed, but QA could not refresh: {error}"
        finally:
            self.qa_importing = False
        yield rx.clear_selected_files("qa_lab_upload")

    @rx.event
    async def import_qa_lab_summary_files(self, files: list[rx.UploadFile]):
        self.qa_error = ""
        self.qa_message = ""
        self.qa_import_results = []
        if not self._require_active_session():
            self.qa_error = "Your session expired. Sign in again to import lab data."
            return
        if not files:
            self.qa_error = "Choose at least one Preliminary Results Summary .xlsx file."
            return
        self.qa_importing = True
        yield
        results: list[dict[str, Any]] = []
        for file in files:
            try:
                results.append(import_lab_summary_bytes(file.name, await file.read()))
            except Exception as error:
                results.append({
                    "File": file.name, "Status": "Error",
                    "Source Rows": 0, "Stored Rows": 0,
                    "Inserted": 0, "Updated": 0, "Details": str(error),
                })
        result_columns = [
            "File", "Status", "Source Rows", "Stored Rows", "Inserted",
            "Updated", "Details",
        ]
        self.qa_import_results = [
            [row.get(column, "") for column in result_columns] for row in results
        ]
        try:
            payload = await rx.run_in_thread(
                lambda: load_qa_module_data(force_refresh=True)
            )
            self._apply_qa_payload(payload)
            self.qa_message = (
                "Lab Direct import finished. Only records explicitly marked PASSED "
                "are authorized for label printing."
            )
        except Exception as error:
            self.qa_error = f"The files were processed, but QA could not refresh: {error}"
        finally:
            self.qa_importing = False
        yield rx.clear_selected_files("qa_lab_summary_upload")

    def _qa_scope_rows(self, operation: str, test_type: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self._qa_packages:
            if str(row.get("operation", "")) != operation:
                continue
            if test_type != "All Test Types" and str(row.get("qa_test_type", "")) != test_type:
                continue
            if self.brand_filter != "All Brands" and str(row.get("brand", "")) != self.brand_filter:
                continue
            if self.strain_filter != "All Strains" and str(row.get("strain", "")) != self.strain_filter:
                continue
            if (
                self.sku_filter != "All SKU Types"
                and not self._qa_sku_compatible(row, self.sku_filter)
            ):
                continue
            rows.append(row)
        return rows

    @staticmethod
    def _qa_sku_compatible(row: dict[str, Any], selected_sku: str) -> bool:
        """Map a compliance test family to the finished SKU family it covers."""
        row_sku = str(row.get("sku_type", "")).strip().lower()
        selected = str(selected_sku or "").strip().lower()
        if not selected or selected == "all sku types" or row_sku == selected:
            return True
        test_type = str(row.get("qa_test_type", "")).strip().lower()
        family_patterns = {
            "flower": r"\bflower\b|\bsmalls?\b",
            "pre-rolls": r"\bpre[- ]?roll\b|\bpreroll\b",
            "vapes": r"\bvape\b|\bcartridge\b|\bdisposable\b",
            "edibles": r"\bedible\b|\bgumm(?:y|ies)\b",
            "concentrates": r"\bconcentrate\b|\brosin\b|\bbadder\b|\bdiamonds?\b",
        }
        pattern = family_patterns.get(test_type)
        return bool(pattern and re.search(pattern, selected, re.I))

    @staticmethod
    def _qa_detail_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "Package Tag": str(row.get("package_tag", "")),
                "Test Date": str(row.get("test_date", "")),
                "Brand": str(row.get("brand", "")),
                "Strain": str(row.get("strain", "")),
                "SKU Type": str(row.get("sku_type", "")),
                "Test Type": str(row.get("qa_test_type", "")),
                "Status": str(row.get("qa_outcome", "")),
                "Total THC": row.get("total_thc", ""),
                "Total Terpenes": row.get("total_terpenes", ""),
            }
            for row in rows
        ]

    @staticmethod
    def _qa_metrics(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        completed = [row for row in rows if row.get("qa_outcome") in {"Passed", "Failed"}]
        passed = sum(row.get("qa_outcome") == "Passed" for row in completed)
        failed = sum(row.get("qa_outcome") == "Failed" for row in completed)
        pending = sum(row.get("qa_outcome") == "Pending" for row in rows)
        rate = passed / len(completed) if completed else 0
        return [
            {"Label": "Pass Success Rate", "Value": f"{rate:.1%}"},
            {"Label": "Completed Batches", "Value": f"{len(completed):,}"},
            {"Label": "Passed", "Value": f"{passed:,}"},
            {"Label": "Failed", "Value": f"{failed:,}"},
            {"Label": "Pending", "Value": f"{pending:,}"},
        ]

    @staticmethod
    def _qa_pass_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        completed = [row for row in rows if row.get("qa_outcome") in {"Passed", "Failed"}]
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in completed:
            key = (str(row.get("qa_test_type", "")), str(row.get("strain", "")))
            group = groups.setdefault(key, {
                "Test Type": key[0], "Strain": key[1],
                "Completed Batches": 0, "Passed": 0, "Failed": 0,
            })
            group["Completed Batches"] += 1
            group[str(row.get("qa_outcome"))] += 1
        result = []
        for group in groups.values():
            group["Pass Success Rate"] = round(
                group["Passed"] / group["Completed Batches"] * 100, 1
            )
            result.append(group)
        return sorted(result, key=lambda row: (-row["Completed Batches"], row["Strain"]))

    @staticmethod
    def _qa_potency_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tested = [
            row for row in rows
            if row.get("total_thc") is not None or row.get("total_terpenes") is not None
        ]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in tested:
            groups.setdefault((
                str(row.get("qa_test_type", "")), str(row.get("strain", ""))
            ), []).append(row)
        result: list[dict[str, Any]] = []
        for (test_type, strain), group in groups.items():
            values: dict[str, list[float]] = {"thc": [], "terpenes": []}
            for row in group:
                for source, target in [("total_thc", "thc"), ("total_terpenes", "terpenes")]:
                    try:
                        value = float(row.get(source))
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(value):
                        values[target].append(value)
            def stat(values_list: list[float], kind: str) -> float | str:
                if not values_list:
                    return ""
                if kind == "avg":
                    return round(sum(values_list) / len(values_list), 2)
                return round(min(values_list) if kind == "min" else max(values_list), 2)
            result.append({
                "Test Type": test_type, "Strain": strain,
                "Tested Batches": len({str(row.get("package_tag", "")) for row in group}),
                "Avg Total THC": stat(values["thc"], "avg"),
                "Min Total THC": stat(values["thc"], "min"),
                "Max Total THC": stat(values["thc"], "max"),
                "Avg Total Terpenes": stat(values["terpenes"], "avg"),
                "Min Total Terpenes": stat(values["terpenes"], "min"),
                "Max Total Terpenes": stat(values["terpenes"], "max"),
            })
        return sorted(result, key=lambda row: (-row["Tested Batches"], row["Strain"]))

    @staticmethod
    def _qa_table_matrix(
        rows: list[dict[str, Any]], columns: list[str]
    ) -> list[list[Any]]:
        """Convert QA records to the row matrix expected by rx.data_table."""
        return [[row.get(column, "") for column in columns] for row in rows]

    @rx.var(cache=True)
    def qa_cultivation_rows(self) -> list[dict[str, Any]]:
        return self._qa_scope_rows("Cultivation", self.qa_cultivation_test_type)

    @rx.var(cache=True)
    def qa_manufacturing_rows(self) -> list[dict[str, Any]]:
        return self._qa_scope_rows("Manufacturing", self.qa_manufacturing_test_type)

    @rx.var(cache=True)
    def qa_cultivation_detail(self) -> list[list[Any]]:
        return self._qa_table_matrix(
            self._qa_detail_rows(self.qa_cultivation_rows)[:250],
            [
                "Package Tag", "Test Date", "Brand", "Strain", "SKU Type",
                "Test Type", "Status", "Total THC", "Total Terpenes",
            ],
        )

    @rx.var(cache=True)
    def qa_manufacturing_detail(self) -> list[list[Any]]:
        return self._qa_table_matrix(
            self._qa_detail_rows(self.qa_manufacturing_rows)[:250],
            [
                "Package Tag", "Test Date", "Brand", "Strain", "SKU Type",
                "Test Type", "Status", "Total THC", "Total Terpenes",
            ],
        )

    @rx.var(cache=True)
    def qa_cultivation_pass_page_size(self) -> int:
        return int(self.qa_cultivation_pass_rows_per_page)

    @rx.var(cache=True)
    def qa_cultivation_potency_page_size(self) -> int:
        return int(self.qa_cultivation_potency_rows_per_page)

    @rx.var(cache=True)
    def qa_cultivation_detail_page_size(self) -> int:
        return int(self.qa_cultivation_detail_rows_per_page)

    @rx.var(cache=True)
    def qa_manufacturing_pass_page_size(self) -> int:
        return int(self.qa_manufacturing_pass_rows_per_page)

    @rx.var(cache=True)
    def qa_manufacturing_potency_page_size(self) -> int:
        return int(self.qa_manufacturing_potency_rows_per_page)

    @rx.var(cache=True)
    def qa_manufacturing_detail_page_size(self) -> int:
        return int(self.qa_manufacturing_detail_rows_per_page)

    @rx.var(cache=True)
    def qa_cultivation_metrics(self) -> list[dict[str, str]]:
        return self._qa_metrics(self.qa_cultivation_rows)

    @rx.var(cache=True)
    def qa_manufacturing_metrics(self) -> list[dict[str, str]]:
        return self._qa_metrics(self.qa_manufacturing_rows)

    @rx.var(cache=True)
    def qa_cultivation_pass_summary(self) -> list[list[Any]]:
        return self._qa_table_matrix(
            self._qa_pass_rows(self.qa_cultivation_rows),
            [
                "Test Type", "Strain", "Completed Batches", "Passed",
                "Failed", "Pass Success Rate",
            ],
        )

    @rx.var(cache=True)
    def qa_manufacturing_pass_summary(self) -> list[list[Any]]:
        return self._qa_table_matrix(
            self._qa_pass_rows(self.qa_manufacturing_rows),
            [
                "Test Type", "Strain", "Completed Batches", "Passed",
                "Failed", "Pass Success Rate",
            ],
        )

    @rx.var(cache=True)
    def qa_cultivation_potency(self) -> list[list[Any]]:
        return self._qa_table_matrix(
            self._qa_potency_rows(self.qa_cultivation_rows),
            [
                "Test Type", "Strain", "Tested Batches", "Avg Total THC",
                "Min Total THC", "Max Total THC", "Avg Total Terpenes",
                "Min Total Terpenes", "Max Total Terpenes",
            ],
        )

    @rx.var(cache=True)
    def qa_manufacturing_potency(self) -> list[list[Any]]:
        return self._qa_table_matrix(
            self._qa_potency_rows(self.qa_manufacturing_rows),
            [
                "Test Type", "Strain", "Tested Batches", "Avg Total THC",
                "Min Total THC", "Max Total THC", "Avg Total Terpenes",
                "Min Total Terpenes", "Max Total Terpenes",
            ],
        )

    @staticmethod
    def _qa_test_type_options(rows: list[dict[str, Any]], defaults: list[str]) -> list[str]:
        present = {str(row.get("qa_test_type", "")) for row in rows}
        return ["All Test Types", *[value for value in defaults if value in present or value == "Other / Needs Review"]]

    @rx.var(cache=True)
    def qa_cultivation_test_type_options(self) -> list[str]:
        return self._qa_test_type_options(
            [row for row in self._qa_packages if row.get("operation") == "Cultivation"],
            ["Flower", "Pre-Rolls", "Other / Needs Review"],
        )

    @rx.var(cache=True)
    def qa_manufacturing_test_type_options(self) -> list[str]:
        return self._qa_test_type_options(
            [row for row in self._qa_packages if row.get("operation") == "Manufacturing"],
            ["Pre-Rolls", "Vapes", "Concentrates", "Edibles", "Flower", "Other / Needs Review"],
        )

    @staticmethod
    def _qa_consistency_options(rows: list[dict[str, Any]]) -> list[str]:
        return sorted({
            str(row.get("strain", "")) for row in rows
            if str(row.get("strain", "")).strip()
            and any(
                value not in {None, ""}
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
                for value in (row.get("total_thc"), row.get("total_terpenes"))
            )
        })

    @rx.var(cache=True)
    def qa_cultivation_consistency_options(self) -> list[str]:
        return self._qa_consistency_options(self.qa_cultivation_rows)

    @rx.var(cache=True)
    def qa_manufacturing_consistency_options(self) -> list[str]:
        return self._qa_consistency_options(self.qa_manufacturing_rows)

    @staticmethod
    def _qa_chart_rows(rows: list[dict[str, Any]], strain: str) -> list[dict[str, Any]]:
        def finite(value: Any) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return round(number, 3) if math.isfinite(number) else None

        result = []
        for row in rows:
            if str(row.get("strain", "")) != strain:
                continue
            chart_row = {
                "Test Date": str(row.get("test_date", "")),
                "Total THC": finite(row.get("total_thc")),
                "Total Terpenes": finite(row.get("total_terpenes")),
            }
            if chart_row["Total THC"] is not None or chart_row["Total Terpenes"] is not None:
                result.append(chart_row)
        return sorted(result, key=lambda row: row["Test Date"])

    @rx.var(cache=True)
    def qa_cultivation_chart(self) -> list[dict[str, Any]]:
        return self._qa_chart_rows(
            self.qa_cultivation_rows, self.qa_cultivation_consistency_strain
        )

    @rx.var(cache=True)
    def qa_manufacturing_chart(self) -> list[dict[str, Any]]:
        return self._qa_chart_rows(
            self.qa_manufacturing_rows, self.qa_manufacturing_consistency_strain
        )

    @rx.var(cache=True)
    def qa_lookup_matches(self) -> list[dict[str, Any]]:
        return self._qa_lookup_rows(self.qa_lookup_search.strip().lower())

    def _qa_lookup_rows(self, search: str = "") -> list[dict[str, Any]]:
        """Return submitted-search or browse rows from normalized state data."""
        rows = self._qa_packages
        if search:
            rows = [
                row for row in rows
                if search in str(row.get("package_tag", "")).lower()
                or search in str(row.get("source_harvest_names", "")).lower()
                or search in str(row.get("source_package_labels", "")).lower()
            ]
            existing_tags = {
                str(row.get("package_tag", "")).strip().lower() for row in rows
            }
            inventory_matches = [
                self._inventory_qa_record(row)
                for row in self.all_inventory
                if (
                    search in str(row.get("Metrc Tag", "")).lower()
                    or search in str(row.get("Source Harvest", "")).lower()
                )
                and str(row.get("Metrc Tag", "")).strip().lower()
                not in existing_tags
            ]
            rows = [*rows, *inventory_matches]
        elif any(value != default for value, default in (
            (self.qa_label_operation_filter, "All Operations"),
            (self.qa_label_brand_filter, "All Brands"),
            (self.qa_label_strain_filter, "All Strains"),
            (self.qa_label_sku_filter, "All SKU Types"),
        )):
            rows = [
                row for row in rows
                if (
                    self.qa_label_operation_filter == "All Operations"
                    or str(row.get("operation", "")) == self.qa_label_operation_filter
                )
                and (
                    self.qa_label_brand_filter == "All Brands"
                    or str(row.get("brand", "")) == self.qa_label_brand_filter
                )
                and (
                    self.qa_label_strain_filter == "All Strains"
                    or str(row.get("strain", "")) == self.qa_label_strain_filter
                )
                and (
                    self.qa_label_sku_filter == "All SKU Types"
                    or str(row.get("sku_type", row.get("qa_test_type", ""))) == self.qa_label_sku_filter
                )
            ]
        else:
            return []
        return rows[:100]

    @rx.var(cache=True)
    def qa_label_operation_options(self) -> list[str]:
        return ["All Operations", *sorted({
            str(row.get("operation", row.get("Operation", "")))
            for row in [*self._qa_packages, *self.qa_label_catalog]
            if str(row.get("operation", row.get("Operation", "")))
        })]

    @rx.var(cache=True)
    def qa_label_brand_options(self) -> list[str]:
        return ["All Brands", *sorted({
            str(row.get("brand", row.get("Brand", "")))
            for row in [*self._qa_packages, *self.qa_label_catalog]
            if str(row.get("brand", row.get("Brand", "")))
            and (
                self.qa_label_operation_filter == "All Operations"
                or str(row.get("operation", row.get("Operation", ""))) == self.qa_label_operation_filter
            )
        })]

    @rx.var(cache=True)
    def qa_label_strain_options(self) -> list[str]:
        return ["All Strains", *sorted({
            str(row.get("strain", row.get("Strain", "")))
            for row in [*self._qa_packages, *self.qa_label_catalog]
            if str(row.get("strain", row.get("Strain", "")))
            and (
                self.qa_label_brand_filter == "All Brands"
                or str(row.get("brand", row.get("Brand", ""))) == self.qa_label_brand_filter
            )
        })]

    @rx.var(cache=True)
    def qa_label_sku_options(self) -> list[str]:
        return ["All SKU Types", *sorted({
            str(row.get("sku_type", row.get("qa_test_type", row.get("SKU Type", ""))))
            for row in [*self._qa_packages, *self.qa_label_catalog]
            if str(row.get("sku_type", row.get("qa_test_type", row.get("SKU Type", ""))))
            and (
                self.qa_label_strain_filter == "All Strains"
                or str(row.get("strain", row.get("Strain", ""))) == self.qa_label_strain_filter
            )
        })]

    @rx.var(cache=True)
    def qa_filtered_label_catalog(self) -> list[dict[str, Any]]:
        search = self.qa_label_catalog_search.strip().lower()
        rows = []
        for row in self.qa_label_catalog:
            if (
                self.qa_label_operation_filter != "All Operations"
                and row.get("Operation") != self.qa_label_operation_filter
            ):
                continue
            if self.qa_label_brand_filter != "All Brands" and row.get("Brand") != self.qa_label_brand_filter:
                continue
            if self.qa_label_strain_filter != "All Strains" and row.get("Strain") != self.qa_label_strain_filter:
                continue
            if self.qa_label_sku_filter != "All SKU Types" and row.get("SKU Type") != self.qa_label_sku_filter:
                continue
            if search and search not in " ".join(str(value or "") for value in row.values()).lower():
                continue
            rows.append(row)
        return rows

    @rx.var(cache=True)
    def qa_native_template_options(self) -> list[str]:
        return [str(row.get("Template Name", "")) for row in self.qa_filtered_label_catalog]

    @rx.var(cache=True)
    def qa_lookup_options(self) -> list[str]:
        return [self._qa_lookup_label(row) for row in self.qa_lookup_matches]

    @rx.var(cache=True)
    def qa_template_options(self) -> list[str]:
        operation = str(self.qa_selected_package.get("operation", ""))
        return [
            str(row.get("Template Name", "")) for row in self.qa_templates
            if str(row.get("Scope", "Both")) in {"Both", operation}
        ]

    @rx.var(cache=True)
    def qa_label_preview(self) -> list[dict[str, str]]:
        if not self.qa_selected_package:
            return []
        template = next(
            (row for row in self.qa_templates if row.get("Template Name") == self.qa_selected_template),
            self.qa_templates[0] if self.qa_templates else {},
        )
        record = dict(self.qa_selected_package)
        if self.qa_override_expiration:
            record["expiration_date"] = self.qa_manual_expiration
        preview = []
        fields = template.get("Fields", list(QA_LABEL_FIELDS))
        if str(record.get("record_origin", "")).startswith("Current Inventory"):
            fields = [
                "package_tag", "brand", "strain", "sku_type", "item",
                "category", "location", "source_harvest_names",
                "lab_testing_status", "record_origin",
            ]
        for field in fields:
            value = record.get(field, "")
            if field in {"total_thc", "total_terpenes"} and value not in {None, ""}:
                try:
                    value = f"{float(value):.2f}%"
                except (TypeError, ValueError):
                    pass
            preview.append({"Field": QA_LABEL_FIELDS.get(field, field), "Value": str(value or "")})
        return preview

    @rx.var(cache=True)
    def qa_selected_analyte_rows(self) -> list[list[str]]:
        """Classify, filter, and matrix-normalize detailed analyte results."""
        columns = ["Category", "Test Date", "Test", "Result", "Passed"]
        return [
            ["" if row.get(column) is None else str(row.get(column, "")) for column in columns]
            for row in self.qa_categorized_analytes
            if (
                self.qa_analyte_category_filter == "All Categories"
                or row.get("Category") == self.qa_analyte_category_filter
            )
        ]

    @staticmethod
    def _qa_analyte_category(test_name: Any) -> str:
        """Map a laboratory test name into a stable compliance family."""
        name = str(test_name or "").strip().lower()
        if re.search(r"water\s*activity|moisture(?:\s*content)?", name):
            return "Water Activity"
        if re.search(r"aflatoxin|ochratoxin|mycotoxin", name):
            return "Mycotoxins"
        if re.search(
            r"\b(?:arsenic|cadmium|chromium|lead|mercury)\b|heavy\s*metals?",
            name,
        ):
            return "Heavy Metals"
        if re.search(
            r"aspergillus|salmonella|shiga|e\.?\s*coli|coliform|"
            r"yeast|mold|microbial|aerobic|enterobacter",
            name,
        ):
            return "Microbials"
        if re.search(
            r"pesticide|insecticide|fungicide|abamectin|acephate|acequinocyl|"
            r"ancymidol|ethephon|flurprimidol|phosmet|piperonyl\s*butoxide|"
            r"acetamiprid|aldicarb|azoxystrobin|bifenazate|bifenthrin|boscalid|"
            r"carbaryl|carbofuran|chlorantraniliprole|chlorphenapyr|chlorpyrifos|"
            r"clofentezine|cyfluthrin|cyhalothrin|cypermethrin|daminozide|"
            r"dichlorvos|diazinon|dimethoate|etofenprox|etoxazole|fenhexamid|"
            r"fenoxycarb|fenpyroximate|fipronil|flonicamid|fludioxonil|"
            r"hexythiazox|imazalil|imidacloprid|kresoxim|malathion|metalaxyl|"
            r"methiocarb|methomyl|mevinphos|myclobutanil|naled|oxamyl|"
            r"paclobutrazol|parathion|permethrin|phenothrin|propiconazole|"
            r"propoxur|pyraclostrobin|pyrethrin|pyridaben|spinetoram|spinosad|"
            r"spiromesifen|spirotetramat|spiroxamine|tebuconazole|tebufenozide|"
            r"tetrachlorvinphos|thiacloprid|thiamethoxam|trifloxystrobin",
            name,
        ):
            return "Pesticides"
        if re.search(
            r"\b(?:thc|thca|thcv|thcva|cbd|cbda|cbdv|cbg|cbga|cbn|cbc|cbca)\b|"
            r"cannabinoid",
            name,
        ):
            return "Cannabinoids"
        if re.search(
            r"terpene|myrcene|limonene|pinene|caryophyllene|linalool|humulene|"
            r"terpinolene|ocimene|bisabolol|camphene|borneol|eucalyptol|"
            r"farnesene|geraniol|guaiol|nerolidol|pulegone|sabinene|terpineol",
            name,
        ):
            return "Terpenes"
        return "Other / Needs Review"

    @rx.var(cache=True)
    def qa_categorized_analytes(self) -> list[dict[str, Any]]:
        return [
            {**row, "Category": self._qa_analyte_category(row.get("Test", ""))}
            for row in self.qa_selected_analytes
        ]

    @rx.var(cache=True)
    def qa_analyte_category_counts(self) -> list[dict[str, Any]]:
        counts = {category: 0 for category in QA_ANALYTE_CATEGORIES[1:]}
        for row in self.qa_categorized_analytes:
            category = str(row.get("Category", "Other / Needs Review"))
            counts[category] = counts.get(category, 0) + 1
        return [
            {"Category": category, "Count": count}
            for category, count in counts.items() if count
        ]

    @rx.var(cache=True)
    def qa_filtered_analyte_count(self) -> int:
        return len(self.qa_selected_analyte_rows)

    @rx.var(cache=True)
    def qa_adjusted_coa_status(self) -> str:
        if self.qa_adjusted_coa:
            return "Adjusted COA — verified"
        if str(self.qa_selected_package.get("record_origin", "")).startswith("Lab Direct"):
            return "Lab Direct — passed / awaiting Metrc"
        return "Metrc data — estimated"

    @rx.var(cache=True)
    def qa_adjusted_other_preview(self) -> str:
        total = self._adjusted_number(self.qa_adjusted_total_terpenes)
        values = [
            self._adjusted_number(value) for value in self.qa_adjusted_terpene_values
        ]
        if total is None or any(value is None for value in values):
            return "Enter all four terpene values"
        precise = [float(value) for value in values if value is not None]
        if sum(precise) > total + 0.0005:
            return "Top-three values exceed Total Terpenes"
        other = adjusted_other_terpenes(total, precise)
        return "" if other is None else f"{other:.2f}%"

    @rx.var(cache=True)
    def qa_adjusted_coa_review_rows(self) -> list[dict[str, Any]]:
        entries = [
            (
                "Total Terpenes",
                self.qa_adjusted_metrc_total_terpenes,
                self.qa_adjusted_total_terpenes,
                0.011,
            ),
            *[
                (
                    self.qa_adjusted_terpene_names[index] or f"Terpene {index + 1}",
                    self.qa_adjusted_metrc_terpene_values[index],
                    self.qa_adjusted_terpene_values[index],
                    0.011,
                )
                for index in range(3)
            ],
            (
                "Total CBG",
                self.qa_adjusted_metrc_total_cbg,
                self.qa_adjusted_total_cbg,
                0.021,
            ),
        ]
        rows: list[dict[str, Any]] = []
        for label, metrc_text, entered_text, tolerance in entries:
            entered = self._adjusted_number(entered_text)
            metrc = self._adjusted_number(metrc_text)
            if entered is None:
                status, color = "Required", "gray"
            elif metrc is not None and abs(entered - metrc) > tolerance:
                status, color = "Suspect", "orange"
            else:
                status, color = "Matches source range", "teal"
            rows.append({
                "Field": label,
                "Source": "—" if metrc is None else f"{metrc:.3f}%",
                "Entered": "—" if entered is None else f"{entered:.3f}%",
                "Status": status,
                "Color": color,
            })
        return rows

    @rx.var(cache=True)
    def qa_adjusted_coa_has_suspect_values(self) -> bool:
        _payload, _errors, suspects = self._adjusted_coa_payload()
        return bool(suspects)

    def _qa_zebra_context(self) -> tuple[dict[str, Any], list[str]]:
        return prepare_label_context(
            self.qa_selected_package,
            self.qa_selected_analytes,
            self.qa_zebra_package_format,
            bulk_uid=self.qa_zebra_bulk_uid,
            harvest_date=self.qa_zebra_harvest_date,
            lot_number=self.qa_zebra_lot_number,
            quantity=self.qa_zebra_quantity,
            adjusted_coa=self.qa_adjusted_coa,
        )

    @rx.var(cache=True)
    def qa_zebra_preview(self) -> list[list[str]]:
        if not self.qa_selected_package:
            return []
        context, _errors = self._qa_zebra_context()
        return [
            ["Lab Sample Tag", str(context.get("lab_tag", ""))],
            ["Printed Bulk UID", str(context.get("bulk_uid", ""))],
            ["Barcode", str(context.get("barcode_value", ""))],
            ["Package Format", str(context.get("package_format", ""))],
            ["Automatic Layout", str(context.get("layout", ""))],
            ["Net / Serving", (
                str(context.get("net_weight", "")) + " / "
                + str(context.get("serving_size", ""))
            )],
            ["Harvest Date", str(context.get("harvest_date", ""))],
            ["Expiration Date", str(context.get("expiration_date", ""))],
            ["Lot", str(context.get("lot_number", ""))],
            ["COA Values", self.qa_adjusted_coa_status],
            ["Total Terpenes", (
                "" if context.get("analytes", {}).get("total_terpenes") is None
                else f"{context['analytes']['total_terpenes']:.2f}%"
            )],
            ["Total CBG", (
                "" if context.get("analytes", {}).get("total_cbg") is None
                else f"{context['analytes']['total_cbg']:.2f}%"
            )],
            ["Other Terpenes", (
                "" if context.get("analytes", {}).get("other_terpenes") is None
                else f"{context['analytes']['other_terpenes']:.2f}%"
            )],
            ["Printer", self.qa_zebra_printer],
            ["Quantity", str(context.get("quantity", 1))],
        ]

    @rx.var(cache=True)
    def qa_zebra_validation_message(self) -> str:
        if not self.qa_selected_package:
            return "Select a passed laboratory record first."
        if self.qa_selected_analytes_loading:
            return "Loading the complete laboratory values for this label..."
        _context, errors = self._qa_zebra_context()
        if errors:
            return " ".join(errors)
        return "All required production-label fields are ready."

    @rx.var(cache=True)
    def qa_zebra_ready(self) -> bool:
        if not self.qa_selected_package or self.qa_selected_analytes_loading:
            return False
        _context, errors = self._qa_zebra_context()
        return not errors

    @rx.var(cache=True)
    def qa_general_compliance_summary(self) -> list[list[Any]]:
        if not self.qa_selected_package:
            return []
        record = self.qa_selected_package
        coa_status = (
            "No associated COA found — general inventory summary"
            if str(record.get("record_origin", "")).startswith("Current Inventory")
            else str(record.get("record_origin", "") or "Associated lab/COA record found")
        )
        values = [
            ("Metrc Tag", record.get("package_tag", "")),
            ("Brand", record.get("brand", "")),
            ("Strain", record.get("strain", "")),
            ("SKU Type", record.get("sku_type", "")),
            ("Item", record.get("item", "")),
            ("Source Harvest", record.get("source_harvest_names", "")),
            ("QA Status", record.get("lab_testing_status", "")),
            ("Record Source", record.get("record_origin", "")),
            ("Source Comparison", record.get("source_discrepancy", "")),
            ("COA Status", coa_status),
            ("Test Date", record.get("test_date", "")),
            ("Expiration Date", record.get("expiration_date", "")),
            ("Total THC", record.get("total_thc", "")),
            ("Total Terpenes", record.get("total_terpenes", "")),
            ("Selected Label", self.qa_selected_native_template),
        ]
        return [[label, "" if value is None else str(value)] for label, value in values]

    @rx.event
    def download_qa_label(self):
        if not self.qa_selected_package:
            self.qa_error = "Select a package or harvest record first."
            return
        template = next(
            (row for row in self.qa_templates if row.get("Template Name") == self.qa_selected_template),
            self.qa_templates[0] if self.qa_templates else {},
        )
        record = dict(self.qa_selected_package)
        if self.qa_override_expiration:
            record["expiration_date"] = self.qa_manual_expiration
        rows = []
        for label, value in self.qa_general_compliance_summary:
            if not str(value or "").strip():
                continue
            rows.append(
                "<div class='row'><span class='label'>" + escape(str(label))
                + "</span><span class='value'>" + escape(str(value)) + "</span></div>"
            )
        width, height = "4in", "6in"
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            f"@page{{size:{width} {height};margin:0}}"
            f"html,body{{margin:0;width:{width};height:{height};overflow:hidden;"
            "font-family:Arial,sans-serif;color:#111}}"
            f".card{{width:{width};height:{height};overflow:hidden;box-sizing:border-box;"
            "padding:.15in;border:1px solid #999;break-inside:avoid;page-break-inside:avoid}}"
            "h1{font-size:16px;margin:0 0 6px}.row{display:grid;grid-template-columns:1.05in 1fr;"
            "gap:6px;border-top:1px solid #ddd;padding:3px 0;line-height:1.15}"
            ".label{font-size:8px;font-weight:bold;text-transform:uppercase;color:#555}"
            ".value{font-size:10px;overflow-wrap:anywhere}.footer{margin-top:6px;font-size:7px;color:#555}"
            "</style></head><body><div class='card'><h1>QCC Compliance Summary</h1>"
            + "".join(rows) + "<div class='footer'>"
            + escape(str(template.get("Footer", "Verify current package status in Metrc before release.")))
            + "</div></div></body></html>"
        )
        log_qa_label_download(record, template, self.auth_email or self.auth_name)
        self.qa_message = "Printable QA label generated and recorded in the audit log."
        self.qa_error = ""
        return rx.download(
            data=html.encode("utf-8"),
            filename=f"qa_label_{record.get('package_tag', 'record')}.html",
        )

    @rx.event
    def download_zebra_zpl(self):
        """Generate a validated printer-ready test file for the local Zebra."""
        self.qa_zebra_message = ""
        self.qa_zebra_error = ""
        if not self._require_active_session():
            self.qa_zebra_error = "Your session expired. Sign in again to generate a label."
            return
        if self.qa_selected_analytes_loading:
            self.qa_zebra_error = "Wait for the complete laboratory values to finish loading."
            return
        context, errors = self._qa_zebra_context()
        try:
            zpl = build_zpl(context, errors)
        except ValueError as error:
            self.qa_zebra_error = str(error)
            return
        audit_template = {
            "Template ID": "qcc-zebra-cultivation-v1",
            "Version": 1,
            "Footer": context.get("layout", ""),
        }
        audit_record = {
            **self.qa_selected_package,
            "printed_bulk_uid": context.get("bulk_uid", ""),
            "package_format": context.get("package_format", ""),
            "layout": context.get("layout", ""),
            "quantity": context.get("quantity", 1),
            "printer": self.qa_zebra_printer,
            "expiration_date": context.get("expiration_date", ""),
        }
        log_qa_label_download(
            audit_record,
            audit_template,
            self.auth_email or self.auth_name,
            output_type="zpl",
        )
        safe_name = re.sub(
            r"[^A-Za-z0-9_-]+", "_", str(context.get("strain", "label"))
        ).strip("_") or "label"
        self.qa_zebra_message = (
            "Validated Zebra ZPL generated. Send one test file through Zebra Setup "
            "Utilities before enabling direct browser printing."
        )
        return rx.download(
            data=zpl.encode("utf-8"),
            filename=f"{safe_name}_{context.get('suffix', '')}_TEST.zpl",
        )

    @rx.event
    def change_inventory_stage_filter(self, value: str):
        self.inventory_stage_filter = value
        self.inventory_page = 1

    @rx.event
    def change_inventory_license_filter(self, value: str):
        self.inventory_license_filter = value
        self.inventory_page = 1

    @rx.event
    def change_inventory_qa_filter(self, value: str):
        self.inventory_qa_filter = value
        self.inventory_page = 1

    @rx.event
    def change_inventory_category_filter(self, value: str):
        self.inventory_category_filter = value
        self.inventory_page = 1

    @rx.event
    def change_inventory_location_filter(self, value: str):
        self.inventory_location_filter = value
        self.inventory_page = 1

    @rx.event
    def change_inventory_ownership_filter(self, value: str):
        self.inventory_ownership_filter = value
        self.inventory_page = 1

    @rx.event
    def change_inventory_include_retention(self, value: bool):
        self.inventory_include_retention = value
        self.inventory_page = 1

    @rx.event
    def change_inventory_weight_unit(self, value: str):
        """Change only the presentation unit; stored weights remain grams."""
        self.inventory_weight_unit = value

    @rx.event
    def change_aging_cpg_band(self, value: str):
        self.aging_cpg_band_filter = value
        self.inventory_page = 1

    @rx.event
    def change_aging_bulk_band(self, value: str):
        self.aging_bulk_band_filter = value
        self.inventory_page = 1

    @rx.event
    def change_summarize_cpg(self, value: bool):
        self.summarize_cpg_inventory = value

    @rx.event
    def change_summarize_bulk(self, value: bool):
        self.summarize_bulk_inventory = value

    @rx.event
    def change_summarize_wip(self, value: bool):
        self.summarize_wip_inventory = value

    @rx.event
    def change_summarize_aging_cpg(self, value: bool):
        self.summarize_aging_cpg = value

    @rx.event
    def change_summarize_aging_bulk(self, value: bool):
        self.summarize_aging_bulk = value

    @rx.event
    def change_summarize_all(self, value: bool):
        self.summarize_all_inventory = value

    @rx.event
    def change_summarize_review(self, value: bool):
        self.summarize_needs_review = value

    @rx.event
    def change_executive_facility_filter(self, value: str):
        self.executive_facility_filter = value

    @rx.event
    def change_executive_ownership_filter(self, value: str):
        self.executive_ownership_filter = value

    @rx.event
    def reset_executive_filters(self):
        self.executive_facility_filter = "All Facilities"
        self.executive_ownership_filter = "QCC-Owned Inventory"

    @rx.event
    def change_inventory_lookup_text(self, value: str):
        self.inventory_lookup_text = value
        if not value.strip():
            self._clear_inventory_lookup_state()

    def _clear_inventory_lookup_state(self) -> None:
        self.inventory_lookup_text = ""
        self.inventory_lookup_message = (
            "Enter a complete Metrc tag to inspect one package."
        )
        self.selected_inventory_details = []

    @rx.event
    def clear_inventory_lookup(self):
        self._clear_inventory_lookup_state()

    @rx.event
    def reset_inventory_filters(self):
        self.inventory_stage_filter = "All Production Stages"
        self.inventory_license_filter = "All Licenses"
        self.inventory_qa_filter = "All QA Statuses"
        self.inventory_category_filter = "All Categories"
        self.inventory_location_filter = "All Locations"
        self.inventory_ownership_filter = "All Ownership Statuses"
        self.inventory_include_retention = False
        self.aging_cpg_band_filter = "All Risk Bands"
        self.aging_bulk_band_filter = "All Age Bands"
        self.inventory_page = 1
        self._clear_inventory_lookup_state()

    @rx.event
    def find_inventory_package(self):
        target = self.inventory_lookup_text.strip().lower()
        if not target:
            self.inventory_lookup_message = "Enter a complete Metrc tag."
            self.selected_inventory_details = []
            return
        match = next(
            (
                row for row in self.all_inventory
                if str(row.get("Metrc Tag", "")).strip().lower() == target
            ),
            None,
        )
        if match is None:
            self.inventory_lookup_message = (
                "This tag is not present in the latest published inventory snapshot."
            )
            self.selected_inventory_details = []
            return
        fields = [
            "Metrc Tag", "Item", "Brand", "Strain", "SKU Type",
            "Production Stage", "QA Status", "Category", "Location",
            "Quantity", "Unit", "Calculated Weight (g)", "Age",
            "Days to Spoil", "Source Harvest", "Ownership Status", "License",
        ]
        self.selected_inventory_details = [
            [field, str(match.get(field, "") or "")] for field in fields
        ]
        self.inventory_lookup_message = "Package found in the latest snapshot."

    @rx.event
    async def reset_filters(self):
        if self.global_filters_resetting:
            return
        self.global_filters_resetting = True
        # Flush the visible spinner/disabled button before changing the data.
        yield
        await asyncio.sleep(0.5)
        self.brand_filter = "All Brands"
        self.strain_filter = "All Strains"
        self.sku_filter = "All SKU Types"
        self.search_text = ""
        self.sku_planning_page = 1
        self.inventory_page = 1
        self.global_filters_resetting = False

    def _selected_sku(self) -> tuple[str, str, str] | None:
        if (
            self.brand_filter == "All Brands"
            or self.strain_filter == "All Strains"
            or self.sku_filter == "All SKU Types"
        ):
            self.sku_detail_message = (
                "Select one Brand, Strain, and SKU Type in Global Filters first."
            )
            self.selected_sku_package_details = []
            self.sku_detail_open = True
            return None
        return self.brand_filter, self.strain_filter, self.sku_filter

    @staticmethod
    def _package_detail_rows(rows: list[dict[str, Any]]) -> list[list[str]]:
        columns = [
            "Metrc Tag", "Item", "Production Stage", "Location", "QA Status",
            "Category", "Age", "Quantity", "Unit", "Calculated Weight (g)",
            "Available Weight (g)", "Source Harvest",
        ]
        return [
            [str(row.get(column, "") or "") for column in columns]
            for row in rows
        ]

    @rx.event
    def view_current_sku_packages(self):
        selected = self._selected_sku()
        if selected is None:
            return
        brand, strain, sku_type = selected
        matches = [
            row for row in self._inventory_view_rows("View CPG")
            if str(row.get("Brand", "")) == brand
            and str(row.get("Strain", "")) == strain
            and str(row.get("SKU Type", "")) == sku_type
        ]
        self.sku_detail_title = f"Current Packages - {brand} - {strain} - {sku_type}"
        self.sku_detail_message = (
            f"{len(matches):,} contributing Metrc package(s). Retention rows are identified by Production Stage."
        )
        self.selected_sku_package_details = self._package_detail_rows(matches)
        self.sku_detail_open = True

    @rx.event
    def view_potential_wip_packages(self):
        selected = self._selected_sku()
        if selected is None:
            return
        self._show_potential_wip(*selected)

    @rx.event
    def view_row_potential_wip(
        self, brand: str, strain: str, sku_type: str
    ):
        self._show_potential_wip(brand, strain, sku_type)

    def _show_potential_wip(
        self, brand: str, strain: str, sku_type: str
    ) -> None:
        frame = pd.DataFrame(
            self._inventory_view_rows("View Potential WIP")
        )
        if frame.empty:
            matches: list[dict[str, Any]] = []
        else:
            source = frame.rename(columns={
                "Brand": "brand", "Strain": "strain", "SKU Type": "sku_type",
                "Production Stage": "production_stage", "QA Status": "qa_status",
                "Category": "category", "Item": "item", "Metrc Tag": "package_tag",
                "Available Weight (g)": "available_weight_grams",
            })
            matched = potential_wip_for_sku(source, brand, strain, sku_type)
            if not matched.empty:
                matched["_age_sort"] = pd.to_numeric(
                    matched.get("Age", matched.get("inventory_age_days")),
                    errors="coerce",
                ).fillna(-1)
                matched = matched.sort_values(
                    ["_age_sort", "package_tag"], ascending=[False, True]
                ).drop(columns=["_age_sort"])
            matches = matched.rename(columns={
                "brand": "Brand", "strain": "Strain", "sku_type": "SKU Type",
                "production_stage": "Production Stage", "qa_status": "QA Status",
                "category": "Category", "item": "Item", "package_tag": "Metrc Tag",
                "available_weight_grams": "Available Weight (g)",
            }).to_dict("records")
        available_weight = sum(
            float(row.get("Available Weight (g)", 0) or 0) for row in matches
        )
        self.sku_detail_title = f"Potential WIP - {brand} - {strain} - {sku_type}"
        self.sku_detail_message = (
            f"{len(matches):,} compatible package(s) - {available_weight:,.1f} g available."
        )
        self.selected_sku_package_details = self._package_detail_rows(matches)
        self.sku_detail_open = True

    @rx.event
    def close_sku_detail(self):
        self.sku_detail_open = False

    @rx.event
    def change_sku_detail_open(self, value: bool):
        self.sku_detail_open = value

    @rx.event
    def load_dashboard(self):
        self.auth_checked = False
        self.auth_configured = auth_is_configured()
        self.auth_failed = False
        self.auth_message = "Checking your secure QCC session..."
        yield
        if not self.auth_configured:
            self._clear_employee()
            self.auth_checked = True
            self.auth_message = (
                "Authentication setup is incomplete. Add the Supabase project URL, "
                "publishable key, and public app URL to .env."
            )
            return
        try:
            employee = validate_app_session(str(self.auth_session_token or ""))
        except Exception as error:
            employee = None
            self.auth_message = f"The secure session could not be checked: {error}"
        if employee is None:
            self.auth_redirecting = False
            self._clear_employee()
            self.auth_checked = True
            return
        self._apply_employee(employee)
        self.auth_redirecting = False
        self.auth_checked = True
        self.auth_message = ""
        if self.auth_role == "Admin":
            try:
                self._refresh_team_directory()
            except Exception as error:
                self.admin_error = f"Team & Access could not be loaded: {error}"
        if database_url():
            # Inventory, Sales, and QA are independent published snapshots.
            # Start one protected concurrent warm-up instead of making the
            # first signed-in user wait for three sequential database loads.
            self.loading = True
            self.error_message = ""
            yield
            yield DashboardState.load_initial_data_background
            return
        self.loading = True
        self.error_message = ""
        yield
        try:
            if database_url():
                payload = get_dashboard_data()
                self.using_demo_data = False
            else:
                payload = demo_dashboard_data()
                self.using_demo_data = True
                self.error_message = (
                    "Demo mode: add QCC_SUPABASE_DATABASE_URL to .env to "
                    "read the shared QCC database."
                )
        except Exception as error:
            payload = demo_dashboard_data()
            self.using_demo_data = True
            self.error_message = (
                "Supabase could not be read. Demo data is displayed. "
                f"Backend detail: {error}"
            )
        self.loaded_at = payload["loaded_at"]
        self.rule_version = payload["rule_version"]
        metrics = payload["metrics"]
        snapshot = payload["snapshot"]
        self.units_metric = f"{float(metrics['units']):,.0f}"
        self.value_metric = f"${float(metrics['value']):,.0f}"
        self.customers_metric = f"{int(metrics['customers']):,}"
        self.manifests_metric = f"{int(metrics.get('manifests', 0)):,}"
        self.weighted_price_metric = (
            f"${float(metrics.get('weighted_price', 0)):,.2f}"
        )
        self.stockouts_metric = f"{int(metrics['stockouts']):,}"
        self.open_manifests_metric = f"{int(metrics.get('open_manifests', 0)):,}"
        self.exception_manifests_metric = f"{int(metrics.get('exception_manifests', 0)):,}"
        self.exception_rows_metric = f"{int(metrics.get('exception_rows', 0)):,}"
        self.transfer_rows_metric = f"{int(metrics.get('transfer_rows', 0)):,}"
        self.latest_shipment = metrics.get("latest_shipment") or "—"
        self.snapshot_date = snapshot.get("business_date") or "—"
        self.snapshot_packages = f"{int(snapshot.get('package_count', 0)):,}"
        self.snapshot_skus = f"{int(snapshot.get('sku_count', 0)):,}"
        self.snapshot_detail = f"{int(snapshot.get('detail_count', 0)):,}"
        self.snapshot_cpg_eligible = (
            f"{int(snapshot.get('authoritative_cpg_count', 0)):,}"
        )
        self.inventory_ready = bool(payload.get("inventory_ready", False))
        self.authoritative_cpg_ready = bool(
            payload.get("authoritative_cpg_ready", False)
        )
        self.brands = payload["brands"]
        self.strains = payload.get("strains", [])
        self.sku_types = payload.get("sku_types", [])
        self.monthly = payload["monthly"]
        self.top_skus = payload["top_skus"]
        self.business_pulse = payload.get("business_pulse", [])
        self.velocity_windows = payload.get(
            "velocity_windows", {"All Time": payload["velocity"]}
        )
        self.availability_adjusted_velocity_windows = payload.get(
            "availability_adjusted_velocity_windows", {}
        )
        # Clone Allocation consumes these windows even while the user remains
        # in Cultivation, so force its cached forecast to refresh when the
        # background Sales payload arrives.
        self.cultivation_clone_plan_demand_revision += 1
        self.velocity = self._selected_sku_velocity()
        self.availability_demand_summary = payload.get(
            "availability_demand_summary", []
        )
        self.availability_demand_weekly = payload.get(
            "availability_demand_weekly", []
        )
        self.stockouts = payload["stockouts"]
        self.saved_plans = payload["saved_plans"]
        self.saved_plan_cards = payload.get("saved_plan_cards", [])
        self.production_templates = payload.get("production_templates", [])
        self.calendar = payload["calendar"]
        self.customers = payload.get("customers", [])
        self.retail_delivery_history = payload.get(
            "retail_delivery_history", []
        )
        self.retailer_locations = payload.get("retailer_locations", [])
        self.exceptions = payload.get("exceptions", [])
        self.exception_packages = payload.get("exception_packages", [])
        self._transfer_data = payload.get("transfer_data", [])
        self.transfer_import_log = payload.get("transfer_import_log", [])
        self.cpg_inventory = payload.get("cpg_inventory", [])
        self.bulk_inventory = payload.get("bulk_inventory", [])
        self.wip_inventory = payload.get("wip_inventory", [])
        self.potential_wip_inventory = payload.get("potential_wip_inventory", [])
        self.aging_cpg = payload.get("aging_cpg", [])
        self.aging_bulk = payload.get("aging_bulk", [])
        self.all_inventory = payload.get("all_inventory", [])
        self.needs_review = payload.get("needs_review", [])
        self._apply_optional_module_payload(payload)
        # The fast operational payload already contains the small Production
        # tables. Keep them ready instead of clearing and rereading them when
        # the user first opens Production Planning.
        self._apply_production_payload(payload)
        self._initialize_production_target()
        self.loading = False
        yield DashboardState.load_sales_background

    @rx.event(background=True)
    async def load_initial_data_background(self):
        """Warm Inventory, Sales, and QA concurrently for the first user."""
        async with self:
            if self.initial_data_loading:
                return
            self.initial_data_loading = True
            self.loading = True
            self.sales_background_loading = True
            self.qa_loading = True
            self.qa_message = "Loading Quality & Compliance data..."

        inventory_task = asyncio.create_task(
            rx.run_in_thread(get_dashboard_data)
        )

        async def named_load(name: str, loader):
            try:
                return name, await rx.run_in_thread(loader), None
            except Exception as error:
                return name, None, error

        auxiliary_tasks = [
            asyncio.create_task(named_load("sales", get_sales_dashboard_data)),
            asyncio.create_task(named_load("qa", load_qa_module_data)),
        ]
        try:
            try:
                payload = await inventory_task
            except Exception as error:
                async with self:
                    payload = demo_dashboard_data()
                    self.using_demo_data = True
                    self.error_message = (
                        "Supabase Inventory could not be read. Demo data is displayed. "
                        f"Backend detail: {error}"
                    )
                    self._apply_payload(payload)
                    self.loading = False
            else:
                async with self:
                    self._apply_payload(payload)
                    self.using_demo_data = False
                    self.loading = False

            # The Inventory update above is released to the browser before
            # this second state lock. Prime only the two measured slow views
            # while Sales and QA continue loading in their own tasks.
            await asyncio.sleep(0)
            async with self:
                all_count, aging_bulk_count, warm_ms = (
                    self._prewarm_slowest_inventory_views()
                )
            print(
                "INVENTORY_PREWARM "
                f"All Inventory={all_count:,} rows | "
                f"Aging Risk Bulk={aging_bulk_count:,} rows | "
                f"{warm_ms:,.0f} ms",
                flush=True,
            )

            for completed in asyncio.as_completed(auxiliary_tasks):
                name, payload, error = await completed
                async with self:
                    if name == "sales":
                        if error is None and payload is not None:
                            self._apply_sales_payload(payload)
                        else:
                            self.error_message = (
                                "Sales history is temporarily unavailable. Inventory, "
                                f"Production, and QA remain available. Detail: {error}"
                            )
                        self.sales_background_loading = False
                    elif name == "qa":
                        if error is None and payload is not None:
                            self._apply_qa_payload(payload)
                            self.qa_message = "Quality & Compliance data is ready."
                            self.qa_error = ""
                        else:
                            self.qa_error = (
                                f"Quality & Compliance could not be loaded: {error}"
                            )
                            self.qa_message = ""
                        self.qa_loading = False
        finally:
            async with self:
                self.loading = False
                self.sales_background_loading = False
                self.qa_loading = False
                self.initial_data_loading = False

    def _apply_sales_payload(self, payload: dict[str, Any]) -> None:
        """Apply transfer-dependent data without resending Inventory state."""
        self.loaded_at = payload["loaded_at"]
        metrics = payload["metrics"]
        self.units_metric = f"{float(metrics['units']):,.0f}"
        self.value_metric = f"${float(metrics['value']):,.0f}"
        self.customers_metric = f"{int(metrics['customers']):,}"
        self.manifests_metric = f"{int(metrics.get('manifests', 0)):,}"
        self.weighted_price_metric = (
            f"${float(metrics.get('weighted_price', 0)):,.2f}"
        )
        self.stockouts_metric = f"{int(metrics['stockouts']):,}"
        self.open_manifests_metric = f"{int(metrics.get('open_manifests', 0)):,}"
        self.exception_manifests_metric = f"{int(metrics.get('exception_manifests', 0)):,}"
        self.exception_rows_metric = f"{int(metrics.get('exception_rows', 0)):,}"
        self.transfer_rows_metric = f"{int(metrics.get('transfer_rows', 0)):,}"
        self.latest_shipment = metrics.get("latest_shipment") or "—"
        self.brands = payload.get("brands", self.brands)
        self.strains = payload.get("strains", self.strains)
        self.sku_types = payload.get("sku_types", self.sku_types)
        # Keep the first Sales update compact. Large view-specific tables are
        # copied from the server cache only when that subtab is opened.
        self.business_pulse = payload.get("business_pulse", [])
        self.velocity_windows = payload.get(
            "velocity_windows", {"All Time": payload.get("velocity", [])}
        )
        self.availability_adjusted_velocity_windows = payload.get(
            "availability_adjusted_velocity_windows", {}
        )
        self.cultivation_clone_plan_demand_revision += 1
        self.velocity = self._selected_sku_velocity()
        self.availability_demand_summary = payload.get(
            "availability_demand_summary", []
        )
        self.availability_demand_weekly = payload.get(
            "availability_demand_weekly", []
        )
        self.retailer_locations = payload.get(
            "retailer_locations", self.retailer_locations
        )
        self._apply_optional_module_payload(payload)
        if payload.get("sales_error"):
            self.error_message = str(payload["sales_error"])

    @rx.event(background=True)
    async def load_sales_background(self):
        """Warm Sales history without blocking Inventory, QA, or navigation."""
        async with self:
            if self.sales_background_loading or not database_url():
                return
            self.sales_background_loading = True
        try:
            payload = await rx.run_in_thread(get_sales_dashboard_data)
            async with self:
                self._apply_sales_payload(payload)
                self.using_demo_data = False
        except Exception as error:
            async with self:
                self.error_message = (
                    "Sales history is temporarily unavailable. Inventory, "
                    f"Production, and QA remain available. Detail: {error}"
                )
        finally:
            async with self:
                self.sales_background_loading = False

    @rx.event(background=True)
    async def load_distribution_operations_background(self):
        """Load only the active Distribution & Customer Service view and page."""
        async with self:
            self.distribution_request_revision += 1
            request_revision = self.distribution_request_revision
            self.distribution_loading = True
            self.distribution_error = ""
            view = self.qa_view
            exception_state = self.selected_exception_state
            page = (
                self.exception_page if view == "exceptions"
                else self.transfer_page
            )
            page_size = (
                self.exception_page_size if view == "exceptions"
                else self.transfer_page_size
            )
            brand_filter = self.brand_filter
            strain_filter = self.strain_filter
            sku_filter = self.sku_filter
            search_text = self.search_text
        try:
            payload = await rx.run_in_thread(
                lambda: get_distribution_operations_data(
                    view,
                    exception_state=exception_state,
                    page=page,
                    page_size=page_size,
                    brand_filter=brand_filter,
                    strain_filter=strain_filter,
                    sku_filter=sku_filter,
                    search_text=search_text,
                )
            )
            async with self:
                if request_revision != self.distribution_request_revision:
                    return
                if view == "exceptions":
                    self.exceptions = payload.get("exceptions", [])
                    self.exception_packages = payload.get(
                        "exception_packages", []
                    )
                    self.exception_server_total = int(
                        payload.get("exception_total", 0) or 0
                    )
                    self.exception_server_manifests = int(
                        payload.get("exception_manifests", 0) or 0
                    )
                    self.exception_server_value = float(
                        payload.get("exception_value", 0) or 0
                    )
                    self.exception_page = int(
                        payload.get("exception_page", page) or page
                    )
                elif view == "transfers":
                    self._transfer_data = payload.get("transfer_data", [])
                    self.transfer_server_total = int(
                        payload.get("transfer_total", 0) or 0
                    )
                    self.transfer_page = int(
                        payload.get("transfer_page", page) or page
                    )
                    self.transfer_import_log = payload.get(
                        "transfer_import_log", []
                    )
                if view not in self.sales_loaded_views:
                    self.sales_loaded_views = [*self.sales_loaded_views, view]
        except Exception as error:
            async with self:
                if request_revision == self.distribution_request_revision:
                    self.distribution_error = (
                        "Distribution data could not be loaded: " + str(error)
                    )
        finally:
            async with self:
                if request_revision == self.distribution_request_revision:
                    self.distribution_loading = False

    @rx.event
    def refresh(self):
        if not self._require_active_session():
            return
        self.loading = True
        self.error_message = ""
        yield
        try:
            if database_url():
                payload = get_dashboard_data(force_refresh=True)
                self.using_demo_data = False
            else:
                payload = demo_dashboard_data()
                self.using_demo_data = True
                self.error_message = (
                    "Demo mode: add QCC_SUPABASE_DATABASE_URL to .env to "
                    "read the shared QCC database."
                )
            self.sales_loaded_views = []
            self._apply_payload(payload)
        except Exception as error:
            self.error_message = f"Refresh failed: {error}"
        self.loading = False
        yield DashboardState.load_sales_background

    def _apply_payload(self, payload: dict[str, Any]) -> None:
        self.loaded_at = payload["loaded_at"]
        self.rule_version = payload["rule_version"]
        metrics = payload["metrics"]
        snapshot = payload["snapshot"]
        self.units_metric = f"{float(metrics['units']):,.0f}"
        self.value_metric = f"${float(metrics['value']):,.0f}"
        self.customers_metric = f"{int(metrics['customers']):,}"
        self.manifests_metric = f"{int(metrics.get('manifests', 0)):,}"
        self.weighted_price_metric = (
            f"${float(metrics.get('weighted_price', 0)):,.2f}"
        )
        self.stockouts_metric = f"{int(metrics['stockouts']):,}"
        self.open_manifests_metric = f"{int(metrics.get('open_manifests', 0)):,}"
        self.exception_manifests_metric = f"{int(metrics.get('exception_manifests', 0)):,}"
        self.exception_rows_metric = f"{int(metrics.get('exception_rows', 0)):,}"
        self.transfer_rows_metric = f"{int(metrics.get('transfer_rows', 0)):,}"
        self.latest_shipment = metrics.get("latest_shipment") or "—"
        self.snapshot_date = snapshot.get("business_date") or "—"
        self.snapshot_packages = f"{int(snapshot.get('package_count', 0)):,}"
        self.snapshot_skus = f"{int(snapshot.get('sku_count', 0)):,}"
        self.snapshot_detail = f"{int(snapshot.get('detail_count', 0)):,}"
        self.snapshot_cpg_eligible = (
            f"{int(snapshot.get('authoritative_cpg_count', 0)):,}"
        )
        self.inventory_ready = bool(payload.get("inventory_ready", False))
        self.authoritative_cpg_ready = bool(
            payload.get("authoritative_cpg_ready", False)
        )
        self.brands = payload["brands"]
        self.strains = payload.get("strains", [])
        self.sku_types = payload.get("sku_types", [])
        self.monthly = payload["monthly"]
        self.top_skus = payload["top_skus"]
        self.business_pulse = payload.get("business_pulse", [])
        self.velocity_windows = payload.get(
            "velocity_windows", {"All Time": payload["velocity"]}
        )
        self.availability_adjusted_velocity_windows = payload.get(
            "availability_adjusted_velocity_windows", {}
        )
        self.velocity = self._selected_sku_velocity()
        self.stockouts = payload["stockouts"]
        self.saved_plans = payload["saved_plans"]
        self.saved_plan_cards = payload.get("saved_plan_cards", [])
        available_plan_ids = {
            str(row.get("Plan ID", "")) for row in self.saved_plan_cards
        }
        self.production_selected_plan_ids = [
            plan_id for plan_id in self.production_selected_plan_ids
            if plan_id in available_plan_ids
        ]
        self.production_templates = payload.get("production_templates", [])
        self.calendar = payload["calendar"]
        self.customers = payload.get("customers", [])
        self.exceptions = payload.get("exceptions", [])
        self.exception_packages = payload.get("exception_packages", [])
        self._transfer_data = payload.get("transfer_data", [])
        self.availability_demand_summary = payload.get(
            "availability_demand_summary", []
        )
        self.availability_demand_weekly = payload.get(
            "availability_demand_weekly", []
        )
        self.transfer_import_log = payload.get("transfer_import_log", [])
        self.cpg_inventory = payload.get("cpg_inventory", [])
        self.bulk_inventory = payload.get("bulk_inventory", [])
        self.wip_inventory = payload.get("wip_inventory", [])
        self.potential_wip_inventory = payload.get("potential_wip_inventory", [])
        self.aging_cpg = payload.get("aging_cpg", [])
        self.aging_bulk = payload.get("aging_bulk", [])
        self.all_inventory = payload.get("all_inventory", [])
        self.needs_review = payload.get("needs_review", [])
        self._apply_optional_module_payload(payload)
        self._set_initial_calendar_month()
        self._initialize_production_target()

    def _apply_optional_module_payload(self, payload: dict[str, Any]) -> None:
        """Hydrate one Sales view and retain it for fast session navigation."""
        if not self.sales_loaded_views:
            self.monthly = []
            self.top_skus = []
            self.stockouts = []
            self.saved_plans = []
            self.saved_plan_cards = []
            self.production_templates = []
            self.calendar = []
            self.customers = []
            self.retail_delivery_history = []
            self.exceptions = []
            self.exception_packages = []
            self._transfer_data = []
            self.transfer_import_log = []
            # Velocity windows are shared demand inputs for Clone Allocation,
            # not optional Sales-tab presentation data. Keep them hydrated
            # when Sales finishes loading while Cultivation is active.

        # The executive action queue and business pulse remain immediately
        # available. They are comparatively small and avoid a second load when
        # leadership returns to the landing page.
        self.business_pulse = payload.get("business_pulse", [])
        self.velocity = payload.get("velocity", [])

        optional_view = self.sales_demand_view
        if (
            self.workspace_view == "distribution"
            and self.distribution_view in {"customers", "retail", "transfers", "exceptions"}
        ):
            optional_view = self.distribution_view
        elif self.workspace_view != "sales_demand":
            return
        if optional_view in self.sales_loaded_views:
            return
        if optional_view == "overview":
            self.monthly = payload.get("monthly", [])
            self.top_skus = payload.get("top_skus", [])
        elif optional_view == "stockouts":
            self.stockouts = payload.get("stockouts", [])
        elif optional_view in {"planning", "production"}:
            self.velocity_windows = payload.get(
                "velocity_windows", {"All Time": self.velocity}
            )
            self.availability_adjusted_velocity_windows = payload.get(
                "availability_adjusted_velocity_windows", {}
            )
            self.velocity = self._selected_sku_velocity()
            if optional_view == "production":
                self.saved_plans = payload.get("saved_plans", [])
                self.saved_plan_cards = payload.get("saved_plan_cards", [])
                self.production_templates = payload.get(
                    "production_templates", []
                )
                self.calendar = payload.get("calendar", [])
                self.production_module_loaded = True
        elif optional_view == "customers":
            self.customers = payload.get("customers", [])
        elif optional_view == "retail":
            self.retail_delivery_history = payload.get(
                "retail_delivery_history", []
            )
            self.retailer_locations = payload.get("retailer_locations", [])
        elif optional_view == "exceptions":
            self.exceptions = payload.get("exceptions", [])
            self.exception_packages = payload.get("exception_packages", [])
        elif optional_view == "transfers":
            self._transfer_data = payload.get("transfer_data", [])
            self.transfer_import_log = payload.get("transfer_import_log", [])

        self.sales_loaded_views = [
            *self.sales_loaded_views, optional_view
        ]

        available_plan_ids = {
            str(row.get("Plan ID", "")) for row in self.saved_plan_cards
        }
        self.production_selected_plan_ids = [
            plan_id for plan_id in self.production_selected_plan_ids
            if plan_id in available_plan_ids
        ]

    def _set_initial_calendar_month(self) -> None:
        if self.calendar:
            first_target = self.calendar[0].get("Target Date", "")
            try:
                parsed = datetime.strptime(first_target, "%Y-%m-%d").date()
                self.calendar_year = parsed.year
                self.calendar_month = parsed.month
                self.calendar_focus_date = parsed.isoformat()
            except (TypeError, ValueError):
                pass
        self._rebuild_calendar()

    def _rebuild_calendar(self) -> None:
        try:
            focus = datetime.strptime(self.calendar_focus_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            focus = date(self.calendar_year, self.calendar_month, 1)
        self.calendar_year, self.calendar_month = focus.year, focus.month
        if self.calendar_view_mode == "Day":
            start, count = focus, 1
            self.calendar_title = focus.strftime("%A, %B %d, %Y")
        elif self.calendar_view_mode == "Week":
            start = focus - timedelta(days=(focus.weekday() + 1) % 7)
            count = 7
            self.calendar_title = (
                f"{start:%b %d} – {(start + timedelta(days=6)):%b %d, %Y}"
            )
        else:
            first = date(focus.year, focus.month, 1)
            start = first - timedelta(days=(first.weekday() + 1) % 7)
            count = 42
            self.calendar_title = f"{month_name[focus.month]} {focus.year}"
        self.calendar_days = []
        line_order = {line: index for index, line in enumerate(PRODUCTION_LINE_OPTIONS)}
        for offset in range(count):
            current = start + timedelta(days=offset)
            current_iso = current.isoformat()
            plans = [
                event for event in self.calendar
                if event.get("Target Date") == current_iso
            ]
            plans.sort(key=lambda event: (
                line_order.get(str(event.get("Production Line", "")), 99),
                str(event.get("Plan Name", "")),
            ))
            self.calendar_days.append({
                "Day": str(current.day),
                "Date": current_iso,
                "In Month": self.calendar_view_mode != "Month" or current.month == focus.month,
                "Plans": plans,
            })

    @rx.var(cache=True)
    def calendar_grid_columns(self) -> str:
        return "1" if self.calendar_view_mode == "Day" else "7"

    @rx.var(cache=True)
    def calendar_weekday_headers(self) -> list[str]:
        if self.calendar_view_mode == "Day":
            try:
                return [datetime.strptime(self.calendar_focus_date, "%Y-%m-%d").strftime("%A")]
            except (TypeError, ValueError):
                return ["Day"]
        return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    @rx.event
    def change_calendar_view_mode(self, value: str):
        if value in {"Month", "Week", "Day"}:
            self.calendar_view_mode = value
            self._rebuild_calendar()

    @rx.event
    def previous_calendar_month(self):
        focus = datetime.strptime(self.calendar_focus_date, "%Y-%m-%d").date()
        if self.calendar_view_mode == "Day":
            focus -= timedelta(days=1)
        elif self.calendar_view_mode == "Week":
            focus -= timedelta(days=7)
        else:
            focus = date(focus.year - (focus.month == 1), 12 if focus.month == 1 else focus.month - 1, 1)
        self.calendar_focus_date = focus.isoformat()
        self._rebuild_calendar()

    @rx.event
    def next_calendar_month(self):
        focus = datetime.strptime(self.calendar_focus_date, "%Y-%m-%d").date()
        if self.calendar_view_mode == "Day":
            focus += timedelta(days=1)
        elif self.calendar_view_mode == "Week":
            focus += timedelta(days=7)
        else:
            focus = date(focus.year + (focus.month == 12), 1 if focus.month == 12 else focus.month + 1, 1)
        self.calendar_focus_date = focus.isoformat()
        self._rebuild_calendar()

    def _production_catalog(self) -> list[tuple[str, str, str]]:
        combinations: set[tuple[str, str, str]] = set()
        for row in [*self.velocity, *self._inventory_view_rows("View CPG")]:
            key = (
                str(row.get("Brand", "") or "").strip(),
                str(row.get("Strain", "") or "").strip(),
                str(row.get("SKU Type", "") or "").strip(),
            )
            if all(key) and "needs review" not in key[0].lower():
                combinations.add(key)
        return sorted(combinations)

    def _configure_production_defaults(self) -> None:
        recipe = production_recipe_type(
            self.production_brand, self.production_sku
        )
        if recipe == "Craft Kings / Royal Smalls Flower":
            defaults = {
                "28g Flower": (55, 5, 10, 10, 5, 12, 3),
                "28g Flower Smalls": (55, 5, 10, 10, 5, 12, 3),
                "14g Flower": (10, 50, 10, 10, 5, 12, 3),
                "7g Flower": (5, 10, 50, 15, 5, 12, 3),
                "3.5g Flower": (5, 5, 15, 50, 10, 12, 3),
                "1g Flower": (5, 5, 15, 20, 40, 12, 3),
            }.get(self.production_sku, (10, 10, 20, 40, 5, 12, 3))
            (
                self.production_mix_28, self.production_mix_14,
                self.production_mix_7, self.production_mix_35,
                self.production_mix_1, self.production_mix_smalls,
                self.production_mix_loss,
            ) = map(float, defaults)
        elif recipe == "Flower Mix":
            defaults = {
                "3.5g Flower": (60.0, 20.0, 10.0),
                "7g Flower": (20.0, 60.0, 10.0),
                "1g Flower": (20.0, 10.0, 60.0),
            }.get(self.production_sku, (60.0, 20.0, 10.0))
            self.production_mix_28 = 0.0
            self.production_mix_14 = 0.0
            self.production_mix_35 = defaults[0]
            self.production_mix_7 = defaults[1]
            self.production_mix_1 = defaults[2]
            self.production_mix_smalls = 7.0
            self.production_mix_loss = 3.0
        self.production_unit_weight = max(
            production_unit_weight_grams(self.production_sku), 0.01
        )
        self.production_plan_name = (
            f"{self.production_strain} {self.production_sku} "
            f"{date.today():%b %d}"
        ).strip()
        self.production_scenario_name = (
            f"{self.production_strain} Mix".strip()
        )

    def _reset_production_builder(self, clear_scenarios: bool = True) -> None:
        self.production_selected_tags = []
        self.production_batch_weight = 0.0
        self.production_save_message = ""
        self.production_save_error = ""
        self.production_last_saved_plan_id = ""
        if clear_scenarios:
            self.production_scenarios = []
        self.production_material_filter = "All Eligible Materials"
        self.production_source_strain_filter = "All Source Strains"
        self.production_selected_source_strains = []
        self.production_source_location_filter = "All Source Locations"
        self.production_source_sort = "Oldest First"
        self.production_source_search = ""
        self.production_source_min_weight = 0.0
        self._configure_production_defaults()

    def _initialize_production_target(self) -> None:
        catalog = self._production_catalog()
        if not catalog:
            return
        brands = sorted({row[0] for row in catalog})
        if self.production_brand not in brands:
            self.production_brand = brands[0]
        strains = sorted({
            row[1] for row in catalog if row[0] == self.production_brand
        })
        if self.production_strain not in strains:
            self.production_strain = strains[0]
        skus = sorted({
            row[2] for row in catalog
            if row[0] == self.production_brand
            and row[1] == self.production_strain
        })
        if self.production_sku not in skus:
            self.production_sku = skus[0]
        self._configure_production_defaults()

    @rx.var(cache=True)
    def production_brand_options(self) -> list[str]:
        return sorted({row[0] for row in self._production_catalog()})

    @rx.var(cache=True)
    def production_strain_options(self) -> list[str]:
        all_strains = sorted({
            row[1] for row in self._production_catalog()
            if row[0] == self.production_brand
        })
        if not self.production_sku:
            return all_strains
        matching = sorted({
            row[1] for row in self._production_catalog()
            if row[0] == self.production_brand
            and row[2] == self.production_sku
        })
        return matching or all_strains

    @rx.var(cache=True)
    def production_sku_options(self) -> list[str]:
        return sorted({
            row[2] for row in self._production_catalog()
            if row[0] == self.production_brand
            and row[1] == self.production_strain
        })

    @rx.event
    def change_production_brand(self, value: str):
        self.production_brand = value
        brand_catalog = [
            row for row in self._production_catalog() if row[0] == value
        ]
        brand_skus = sorted({row[2] for row in brand_catalog})
        if self.production_sku not in brand_skus:
            self.production_sku = brand_skus[0] if brand_skus else ""
        strains = self.production_strain_options
        self.production_strain = strains[0] if strains else ""
        self._reset_production_builder()

    @rx.event
    def change_production_strain(self, value: str):
        self.production_strain = value
        self._reset_production_builder()

    @rx.event
    def change_production_sku(self, value: str):
        self.production_sku = value
        self._reset_production_builder()

    @rx.var(cache=True)
    def production_recipe(self) -> str:
        return production_recipe_type(self.production_brand, self.production_sku)

    def _potential_source_frame(self) -> pd.DataFrame:
        frame = pd.DataFrame(
            self._inventory_view_rows("View Potential WIP")
        )
        if self.production_edit_plan_id:
            selected_card = next((
                card for card in self.saved_plan_cards
                if card.get("Plan ID") == self.production_edit_plan_id
            ), None)
            committed = {
                str(row[0]): float(row[1] or 0)
                for row in (selected_card or {}).get("Sources", [])
            } if str((selected_card or {}).get("Status", "")) in {
                "Planned", "Committed", "In Production"
            } else {}
            physical = pd.DataFrame(self._inventory_view_rows("View WIP"))
            if committed and not physical.empty:
                physical = physical[
                    physical["Metrc Tag"].astype(str).isin(committed)
                ].copy()
                physical["Available Weight (g)"] = physical["Metrc Tag"].astype(
                    str
                ).map(committed).fillna(0)
                if frame.empty:
                    frame = physical
                else:
                    frame = frame.copy()
                    existing = frame["Metrc Tag"].astype(str)
                    frame["Available Weight (g)"] = pd.to_numeric(
                        frame["Available Weight (g)"], errors="coerce"
                    ).fillna(0) + existing.map(committed).fillna(0)
                    frame = pd.concat([
                        frame,
                        physical[
                            ~physical["Metrc Tag"].astype(str).isin(existing)
                        ],
                    ], ignore_index=True, sort=False)
        if frame.empty or not all([
            self.production_brand, self.production_strain, self.production_sku,
        ]):
            return pd.DataFrame()
        source = frame.rename(columns={
            "Brand": "brand", "Strain": "strain", "SKU Type": "sku_type",
            "Production Stage": "production_stage", "QA Status": "qa_status",
            "Category": "category", "Item": "item",
            "Metrc Tag": "package_tag", "Available Weight (g)": "available_weight_grams",
            "Calculated Weight (g)": "calculated_weight_grams",
            "Source Harvest": "source_harvest", "Location": "location",
            "Age": "inventory_age_days",
        })
        return potential_wip_for_sku(
            source, self.production_brand, self.production_strain,
            self.production_sku,
        )

    @rx.var(cache=True)
    def production_all_source_records(self) -> list[dict[str, Any]]:
        matches = self._potential_source_frame()
        if matches.empty:
            return []
        matches["available_weight_grams"] = pd.to_numeric(
            matches["available_weight_grams"], errors="coerce"
        ).fillna(0)
        matches["inventory_age_days"] = pd.to_numeric(
            matches.get("inventory_age_days"), errors="coerce"
        ).fillna(0)
        return [
            {
                "Metrc Tag": str(row.get("package_tag", "")),
                "Item": str(row.get("item", "") or ""),
                "Source Strain": str(row.get("strain", "") or ""),
                "WIP Component": str(row.get("wip_component", "Primary") or "Primary"),
                "Material Type": self._production_material_type(
                    str(row.get("item", "") or ""),
                    str(row.get("category", "") or ""),
                ),
                "Category": str(row.get("category", "") or ""),
                "Location": str(row.get("location", "") or ""),
                "QA Status": str(row.get("qa_status", "") or ""),
                "Age": round(float(row.get("inventory_age_days", 0) or 0), 1),
                "Available Weight (g)": round(
                    float(row.get("available_weight_grams", 0) or 0), 2
                ),
            }
            for row in matches.sort_values(
                ["inventory_age_days", "package_tag"], ascending=[False, True]
            ).to_dict("records")
        ]

    @staticmethod
    def _production_material_type(item: str, category: str) -> str:
        item_text = str(item or "").lower()
        category_text = str(category or "").lower()
        text = f"{item_text} {category_text}"
        if "trim" in item_text and "shake" not in item_text:
            return "Trim"
        if "shake" in item_text and "trim" not in item_text:
            return "Shake"
        if "trim" in text and "shake" not in text:
            return "Trim"
        if "shake" in text:
            return "Shake"
        if any(token in text for token in ["mids", "mid ", "smalls", "small "]):
            return "Mids / Smalls"
        if "bud/flower - bulk" in text or "bulk flower" in text or "flower bulk" in text:
            return "Bulk Flower"
        if "bulk" in text:
            return "Other Bulk"
        return "Other Eligible"

    @rx.var(cache=True)
    def production_material_options(self) -> list[str]:
        return [
            "All Eligible Materials",
            *sorted({row["Material Type"] for row in self.production_all_source_records}),
        ]

    @rx.var(cache=True)
    def production_source_strain_options(self) -> list[str]:
        return [
            "All Source Strains",
            *sorted({row["Source Strain"] for row in self.production_all_source_records
                     if row["Source Strain"]}),
        ]

    @rx.var(cache=True)
    def production_source_strain_values(self) -> list[str]:
        return sorted({
            row["Source Strain"] for row in self.production_all_source_records
            if row["Source Strain"]
        })

    @rx.var(cache=True)
    def production_multi_strain_enabled(self) -> bool:
        return (
            self.production_brand == "Craft Kings"
            and self.production_strain in {
                "Hybrid Blend", "Indica Blend", "Sativa Blend",
            }
            and "pre-roll" in self.production_sku.lower()
        )

    @rx.var(cache=True)
    def production_source_location_options(self) -> list[str]:
        return [
            "All Source Locations",
            *sorted({row["Location"] for row in self.production_all_source_records
                     if row["Location"]}),
        ]

    @rx.var(cache=True)
    def production_source_records(self) -> list[dict[str, Any]]:
        rows = list(self.production_all_source_records)
        if self.production_material_filter != "All Eligible Materials":
            rows = [
                row for row in rows
                if row["Material Type"] == self.production_material_filter
            ]
        if self.production_multi_strain_enabled and self.production_selected_source_strains:
            rows = [
                row for row in rows
                if row["Source Strain"] in self.production_selected_source_strains
            ]
        elif self.production_source_strain_filter != "All Source Strains":
            rows = [
                row for row in rows
                if row["Source Strain"] == self.production_source_strain_filter
            ]
        if self.production_source_location_filter != "All Source Locations":
            rows = [
                row for row in rows
                if row["Location"] == self.production_source_location_filter
            ]
        if self.production_source_min_weight > 0:
            rows = [
                row for row in rows
                if float(row["Available Weight (g)"]) >= self.production_source_min_weight
            ]
        search = self.production_source_search.strip().lower()
        if search:
            rows = [
                row for row in rows
                if search in " ".join(str(value) for value in row.values()).lower()
            ]
        if self.production_source_sort == "Largest Lot First":
            rows.sort(key=lambda row: (-float(row["Available Weight (g)"]), row["Metrc Tag"]))
        elif self.production_source_sort == "Smallest Lot First":
            rows.sort(key=lambda row: (float(row["Available Weight (g)"]), row["Metrc Tag"]))
        elif self.production_source_sort == "Newest First":
            rows.sort(key=lambda row: (float(row["Age"]), row["Metrc Tag"]))
        else:
            rows.sort(key=lambda row: (-float(row["Age"]), row["Metrc Tag"]))
        return rows

    @rx.var(cache=True)
    def production_source_rows(self) -> list[list[Any]]:
        return [[
            "Selected" if row["Metrc Tag"] in self.production_selected_tags else "",
            row["Metrc Tag"], row["Item"], row["WIP Component"],
            row["Category"], row["Location"], row["QA Status"], row["Age"],
            row["Available Weight (g)"],
        ] for row in self.production_source_records]

    @rx.var(cache=True)
    def production_source_count(self) -> str:
        return (
            f"{len(self.production_source_records):,} of "
            f"{len(self.production_all_source_records):,}"
        )

    @rx.var(cache=True)
    def production_source_weight(self) -> str:
        total = sum(
            float(row["Available Weight (g)"]) for row in self.production_source_records
        )
        return self._weight_label(total)

    @rx.var(cache=True)
    def production_selected_weight(self) -> float:
        selected = set(self.production_selected_tags)
        return round(sum(
            float(row["Available Weight (g)"])
            for row in self.production_all_source_records
            if row["Metrc Tag"] in selected
        ), 2)

    @rx.var(cache=True)
    def production_selected_weight_label(self) -> str:
        return self._weight_label(self.production_selected_weight)

    @rx.event
    def toggle_production_source(self, package_tag: str, selected: bool):
        tags = list(self.production_selected_tags)
        if selected and package_tag not in tags:
            tags.append(package_tag)
        elif not selected and package_tag in tags:
            tags.remove(package_tag)
        self.production_selected_tags = tags
        self.production_batch_weight = self.production_selected_weight
        self.production_save_message = ""
        self.production_save_error = ""

    @rx.event
    def select_all_production_sources(self):
        self.production_selected_tags = [
            row["Metrc Tag"] for row in self.production_source_records
        ]
        self.production_batch_weight = self.production_selected_weight

    @rx.event
    def clear_production_sources(self):
        self.production_selected_tags = []
        self.production_batch_weight = 0.0

    def _pending_source_commitments(self) -> dict[str, float]:
        """Mirror the database allocation order for an immediate UI update."""
        available_by_tag = {
            str(row.get("Metrc Tag", "")): float(
                row.get("Available Weight (g)", 0) or 0
            )
            for row in self.production_all_source_records
        }
        remaining = max(float(self.production_batch_weight or 0), 0.0)
        commitments: dict[str, float] = {}
        for package_tag in self.production_selected_tags:
            available = max(available_by_tag.get(str(package_tag), 0.0), 0.0)
            committed = min(available, remaining)
            if committed > 0:
                commitments[str(package_tag)] = committed
                remaining -= committed
            if remaining <= 0.001:
                break
        return commitments

    def _apply_local_source_commitments(
        self, commitments: dict[str, float]
    ) -> None:
        """Remove consumed source lots from the builder without a full reload."""
        if not commitments:
            return

        def updated_rows(
            rows: list[dict[str, Any]], *, update_view_flag: bool
        ) -> list[dict[str, Any]]:
            updated: list[dict[str, Any]] = []
            for source_row in rows:
                package_tag = str(source_row.get("Metrc Tag", ""))
                committed = commitments.get(package_tag)
                if committed is None:
                    updated.append(source_row)
                    continue
                row = dict(source_row)
                available = max(
                    float(row.get("Available Weight (g)", 0) or 0)
                    - committed,
                    0.0,
                )
                row["Available Weight (g)"] = round(available, 2)
                if update_view_flag and available <= 0.001:
                    row["View Potential WIP"] = False
                updated.append(row)
            return updated

        self.all_inventory = updated_rows(
            self.all_inventory, update_view_flag=True
        )
        self.potential_wip_inventory = updated_rows(
            self.potential_wip_inventory, update_view_flag=False
        )

    @rx.event
    def change_production_material_filter(self, value: str):
        self.production_material_filter = value

    @rx.event
    def change_production_source_strain_filter(self, value: str):
        self.production_source_strain_filter = value

    @rx.event
    def toggle_production_source_strain(self, value: str, selected: bool):
        strains = list(self.production_selected_source_strains)
        if selected and value not in strains:
            strains.append(value)
        elif not selected and value in strains:
            strains.remove(value)
        self.production_selected_source_strains = strains

    @rx.event
    def select_all_production_source_strains(self):
        self.production_selected_source_strains = list(
            self.production_source_strain_values
        )

    @rx.event
    def clear_production_source_strains(self):
        self.production_selected_source_strains = []

    @rx.event
    def change_production_source_location_filter(self, value: str):
        self.production_source_location_filter = value

    @rx.event
    def change_production_source_sort(self, value: str):
        self.production_source_sort = value

    @rx.event
    def change_production_source_search(self, value: str):
        self.production_source_search = value

    @rx.event
    def change_production_source_min_weight(self, value: str):
        self.production_source_min_weight = max(self._float_value(value), 0.0)

    @staticmethod
    def _float_value(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @rx.event
    def change_production_batch_weight(self, value: str):
        self.production_batch_weight = self._float_value(value)

    @rx.event
    def change_production_mix_28(self, value: str):
        self.production_mix_28 = self._float_value(value)

    @rx.event
    def change_production_mix_14(self, value: str):
        self.production_mix_14 = self._float_value(value)

    @rx.event
    def change_production_mix_7(self, value: str):
        self.production_mix_7 = self._float_value(value)

    @rx.event
    def change_production_mix_35(self, value: str):
        self.production_mix_35 = self._float_value(value)

    @rx.event
    def change_production_mix_1(self, value: str):
        self.production_mix_1 = self._float_value(value)

    @rx.event
    def change_production_mix_smalls(self, value: str):
        self.production_mix_smalls = self._float_value(value)

    @rx.event
    def change_production_mix_loss(self, value: str):
        self.production_mix_loss = self._float_value(value)

    @rx.event
    def change_production_unit_weight(self, value: str):
        self.production_unit_weight = self._float_value(value)

    @rx.event
    def change_production_overfill(self, value: str):
        self.production_overfill_percent = self._float_value(value)

    @rx.event
    def change_production_process_loss(self, value: str):
        self.production_process_loss_percent = self._float_value(value)

    @rx.event
    def change_production_qa_retention(self, value: str):
        self.production_qa_retention_grams = self._float_value(value)

    @rx.event
    def change_production_gummy_weight(self, value: str):
        self.production_gummy_piece_weight = self._float_value(value)

    @rx.event
    def change_production_gummy_count(self, value: str):
        self.production_gummies_per_package = max(int(self._float_value(value, 1)), 1)

    @rx.event
    def change_production_plan_name(self, value: str):
        self.production_plan_name = value

    @rx.event
    def change_production_target_date(self, value: str):
        self.production_target_date = value

    @rx.event
    def change_production_plan_status(self, value: str):
        self.production_plan_status = value

    @rx.event
    def change_production_plan_notes(self, value: str):
        self.production_plan_notes = value

    @rx.event
    def change_production_assigned_department(self, value: str):
        self.production_assigned_department = value

    @rx.event
    def change_production_line(self, value: str):
        if value in PRODUCTION_LINE_OPTIONS:
            self.production_line = value

    @rx.event
    def change_production_scenario_name(self, value: str):
        self.production_scenario_name = value

    @rx.event
    def change_output_brand_28(self, value: str):
        self.production_output_brand_28 = value

    @rx.event
    def change_output_brand_14(self, value: str):
        self.production_output_brand_14 = value

    @rx.event
    def change_output_brand_7(self, value: str):
        self.production_output_brand_7 = value

    @rx.event
    def change_output_brand_35(self, value: str):
        self.production_output_brand_35 = value

    @rx.event
    def change_output_brand_1(self, value: str):
        self.production_output_brand_1 = value

    @rx.var(cache=True)
    def production_mix_result(self) -> dict[str, Any]:
        return calculate_flower_batch_mix(self.production_batch_weight, {
            "flower_28": self.production_mix_28,
            "flower_14": self.production_mix_14,
            "flower_7": self.production_mix_7,
            "flower_35": self.production_mix_35,
            "flower_1": self.production_mix_1,
            "smalls": self.production_mix_smalls,
            "loss": self.production_mix_loss,
        })

    @rx.var(cache=True)
    def production_mix_total(self) -> str:
        return f"{float(self.production_mix_result['percentage_total']):.1f}%"

    @rx.var(cache=True)
    def production_mix_valid(self) -> bool:
        return abs(float(self.production_mix_result["percentage_total"]) - 100) <= 0.001

    @rx.var(cache=True)
    def production_output_records(self) -> list[dict[str, Any]]:
        if self.production_recipe in {
            "Flower Mix", "Craft Kings / Royal Smalls Flower",
        }:
            result = self.production_mix_result
            craft = self.production_recipe == "Craft Kings / Royal Smalls Flower"
            config = [
                ("28g Flower", "flower_28", self.production_output_brand_28, 28.0),
                ("14g Flower", "flower_14", self.production_output_brand_14, 14.0),
                ("7g Flower", "flower_7", self.production_output_brand_7, 7.0),
                ("3.5g Flower", "flower_35", self.production_output_brand_35, 3.5),
                ("1g Flower", "flower_1", self.production_output_brand_1, 1.0),
            ]
            if not craft:
                config = [
                    (sku, key, self.production_brand, weight)
                    for sku, key, _brand, weight in config[2:]
                ]
            return [{
                "brand": brand,
                "strain": self.production_strain,
                "sku_type": sku,
                "allocation_percent": float(result["percentages"][key]),
                "allocated_weight_grams": float(result["allocated_grams"][key]),
                "projected_units": int(result["projected_units"][key]),
                "unit_weight_grams": weight,
            } for sku, key, brand, weight in config
            if float(result["allocated_grams"][key]) > 0]
        if self.production_recipe in {"Unsupported", "Infused Pre-Rolls"}:
            return []
        unit_weight = self.production_unit_weight
        if self.production_recipe == "Craft Kings Gummies":
            unit_weight = (
                self.production_gummy_piece_weight
                * self.production_gummies_per_package
            )
        result = calculate_single_output_yield(
            self.production_batch_weight, unit_weight,
            self.production_process_loss_percent,
            self.production_overfill_percent,
            self.production_qa_retention_grams,
        )
        return [{
            "brand": self.production_brand,
            "strain": self.production_strain,
            "sku_type": self.production_sku,
            "allocation_percent": (
                float(result["packageable_weight_grams"])
                / self.production_batch_weight * 100
                if self.production_batch_weight else 0
            ),
            "allocated_weight_grams": float(result["packageable_weight_grams"]),
            "projected_units": int(result["projected_units"]),
            "unit_weight_grams": float(result["planned_unit_weight_grams"]),
        }]

    @rx.var(cache=True)
    def production_output_rows(self) -> list[list[Any]]:
        return [[
            output["brand"], output["strain"], output["sku_type"],
            round(float(output["allocation_percent"]), 2),
            round(float(output["allocated_weight_grams"]), 1),
            int(output["projected_units"]),
        ] for output in self.production_output_records]

    @rx.var(cache=True)
    def production_projected_units(self) -> str:
        return f"{sum(int(row['projected_units']) for row in self.production_output_records):,}"

    @rx.var(cache=True)
    def production_save_enabled(self) -> bool:
        return bool(
            self.production_plan_name.strip()
            and self.production_selected_tags
            and self.production_batch_weight > 0
            and self.production_batch_weight <= self.production_selected_weight + 0.001
            and self.production_output_records
            and (
                self.production_mix_valid
                if self.production_recipe in {
                    "Flower Mix", "Craft Kings / Royal Smalls Flower",
                } else True
            )
        )

    @rx.event
    def add_production_scenario(self):
        if not self.production_output_records:
            return
        units = {
            row["sku_type"]: int(row["projected_units"])
            for row in self.production_output_records
        }
        self.production_scenarios = [*self.production_scenarios, [
            self.production_scenario_name.strip() or "Untitled Scenario",
            round(self.production_batch_weight, 1),
            units.get("28g Flower", 0), units.get("14g Flower", 0),
            units.get("7g Flower", 0), units.get("3.5g Flower", 0),
            units.get("1g Flower", 0), sum(units.values()),
        ]]

    @rx.event
    def clear_production_scenarios(self):
        self.production_scenarios = []

    def _saved_plan_card(self, plan_id: str) -> dict[str, Any] | None:
        return next((
            card for card in self.saved_plan_cards
            if card.get("Plan ID") == str(plan_id)
        ), None)

    def _apply_saved_mix(
        self,
        outputs: list[list[Any]] | list[dict[str, Any]],
        smalls_percent: float,
        loss_percent: float,
    ) -> None:
        self.production_mix_28 = 0.0
        self.production_mix_14 = 0.0
        self.production_mix_7 = 0.0
        self.production_mix_35 = 0.0
        self.production_mix_1 = 0.0
        for output in outputs:
            if isinstance(output, dict):
                sku = str(output.get("sku_type", "") or "")
                percent = self._float_value(output.get("allocation_percent"))
                brand = str(output.get("brand", "") or "")
                unit_weight = self._float_value(output.get("unit_weight_grams"))
            else:
                sku = str(output[2] if len(output) > 2 else "")
                percent = self._float_value(output[3] if len(output) > 3 else 0)
                brand = str(output[0] if output else "")
                unit_weight = 0.0
            if sku == "28g Flower":
                self.production_mix_28 = percent
                self.production_output_brand_28 = brand or self.production_output_brand_28
            elif sku == "14g Flower":
                self.production_mix_14 = percent
                self.production_output_brand_14 = brand or self.production_output_brand_14
            elif sku == "7g Flower":
                self.production_mix_7 = percent
                self.production_output_brand_7 = brand or self.production_output_brand_7
            elif sku == "3.5g Flower":
                self.production_mix_35 = percent
                self.production_output_brand_35 = brand or self.production_output_brand_35
            elif sku == "1g Flower":
                self.production_mix_1 = percent
                self.production_output_brand_1 = brand or self.production_output_brand_1
            elif unit_weight > 0:
                self.production_unit_weight = unit_weight
        self.production_mix_smalls = self._float_value(smalls_percent)
        self.production_mix_loss = self._float_value(loss_percent)

    def _load_plan_into_builder(self, plan_id: str, editing: bool) -> bool:
        card = self._saved_plan_card(plan_id)
        if not card:
            self.production_action_error = "The selected production plan was not found."
            return False
        self.production_brand = str(card.get("Target Brand", "") or "")
        self.production_strain = str(card.get("Target Strain", "") or "")
        self.production_sku = str(card.get("Target SKU Type", "") or "")
        self._reset_production_builder()
        self._apply_saved_mix(
            card.get("Outputs", []),
            self._float_value(card.get("Smalls/Shake %")),
            self._float_value(card.get("Process Loss %")),
        )
        self.production_overfill_percent = self._float_value(card.get("Overfill %"))
        self.production_process_loss_percent = self._float_value(
            card.get("Process Loss %")
        )
        self.production_qa_retention_grams = self._float_value(
            card.get("QA Retention (g)")
        )
        saved_unit_weight = self._float_value(card.get("Unit Fill Weight (g)"))
        if saved_unit_weight > 0:
            self.production_unit_weight = saved_unit_weight
        try:
            formulation = json.loads(
                str(card.get("Formulation Details", "{}") or "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            formulation = {}
        self.production_gummy_piece_weight = self._float_value(
            formulation.get("gummy_piece_weight_grams"),
            self.production_gummy_piece_weight,
        )
        self.production_gummies_per_package = max(
            int(self._float_value(
                formulation.get("gummies_per_package"),
                self.production_gummies_per_package,
            )),
            1,
        )
        self.production_plan_name = str(card.get("Plan Name", "") or "")
        self.production_target_date = str(card.get("Target Date", "") or "")
        self.production_plan_status = str(card.get("Status", "Planned") or "Planned")
        self.production_plan_notes = str(card.get("Notes", "") or "")
        self.production_assigned_department = str(
            card.get("Department", "Production") or "Production"
        )
        self.production_line = str(
            card.get("Production Line", "Flower Line 1") or "Flower Line 1"
        )
        self.production_edit_plan_id = str(plan_id) if editing else ""
        if editing:
            self.production_selected_tags = [
                str(row[0]) for row in card.get("Sources", []) if row
            ]
            self.production_batch_weight = self._float_value(
                card.get("Batch Weight (g)")
            )
        else:
            self.production_selected_tags = []
            self.production_batch_weight = 0.0
            self.production_plan_name = "Copy of " + self.production_plan_name
        self.production_scenarios = []
        self.production_view = "build"
        self.production_action_message = (
            "Plan loaded for editing." if editing
            else "Plan duplicated. Select current source lots before saving."
        )
        self.production_action_error = ""
        return True

    @rx.event
    def start_edit_production_plan(self, plan_id: str):
        self._load_plan_into_builder(plan_id, True)

    @rx.event
    def duplicate_production_plan(self, plan_id: str):
        self._load_plan_into_builder(plan_id, False)

    @rx.event
    def cancel_production_plan_edit(self):
        self.production_edit_plan_id = ""
        self._reset_production_builder()
        self.production_action_message = "Plan amendment cancelled."

    def _remove_deleted_production_plans(self, plan_ids: list[str]) -> None:
        """Remove confirmed deletes immediately without rebuilding the app."""
        deleted = {str(plan_id) for plan_id in plan_ids}
        self.saved_plan_cards = [
            row for row in self.saved_plan_cards
            if str(row.get("Plan ID", "")) not in deleted
        ]
        self.saved_plans = [
            row for row in self.saved_plans
            if str(row.get("Plan ID", "")) not in deleted
        ]
        self.calendar = [
            row for row in self.calendar
            if str(row.get("Plan ID", "")) not in deleted
        ]
        self.production_selected_plan_ids = [
            plan_id for plan_id in self.production_selected_plan_ids
            if plan_id not in deleted
        ]
        self._rebuild_calendar()

    @rx.event
    def delete_production_plan(self, plan_id: str):
        if not self._require_active_session():
            return
        self.production_action_error = ""
        self.production_action_message = f"Deleting production plan {plan_id}..."
        self.loading = True
        yield
        try:
            deleted_ids = delete_reflex_production_plans([plan_id])
            if self.production_edit_plan_id == plan_id:
                self.production_edit_plan_id = ""
                self._reset_production_builder()
            self._remove_deleted_production_plans(deleted_ids)
            self.production_action_message = (
                f"Production plan deleted and its WIP released: {plan_id}"
            )
        except Exception as error:
            self.production_action_error = f"Plan could not be deleted: {error}"
        self.loading = False
        if not self.production_action_error:
            yield DashboardState.refresh_production_data_background

    @rx.event
    def toggle_saved_plan_selection(self, plan_id: str, selected: bool):
        plan_id = str(plan_id or "")
        current = list(self.production_selected_plan_ids)
        if selected and plan_id and plan_id not in current:
            current.append(plan_id)
        elif not selected and plan_id in current:
            current.remove(plan_id)
        self.production_selected_plan_ids = current

    @rx.event
    def select_all_filtered_production_plans(self):
        self.production_selected_plan_ids = [
            str(row.get("Plan ID", ""))
            for row in self.filtered_saved_plan_cards
            if str(row.get("Plan ID", ""))
        ]

    @rx.event
    def clear_production_plan_selection(self):
        self.production_selected_plan_ids = []

    @rx.event
    def delete_selected_production_plans(self):
        if not self._require_active_session():
            return
        plan_ids = list(self.production_selected_plan_ids)
        if not plan_ids:
            self.production_action_error = "Select at least one production plan."
            return
        self.production_action_error = ""
        self.production_action_message = (
            f"Deleting {len(plan_ids):,} selected production plan(s)..."
        )
        self.loading = True
        yield
        try:
            deleted_ids = delete_reflex_production_plans(plan_ids)
            if self.production_edit_plan_id in deleted_ids:
                self.production_edit_plan_id = ""
                self._reset_production_builder()
            self._remove_deleted_production_plans(deleted_ids)
            self.production_selected_plan_ids = []
            self.production_action_message = (
                f"Deleted {len(deleted_ids):,} production plan(s) and released their WIP."
            )
        except Exception as error:
            self.production_action_error = f"Plans could not be deleted: {error}"
        self.loading = False
        if not self.production_action_error:
            yield DashboardState.refresh_production_data_background

    @rx.event
    def create_template_from_plan(self, plan_id: str):
        if not self._require_active_session():
            return
        card = self._saved_plan_card(plan_id)
        if not card:
            self.production_action_error = "The selected production plan was not found."
            return
        self.loading = True
        yield
        try:
            template_id = create_reflex_production_template(
                plan_id,
                f"{card.get('Plan Name', 'Production Plan')} Template",
            )
            self._apply_payload(get_dashboard_data(force_refresh=True))
            self.production_action_message = f"Reusable template created: {template_id}"
            self.production_action_error = ""
        except Exception as error:
            self.production_action_error = f"Template could not be created: {error}"
        self.loading = False

    @rx.var(cache=True)
    def production_template_options(self) -> list[str]:
        return ["No Template", *[
            f"{row.get('template_name', 'Template')} · {row.get('template_id', '')}"
            for row in self.production_templates
        ]]

    @rx.var(cache=True)
    def production_status_options(self) -> list[str]:
        return (
            PRODUCTION_PLAN_STATUSES
            if self.production_edit_plan_id
            else ["Planned", "Committed"]
        )

    @rx.event
    def apply_production_template(self, choice: str):
        self.production_template_choice = choice
        if choice == "No Template":
            return
        template_id = choice.rsplit(" · ", 1)[-1]
        template = next((
            row for row in self.production_templates
            if str(row.get("template_id", "")) == template_id
        ), None)
        if not template:
            self.production_action_error = "The selected template was not found."
            return
        try:
            details = json.loads(str(template.get("template_details", "{}") or "{}"))
            outputs = json.loads(str(template.get("outputs_json", "[]") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            self.production_action_error = "The saved template details are invalid."
            return
        self.production_brand = str(template.get("target_brand", "") or "")
        self.production_strain = str(template.get("strain", "") or "")
        self.production_sku = str(template.get("target_sku_type", "") or "")
        self._reset_production_builder()
        self._apply_saved_mix(
            outputs,
            self._float_value(details.get("smalls_shake_percent")),
            self._float_value(details.get("loss_percent")),
        )
        self.production_process_loss_percent = self._float_value(
            details.get("process_loss_percent")
        )
        self.production_overfill_percent = self._float_value(
            details.get("overfill_percent")
        )
        self.production_qa_retention_grams = self._float_value(
            details.get("qa_retention_grams")
        )
        self.production_assigned_department = str(
            details.get("assigned_department", "Production") or "Production"
        )
        nested_formulation = details.get("formulation_details", "{}")
        try:
            nested_formulation = json.loads(
                nested_formulation
                if isinstance(nested_formulation, str)
                else json.dumps(nested_formulation)
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            nested_formulation = {}
        self.production_gummy_piece_weight = self._float_value(
            nested_formulation.get("gummy_piece_weight_grams"),
            self.production_gummy_piece_weight,
        )
        self.production_gummies_per_package = max(
            int(self._float_value(
                nested_formulation.get("gummies_per_package"),
                self.production_gummies_per_package,
            )),
            1,
        )
        saved_weight = self._float_value(details.get("unit_fill_weight_grams"))
        if saved_weight > 0:
            self.production_unit_weight = saved_weight
        self.production_edit_plan_id = ""
        self.production_selected_tags = []
        self.production_batch_weight = 0.0
        self.production_plan_name = (
            f"{self.production_strain} {self.production_sku} {date.today():%b %d}"
        ).strip()
        self.production_action_message = (
            "Template applied. Select current WIP lots and set the batch weight."
        )
        self.production_action_error = ""

    @rx.event
    def save_production_plan(self):
        if self.production_saving:
            return
        self.production_save_error = ""
        self.production_save_message = ""
        self.production_last_saved_plan_id = ""
        if not self.production_save_enabled:
            self.production_save_error = (
                "Complete the source selection and valid formulation before saving."
            )
            return
        self.production_saving = True
        self.production_action_message = "Saving production plan to Supabase..."
        # Push the visible progress state before beginning the database write.
        yield
        if not self._require_active_session():
            self.production_save_error = "Your session expired. Sign in again."
            self.production_saving = False
            return
        yield rx.toast.loading(
            "Saving production plan to Supabase...",
            id="qcc-production-save",
            duration=20000,
        )
        try:
            pending_commitments = self._pending_source_commitments()
            plan_id = create_reflex_production_plan(
                plan_name=self.production_plan_name,
                output_brand=self.production_brand,
                strain=self.production_strain,
                target_sku_type=self.production_sku,
                recipe_type=self.production_recipe,
                target_packaging_date=self.production_target_date,
                status=self.production_plan_status,
                batch_weight_grams=self.production_batch_weight,
                selected_tags=self.production_selected_tags,
                outputs=self.production_output_records,
                process_loss_percent=(
                    self.production_mix_loss
                    if self.production_recipe in {
                        "Flower Mix", "Craft Kings / Royal Smalls Flower",
                    } else self.production_process_loss_percent
                ),
                overfill_percent=self.production_overfill_percent,
                qa_retention_grams=self.production_qa_retention_grams,
                formulation_details={
                    "calculation": (
                        "flower_mix" if "Flower" in self.production_recipe
                        else "single_output_weight_yield"
                    ),
                    "smalls_shake_percent": self.production_mix_smalls,
                    "gummy_piece_weight_grams": self.production_gummy_piece_weight,
                    "gummies_per_package": self.production_gummies_per_package,
                },
                notes=self.production_plan_notes,
                plan_id=self.production_edit_plan_id,
                assigned_department=self.production_assigned_department,
                production_line=self.production_line,
            )
            edited = bool(self.production_edit_plan_id)
            if not edited:
                self._apply_local_source_commitments(pending_commitments)
            self.production_edit_plan_id = ""
            self.production_selected_tags = []
            self.production_batch_weight = 0.0
            self.production_save_message = (
                f"Production plan {'updated' if edited else 'saved'} successfully: {plan_id}"
            )
            self.production_last_saved_plan_id = plan_id
            self.production_action_message = self.production_save_message
            self.production_action_error = ""
            self.sales_demand_view = "production"
            self.production_view = "build"
            yield rx.toast.success(
                self.production_save_message,
                id="qcc-production-save",
                duration=7000,
            )
        except Exception as error:
            self.production_save_error = f"Production plan could not be saved: {error}"
            self.production_action_error = self.production_save_error
            yield rx.toast.error(
                self.production_save_error,
                id="qcc-production-save",
                duration=9000,
            )
        finally:
            self.production_saving = False
        if self.production_last_saved_plan_id:
            yield DashboardState.refresh_production_data_background

    @rx.event
    def view_saved_production_plan(self):
        self.production_view = "saved"

    @rx.event
    def build_another_production_plan(self):
        self.production_edit_plan_id = ""
        self._reset_production_builder()
        self.production_view = "build"
        self.production_action_message = "Ready to build another production plan."
        self.production_action_error = ""

    @rx.event
    def change_production_view(self, value: str):
        self.production_view = value
        if value == "calendar":
            self._set_initial_calendar_month()

    def _apply_production_payload(self, production: dict[str, Any]) -> None:
        self.saved_plans = production.get("saved_plans", [])
        self.saved_plan_cards = production.get("saved_plan_cards", [])
        self.production_templates = production.get("production_templates", [])
        self.calendar = production.get("calendar", [])
        self.production_module_loaded = True
        self._set_initial_calendar_month()

    @rx.event(background=True)
    async def load_production_data_background(self):
        """Show saved plans and calendar without waiting for Sales history."""
        async with self:
            if self.production_data_loading or self.production_module_loaded:
                return
            self.production_data_loading = True
        try:
            production = await rx.run_in_thread(load_production_module_data)
            async with self:
                self._apply_production_payload(production)
                self.production_action_error = ""
        except Exception as error:
            async with self:
                self.production_action_error = (
                    f"Production plans could not be loaded: {error}"
                )
        finally:
            async with self:
                self.production_data_loading = False

    @rx.event(background=True)
    async def refresh_production_data_background(self):
        """Refresh saved plans without holding up Production tab navigation."""
        async with self:
            if self.production_data_loading:
                return
            self.production_data_loading = True
        try:
            production, sales = await asyncio.gather(
                rx.run_in_thread(load_production_module_data),
                rx.run_in_thread(
                    lambda: get_sales_dashboard_data(force_refresh=True)
                ),
            )
            async with self:
                self._apply_production_payload(production)
                # A saved plan changes committed and available WIP. Refresh the
                # cached Sales planning rows once, without reloading inventory.
                self._apply_sales_payload(sales)
        except Exception as error:
            async with self:
                self.production_action_error = (
                    f"Saved production plans could not be refreshed: {error}"
                )
        finally:
            async with self:
                self.production_data_loading = False

    @rx.event
    def download_production_calendar(self):
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//QCC//Production Planning//EN",
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        ]
        for event in self.filtered_calendar:
            raw_date = str(event.get("Target Date", ""))
            event_date = raw_date.replace("-", "")
            if not event_date:
                continue
            try:
                end_date = (
                    date.fromisoformat(raw_date) + timedelta(days=1)
                ).strftime("%Y%m%d")
            except ValueError:
                end_date = event_date
            line = str(event.get("Production Line", "Unassigned"))
            summary = f"{line}: {event.get('Plan Name', 'Production Plan')}".replace(",", "\\,")
            description = str(event.get("Output Summary", "")).replace(",", "\\,")
            lines.extend([
                "BEGIN:VEVENT",
                f"UID:{event.get('Plan ID', '')}@qcc-control-tower",
                f"DTSTART;VALUE=DATE:{event_date}",
                f"DTEND;VALUE=DATE:{end_date}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{description}",
                "END:VEVENT",
            ])
        lines.append("END:VCALENDAR")
        return rx.download(
            data="\r\n".join(lines).encode("utf-8"),
            filename=f"qcc_production_calendar_{date.today().isoformat()}.ics",
        )

    def _compatible_brand(self, row: dict[str, Any]) -> str:
        return compatible_inventory_brand(row)

    def _filter_brand_value(self, row: dict[str, Any]) -> str:
        stage = str(row.get("Production Stage", "") or "").strip()
        if stage in UNFINISHED_INVENTORY_STAGES:
            return self._compatible_brand(row)
        return str(row.get("Brand", "") or "").strip()

    def _matches(self, row: dict[str, Any]) -> bool:
        if self.brand_filter != "All Brands" and self.brand_filter not in [
            value.strip()
            for value in self._filter_brand_value(row).split(",")
        ]:
            return False
        strain = (
            "" if self.strain_filter == "All Strains"
            else self.strain_filter.strip().lower()
        )
        if (
            strain
            and row.get("Strain") is not None
            and strain not in str(row.get("Strain", "")).lower()
        ):
            return False
        if (
            self.sku_filter != "All SKU Types"
            and row.get("SKU Type") is not None
            and self.sku_filter not in [
                value.strip()
                for value in str(row.get("SKU Type", "")).split(",")
            ]
        ):
            return False
        search = self.search_text.strip().lower()
        if not search:
            return True
        return search in " ".join(str(value or "") for value in row.values()).lower()

    @rx.var(cache=True)
    def brand_options(self) -> list[str]:
        return ["All Brands", *self.brands]

    @rx.var(cache=True)
    def sku_options(self) -> list[str]:
        return ["All SKU Types", *self.sku_types]

    @rx.var(cache=True)
    def strain_options(self) -> list[str]:
        return ["All Strains", *self.strains]

    @rx.var(cache=True)
    def filtered_top_skus(self) -> list[dict[str, Any]]:
        return [row for row in self.top_skus if self._matches(row)]

    @rx.var(cache=True)
    def filtered_velocity(self) -> list[dict[str, Any]]:
        return [
            row for row in self.velocity
            if self._matches(row) and self._matches_lifecycle(row)
        ]

    def _matches_lifecycle(self, row: dict[str, Any]) -> bool:
        status = str(row.get("Lifecycle Status", "Active") or "Active")
        if self.demand_lifecycle_filter == "Active Products Only":
            return status == "Active"
        if self.demand_lifecycle_filter == "Active + Dormant":
            return status in {"Active", "Dormant"}
        if self.demand_lifecycle_filter == "Include Retirement Candidates":
            return status in {"Active", "Dormant", "Retirement Candidate"}
        if self.demand_lifecycle_filter == "White Label Products":
            return status == "White Label"
        return True

    @rx.var(cache=True)
    def filtered_velocity_count(self) -> str:
        return f"{len(self.filtered_velocity):,} SKU combinations"

    @rx.var(cache=True)
    def sku_planning_sorted_records(self) -> list[dict[str, Any]]:
        rows = list(self.filtered_velocity)
        if self.sku_planning_sort == "Current Units - Low to High":
            rows.sort(key=lambda row: float(row.get("Current Units", 0) or 0))
        elif self.sku_planning_sort == "Weeks of Supply - Low to High":
            rows.sort(key=lambda row: float(row.get("Weeks of Supply", 0) or 0))
        elif self.sku_planning_sort == "Units Shipped - High to Low":
            rows.sort(
                key=lambda row: -float(row.get("Units Shipped", 0) or 0)
            )
        elif self.sku_planning_sort == "Brand / Strain / SKU":
            rows.sort(key=lambda row: (
                str(row.get("Brand", "")), str(row.get("Strain", "")),
                str(row.get("SKU Type", "")),
            ))
        else:
            rows.sort(
                key=lambda row: -float(row.get("Avg Weekly Units", 0) or 0)
            )
        return rows

    @rx.var(cache=True)
    def sku_planning_total_pages(self) -> int:
        count = len(self.sku_planning_sorted_records)
        return max((count + self.sku_planning_page_size - 1) // self.sku_planning_page_size, 1)

    @rx.var(cache=True)
    def sku_planning_page_records(self) -> list[dict[str, Any]]:
        page = min(max(self.sku_planning_page, 1), self.sku_planning_total_pages)
        start = (page - 1) * self.sku_planning_page_size
        return self.sku_planning_sorted_records[
            start:start + self.sku_planning_page_size
        ]

    @rx.var(cache=True)
    def sku_planning_page_label(self) -> str:
        page = min(max(self.sku_planning_page, 1), self.sku_planning_total_pages)
        count = len(self.sku_planning_sorted_records)
        if not count:
            return "No matching rows"
        start = (page - 1) * self.sku_planning_page_size + 1
        end = min(page * self.sku_planning_page_size, count)
        return (
            f"Rows {start:,}-{end:,} of {count:,} · "
            f"Page {page:,} of {self.sku_planning_total_pages:,}"
        )

    @rx.var(cache=True)
    def sku_planning_page_size_value(self) -> str:
        return str(self.sku_planning_page_size)

    @rx.event
    def change_sku_planning_sort(self, value: str):
        self.sku_planning_sort = value
        self.sku_planning_page = 1

    @rx.event
    def change_sku_planning_page_size(self, value: str):
        try:
            self.sku_planning_page_size = int(value)
        except (TypeError, ValueError):
            self.sku_planning_page_size = 10
        self.sku_planning_page = 1

    def _selected_sku_velocity(self) -> list[dict[str, Any]]:
        windows = (
            self.availability_adjusted_velocity_windows
            if self.sku_use_availability_adjusted
            else self.velocity_windows
        )
        return windows.get(
            self.sku_velocity_period,
            self.velocity_windows.get(self.sku_velocity_period, self.velocity),
        )

    @rx.event
    def change_sku_velocity_period(self, value: str):
        self.sku_velocity_period = value
        self.velocity = self._selected_sku_velocity()
        self.sku_planning_page = 1

    @rx.event
    def change_sku_availability_adjusted(self, value: bool):
        self.sku_use_availability_adjusted = bool(value)
        self.velocity = self._selected_sku_velocity()
        self.sku_planning_page = 1

    @rx.event
    def change_demand_lifecycle_filter(self, value: str):
        self.demand_lifecycle_filter = value
        self.sku_planning_page = 1

    @rx.event
    def change_saved_plan_search(self, value: str):
        self.saved_plan_search = value

    @rx.event
    def change_saved_plan_status_filter(self, value: str):
        self.saved_plan_status_filter = value

    @rx.event
    def previous_sku_planning_page(self):
        self.sku_planning_page = max(self.sku_planning_page - 1, 1)

    @rx.event
    def next_sku_planning_page(self):
        self.sku_planning_page = min(
            self.sku_planning_page + 1, self.sku_planning_total_pages
        )

    @rx.event
    def change_sales_demand_view(self, value: str):
        """Switch tabs first, then hydrate only the selected large module."""
        self.sales_demand_view = value
        self.transfer_page = 1
        # Send the inexpensive navigation change to the browser immediately.
        # The selected module's optional data is applied in a second state
        # update, so a large sales table cannot hold up the tab transition.
        yield
        if value == "production" and not self.production_module_loaded:
            yield DashboardState.load_production_data_background
        if value in self.sales_loaded_views:
            if value == "production":
                self._set_initial_calendar_month()
                self._initialize_production_target()
            return
        yield DashboardState.load_sales_background

    @rx.event
    def change_shipment_exception_view(self, value: str):
        self.shipment_exception_view = value
        self.exception_page = 1
        yield
        yield DashboardState.load_distribution_operations_background

    @rx.event
    def change_shipment_exception_summary_view(self, value: bool):
        self.shipment_exception_show_manifest_summary = value

    @rx.event
    def change_package_lineage_draft(self, value: str):
        self.package_lineage_draft = value

    @rx.event
    def clear_package_lineage(self):
        self.package_lineage_draft = ""
        self.package_lineage_query = ""
        self.package_lineage_message = (
            "Search a Metrc package tag or manifest to reconstruct its stored history."
        )
        self.package_lineage_error = ""
        self.package_lineage_packages = "0"
        self.package_lineage_sources = "0"
        self.package_lineage_snapshots = "0"
        self.package_lineage_transfers = "0"
        self.package_lineage_records = []
        self.package_lineage_timeline = []

    @rx.event
    def find_package_lineage(self):
        target = self.package_lineage_draft.strip()
        if not target:
            self.package_lineage_error = "Enter a Metrc package tag or manifest number."
            return
        self.package_lineage_loading = True
        self.package_lineage_error = ""
        self.package_lineage_query = target
        self.package_lineage_message = "Searching preserved package history…"
        yield
        try:
            payload = load_package_lineage(target)
            self.package_lineage_message = str(payload.get("message", ""))
            self.package_lineage_packages = f"{int(payload.get('package_count', 0)):,}"
            self.package_lineage_sources = f"{int(payload.get('source_count', 0)):,}"
            self.package_lineage_snapshots = f"{int(payload.get('snapshot_count', 0)):,}"
            self.package_lineage_transfers = f"{int(payload.get('transfer_count', 0)):,}"
            self.package_lineage_records = list(payload.get("lineage", []))
            self.package_lineage_timeline = list(payload.get("timeline", []))
        except Exception as error:
            self.package_lineage_error = (
                "Historical package lineage could not be searched: " + str(error)
            )
            self.package_lineage_message = "No lineage results are currently displayed."
            self.package_lineage_records = []
            self.package_lineage_timeline = []
        finally:
            self.package_lineage_loading = False

    @rx.event
    def change_workspace_view(self, value: str):
        self.workspace_view = value
        if value == "cultivation":
            if not self.cultivation_saved_loaded:
                yield DashboardState.load_saved_clone_allocations
            if not self.cultivation_clone_plan_history_loaded:
                yield DashboardState.load_cultivation_clone_plan_history
            return
        if value == "quality":
            self.qa_view = self.quality_view
            if not self.qa_loaded and not self.qa_loading:
                yield DashboardState.load_qa_background(False)
            return
        if value == "distribution":
            self.qa_view = self.distribution_view
            if self.distribution_view in {"transfers", "exceptions"}:
                yield DashboardState.load_distribution_operations_background
                return
            if (
                self.distribution_view in {"customers", "retail"}
                and self.distribution_view not in self.sales_loaded_views
            ):
                self.sales_demand_view = self.distribution_view
                yield DashboardState.load_sales_background
            return
        if value != "sales_demand":
            return
        if (
            self.sales_demand_view == "production"
            and not self.production_module_loaded
        ):
            yield DashboardState.load_production_data_background
        if self.sales_demand_view in self.sales_loaded_views:
            return
        yield DashboardState.load_sales_background

    def change_executive_view(self, value: str):
        self.executive_view = value
        self.executive_report_error = ""
        self.executive_report_message = ""

    def _registry_payload(self) -> dict[str, list[dict[str, Any]]]:
        if not self._cultivation_registry:
            self._cultivation_registry = load_registry()
            self.cultivation_registry_loaded = True
            self.cultivation_registry_revision += 1
        return self._cultivation_registry

    def _current_clone_period(self) -> dict[str, str]:
        """Return the explicitly selected or date-current schedule crop."""
        rows = list(self._registry_payload().get("schedule", []))
        current = current_schedule_row(rows)
        if current:
            return {
                "crop": str(current.get("crop", "")),
                "room": str(current.get("room", "")),
                "clone_cut_date": str(current.get("clone_cut_date", "")),
                "flower_entry_date": str(current.get("flower_entry_date", "")),
                "harvest_date": str(current.get("harvest_date", "")),
                "available_date": str(current.get("available_date", "")),
            }
        return clone_planning_periods(1)[0]

    def _registered_room_bench_plans(
        self, room: str, density: float | None = None
    ) -> list[BenchPlan]:
        benches = [
            row for row in self._registry_payload().get("benches", [])
            if str(row.get("room_name", "")) == room and bool(row.get("active", True))
        ]
        if not benches:
            return room_bench_plans(room, density or self.cultivation_plant_density)
        palette = (
            ("#0f766e", "#f0fdfa"), ("#2563eb", "#eff6ff"),
            ("#7c3aed", "#f5f3ff"), ("#ea580c", "#fff7ed"),
            ("#db2777", "#fdf2f8"), ("#0891b2", "#ecfeff"),
            ("#65a30d", "#f7fee7"), ("#ca8a04", "#fefce8"),
        )
        selected_density = density or self.cultivation_plant_density
        plans: list[BenchPlan] = []
        for index, bench in enumerate(benches):
            metrics = calculate_bench_metrics(
                bench.get("length_ft"), bench.get("width_ft"), selected_density
            )
            plans.append({
                "bench": str(bench.get("bench", "")),
                "length": float(bench.get("length_ft", 0) or 0),
                "width": float(bench.get("width_ft", 0) or 0),
                "square_feet": float(metrics["canopy_sqft"]),
                "target_plants": int(metrics["target_plants"]),
                "strain_count": 1, "strain_1": "", "percent_1": 100.0,
                "strain_2": "", "percent_2": 0.0,
                "strain_3": "", "percent_3": 0.0,
                "accent": palette[index % len(palette)][0],
                "tint": palette[index % len(palette)][1],
            })
        return plans

    @rx.var(cache=True)
    def cultivation_schedule_rows(self) -> list[dict[str, Any]]:
        _ = self.cultivation_registry_revision
        return [{
            "Crop": row.get("crop", ""), "Program": row.get("program_id", ""),
            "Room": row.get("room", ""), "Clone Cut": row.get("clone_cut_date", ""),
            "Flower Entry": row.get("flower_entry_date", ""), "Harvest": row.get("harvest_date", ""),
            "Expected Available": row.get("available_date", ""), "Status": row.get("status", ""),
            "Source": row.get("source", ""), "Schedule ID": row.get("schedule_id", ""),
        } for row in self._registry_payload().get("schedule", [])]

    @rx.var(cache=True)
    def cultivation_room_registry_rows(self) -> list[dict[str, Any]]:
        _ = self.cultivation_registry_revision
        return [{
            "Room ID": row.get("room_id", ""), "Code": row.get("room_code", ""),
            "Room": row.get("name", ""), "Building": row.get("building", ""),
            "Program": row.get("program_id", ""), "L × W × H (ft)": f'{row.get("length_ft",0)} × {row.get("width_ft",0)} × {row.get("height_ft",0)}',
            "Floor Area": row.get("floor_area_sqft", 0), "Volume": row.get("volume_cuft", 0),
            "Physical Canopy": row.get("physical_canopy_sqft", 0), "Overhead": row.get("overhead_type", ""),
            "Fixtures": row.get("fixture_count", 0), "Total Overhead W": row.get("total_overhead_watts", 0),
            "Effective": row.get("effective_date", "") or "—", "Active": "Yes" if row.get("active", True) else "No",
        } for row in self._registry_payload().get("rooms", [])]

    @rx.var(cache=True)
    def cultivation_bench_registry_rows(self) -> list[dict[str, Any]]:
        _ = self.cultivation_registry_revision
        return [{
            "Bench ID": row.get("bench_id", ""), "Room": row.get("room_name", ""),
            "Bench": row.get("bench", ""), "Length": row.get("length_ft", 0), "Width": row.get("width_ft", 0),
            "Canopy sqft": row.get("canopy_sqft", 0), "Plants/sqft": row.get("default_density", 0),
            "Target Plants": row.get("target_plants", 0), "Supplemental": row.get("supplemental_type", ""),
            "Rows": row.get("supplemental_rows", 0), "Watts/Row": row.get("watts_per_row", 0),
            "Total Supplemental W": row.get("total_supplemental_watts", 0),
            "Effective": row.get("effective_date", "") or "—", "Active": "Yes" if row.get("active", True) else "No",
        } for row in self._registry_payload().get("benches", [])]

    @rx.var(cache=True)
    def cultivation_historical_entry_rows(self) -> list[dict[str, Any]]:
        _ = self.cultivation_registry_revision
        output: list[dict[str, Any]] = []
        for record in self._registry_payload().get("historical_yields", []):
            ff = fresh_frozen_canopy(
                planted_canopy_sqft=record.get("planted_canopy_sqft"),
                planted_plants=record.get("planted_plants"),
                fresh_frozen_plants=(record.get("actual_ff_plants") or record.get("planned_ff_plants")),
                actual_fresh_frozen_canopy_sqft=record.get("actual_ff_canopy_sqft"),
            )
            dry = float(record.get("dry_flower_lbs", 0) or 0)
            net_canopy = float(ff["net_dry_canopy_sqft"] or 0)
            output.append({
                "Record ID": record.get("harvest_id", ""),
                "Crop": record.get("crop", ""), "Room": record.get("room", ""),
                "Strain": record.get("strain", "") or "Room total",
                "Harvest Date": record.get("harvest_date", ""),
                "Planted Canopy": ff["planted_canopy_sqft"],
                "Fresh Frozen Plants": ff["fresh_frozen_plants"],
                "Fresh Frozen Canopy": ff["fresh_frozen_canopy_sqft"],
                "Net Dry Canopy": ff["net_dry_canopy_sqft"],
                "Dry Flower (lb)": round(dry, 2),
                "Yield (g/sqft)": round(dry * 453.59237 / net_canopy, 1) if net_canopy else 0,
                "Source": record.get("data_source", "Manual"),
            })
        return output

    @rx.var(cache=True)
    def cultivation_program_options(self) -> list[str]:
        _ = self.cultivation_registry_revision
        return [str(row.get("program_id", "")) for row in self._registry_payload().get("programs", [])]

    @rx.var(cache=True)
    def cultivation_registry_room_options(self) -> list[str]:
        _ = self.cultivation_registry_revision
        return [str(row.get("name", "")) for row in self._registry_payload().get("rooms", []) if row.get("active", True)]

    @rx.var(cache=True)
    def cultivation_registry_room_id_options(self) -> list[str]:
        _ = self.cultivation_registry_revision
        return [str(row.get("room_id", "")) for row in self._registry_payload().get("rooms", []) if row.get("active", True)]

    @rx.var(cache=True)
    def cultivation_registry_bench_id_options(self) -> list[str]:
        _ = self.cultivation_registry_revision
        return [str(row.get("bench_id", "")) for row in self._registry_payload().get("benches", []) if row.get("active", True)]

    @rx.var(cache=True)
    def cultivation_schedule_id_options(self) -> list[str]:
        _ = self.cultivation_registry_revision
        return [
            f'{row.get("crop", "")} | {row.get("room", "")} | '
            f'{row.get("clone_cut_date", "")} | {row.get("schedule_id", "")}'
            for row in self._registry_payload().get("schedule", [])
        ]

    @rx.var(cache=True)
    def cultivation_schedule_preview_rows(self) -> list[dict[str, Any]]:
        return [{
            "Crop": row.get("crop", ""), "Room": row.get("room", ""),
            "Clone Cut Date": row.get("clone_cut_date", ""),
            "Flower Entry Date": row.get("flower_entry_date", ""),
            "Harvest Date": row.get("harvest_date", ""),
            "Available Date": row.get("available_date", ""),
            "Status": row.get("status", ""),
        } for row in self.cultivation_schedule_preview]

    @rx.var(cache=True)
    def cultivation_current_crop_badge(self) -> str:
        _ = self.cultivation_registry_revision
        return "CURRENT CYCLE · " + self._current_clone_period()["crop"]

    @rx.var(cache=True)
    def cultivation_current_room_capacity_title(self) -> str:
        _ = self.cultivation_registry_revision
        room = self._current_clone_period()["room"].replace("Flower Room ", "F")
        return f"{room} Room Capacity"

    @rx.var(cache=True)
    def cultivation_historical_yield_id_options(self) -> list[str]:
        _ = self.cultivation_registry_revision
        return [str(row.get("harvest_id", "")) for row in self._registry_payload().get("historical_yields", [])]

    @rx.var(cache=True)
    def cultivation_schedule_future_count(self) -> int:
        _ = self.cultivation_registry_revision
        today = date.today().isoformat()
        return len([row for row in self._registry_payload().get("schedule", []) if str(row.get("clone_cut_date", "")) >= today])

    @rx.event
    def load_cultivation_registry(self):
        self.cultivation_registry_error = ""
        try:
            self._cultivation_registry = load_registry()
            self.cultivation_registry_loaded = True
            self.cultivation_registry_revision += 1
            period = self._current_clone_period()
            current = next(
                (row for row in self._cultivation_registry.get("schedule", [])
                 if str(row.get("crop", "")) == period["crop"]),
                None,
            )
            self.cultivation_current_schedule_id = str(
                (current or {}).get("schedule_id", "")
            )
            self.cultivation_current_crop_draft = period["crop"]
            self.cultivation_registry_message = "Cultivation schedule and facility registries loaded."
        except Exception as error:
            self.cultivation_registry_loaded = True
            self.cultivation_registry_error = "The cultivation registry could not be loaded: " + str(error)

    @rx.event
    def preview_cultivation_schedule(self, form_data: dict[str, Any]):
        self.cultivation_registry_error = ""
        try:
            registry = self._registry_payload()
            program_id = str(form_data.get("program_id") or self.cultivation_schedule_program)
            program = next((row for row in registry.get("programs", []) if str(row.get("program_id")) == program_id), registry.get("programs", [{}])[0])
            self.cultivation_schedule_program = program_id
            self.cultivation_schedule_start_crop = str(form_data.get("start_crop") or CLONE_PLANNING_FIRST_CROP)
            self.cultivation_schedule_first_cut = str(form_data.get("first_cut") or CLONE_PLANNING_FIRST_CUT_DATE.isoformat())
            self.cultivation_schedule_count = max(1, int(form_data.get("count") or DEFAULT_FUTURE_CROPS))
            self.cultivation_schedule_preview = generate_schedule(program=program, rooms=registry.get("rooms", []), start_crop=self.cultivation_schedule_start_crop, first_clone_cut=self.cultivation_schedule_first_cut, count=self.cultivation_schedule_count)
            self.cultivation_registry_message = f"Previewed {len(self.cultivation_schedule_preview)} crops. Review them before saving."
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def preview_cultivation_schedule_editor(self):
        self.cultivation_registry_error = ""
        try:
            registry = self._registry_payload()
            program = next(
                (row for row in registry.get("programs", []) if str(row.get("program_id")) == self.cultivation_schedule_program),
                registry.get("programs", [{}])[0],
            )
            self.cultivation_schedule_preview = generate_schedule(
                program=program, rooms=registry.get("rooms", []),
                start_crop=self.cultivation_schedule_start_crop,
                first_clone_cut=self.cultivation_schedule_first_cut,
                count=self.cultivation_schedule_count,
            )
            if any(
                str(row.get("program_id", "")) == self.cultivation_schedule_program
                and str(row.get("status", "")) == "Planning"
                for row in registry.get("schedule", [])
            ):
                for row in self.cultivation_schedule_preview:
                    row["status"] = "Upcoming"
            self.cultivation_registry_message = f"Previewed {len(self.cultivation_schedule_preview)} crops. Review them before saving."
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def save_cultivation_schedule_preview(self):
        if not self.cultivation_schedule_preview:
            self.cultivation_registry_error = "Preview the schedule before saving it."
            return
        self.cultivation_schedule_saving = True
        try:
            count = save_schedule_rows(self.cultivation_schedule_preview, self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry()
            self.cultivation_registry_revision += 1
            self.cultivation_schedule_preview = []
            self.cultivation_registry_message = (
                f"Saved {count} cultivation schedule records. They now appear in "
                "the Saved Schedule table below. Clone Allocation automatically follows "
                "the most recent crop whose clone-cut date has arrived."
            )
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)
        finally:
            self.cultivation_schedule_saving = False

    @rx.event
    def choose_current_schedule(self, schedule_id: str):
        try:
            selected_id = str(schedule_id).rsplit(" | ", 1)[-1].strip()
            set_current_schedule(selected_id, self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry()
            self.cultivation_registry_revision += 1
            period = self._current_clone_period()
            self.cultivation_current_schedule_id = selected_id
            self.cultivation_current_crop_draft = period["crop"]
            self.cultivation_flower_room = period["room"]
            self.cultivation_cycle_name = period["crop"]
            self.cultivation_flower_entry_date = period["flower_entry_date"]
            self.cultivation_bench_plans = self._registered_room_bench_plans(period["room"])
            self.cultivation_registry_message = f'{period["crop"]} is now the current Clone Allocation crop.'
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def save_current_crop_name(self):
        self.cultivation_registry_error = ""
        try:
            schedule_id = self.cultivation_current_schedule_id
            if not schedule_id:
                current_crop = self._current_clone_period()["crop"]
                current = next(
                    (row for row in self._registry_payload().get("schedule", [])
                     if str(row.get("crop", "")) == current_crop),
                    None,
                )
                schedule_id = str((current or {}).get("schedule_id", ""))
            if not schedule_id:
                raise ValueError("Select a saved schedule crop before renaming it.")
            crop = rename_schedule_crop(
                schedule_id,
                self.cultivation_current_crop_draft,
                self.auth_name or self.auth_email or "QCC Reflex User",
            )
            self._cultivation_registry = load_registry()
            self.cultivation_registry_revision += 1
            self.cultivation_cycle_name = crop
            self.cultivation_current_crop_draft = crop
            self.cultivation_clone_plan_history = load_clone_plans()
            self.cultivation_clone_plan_entry_version += 1
            self.cultivation_registry_message = (
                f"Current crop renamed to {crop}. Linked clone plans, room layouts, "
                "Fresh Frozen adjustments, and manual yield records were kept attached."
            )
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def submit_cycle_program(self, form_data: dict[str, Any]):
        try:
            rotation = [value.strip() for value in str(form_data.get("room_rotation", "")).split(",") if value.strip()]
            program_id = save_cycle_program({
                "program_id": form_data.get("program_id", ""), "name": form_data.get("name", ""),
                "code_prefix": form_data.get("code_prefix", "F"), "cadence_days": form_data.get("cadence_days", 14),
                "rooting_days": form_data.get("rooting_days", 21), "veg_days": form_data.get("veg_days", 19),
                "flowering_days": form_data.get("flowering_days", 68), "processing_days": form_data.get("processing_days", 30),
                "target_future_crops": form_data.get("target_future_crops", 26), "room_rotation": rotation, "active": True,
            }, self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved cycle program {program_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def save_cycle_program_editor(self):
        try:
            program_id = save_cycle_program({
                "program_id": self.cultivation_schedule_program,
                "name": self.cultivation_program_name,
                "code_prefix": self.cultivation_program_code_prefix,
                "cadence_days": self.cultivation_program_cadence_days,
                "rooting_days": self.cultivation_program_rooting_days,
                "veg_days": self.cultivation_program_veg_days,
                "flowering_days": self.cultivation_program_flowering_days,
                "processing_days": self.cultivation_program_processing_days,
                "target_future_crops": self.cultivation_program_target_crops,
                "room_rotation": [value.strip() for value in self.cultivation_program_room_rotation.split(",") if value.strip()],
                "active": True,
            }, self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved cycle program {program_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def submit_cultivation_room(self, form_data: dict[str, Any]):
        try:
            room_id = save_room(dict(form_data), self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved room {room_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def save_cultivation_room_editor(self):
        try:
            room_id = save_room({
                "room_id": self.cultivation_room_edit_id, "room_code": self.cultivation_room_code,
                "name": self.cultivation_room_name, "building": self.cultivation_room_building,
                "program_id": self.cultivation_room_program, "length_ft": self.cultivation_room_length,
                "width_ft": self.cultivation_room_width, "height_ft": self.cultivation_room_height,
                "overhead_type": self.cultivation_room_overhead_type, "overhead_other": self.cultivation_room_overhead_other,
                "fixture_count": self.cultivation_room_fixture_count, "watts_per_fixture": self.cultivation_room_watts_fixture,
                "overhead_watts_override": self.cultivation_room_watts_override,
                "effective_date": self.cultivation_room_effective_date, "notes": self.cultivation_room_notes,
                "active": True,
            }, self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved room {room_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def load_cultivation_room_editor(self, room_id: str):
        row = next((item for item in self._registry_payload().get("rooms", []) if str(item.get("room_id")) == room_id), None)
        if not row:
            return
        self.cultivation_room_edit_id = room_id
        self.cultivation_room_code = str(row.get("room_code", ""))
        self.cultivation_room_name = str(row.get("name", ""))
        self.cultivation_room_building = str(row.get("building", ""))
        self.cultivation_room_program = str(row.get("program_id", ""))
        self.cultivation_room_length = float(row.get("length_ft", 0) or 0)
        self.cultivation_room_width = float(row.get("width_ft", 0) or 0)
        self.cultivation_room_height = float(row.get("height_ft", 0) or 0)
        self.cultivation_room_overhead_type = str(row.get("overhead_type", "Other"))
        self.cultivation_room_overhead_other = str(row.get("overhead_other", ""))
        self.cultivation_room_fixture_count = int(row.get("fixture_count", 0) or 0)
        self.cultivation_room_watts_fixture = float(row.get("watts_per_fixture", 0) or 0)
        self.cultivation_room_watts_override = float(row.get("overhead_watts_override", 0) or 0)
        self.cultivation_room_effective_date = str(row.get("effective_date", "") or "")
        self.cultivation_room_notes = str(row.get("notes", ""))

    @rx.event
    def submit_cultivation_bench(self, form_data: dict[str, Any]):
        try:
            bench_id = save_bench(dict(form_data), self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved bench {bench_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def save_cultivation_bench_editor(self):
        try:
            bench_id = save_bench({
                "bench_id": self.cultivation_bench_edit_id, "room_id": self.cultivation_bench_room_id,
                "bench": self.cultivation_bench_name, "length_ft": self.cultivation_bench_length,
                "width_ft": self.cultivation_bench_width, "default_density": self.cultivation_bench_density,
                "supplemental_type": self.cultivation_bench_supplemental_type,
                "supplemental_rows": self.cultivation_bench_supplemental_rows,
                "watts_per_row": self.cultivation_bench_watts_row,
                "supplemental_watts_override": self.cultivation_bench_watts_override,
                "effective_date": self.cultivation_bench_effective_date, "notes": self.cultivation_bench_notes,
                "active": True,
            }, self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved bench {bench_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def load_cultivation_bench_editor(self, bench_id: str):
        row = next((item for item in self._registry_payload().get("benches", []) if str(item.get("bench_id")) == bench_id), None)
        if not row:
            return
        self.cultivation_bench_edit_id = bench_id
        self.cultivation_bench_room_id = str(row.get("room_id", ""))
        self.cultivation_bench_name = str(row.get("bench", ""))
        self.cultivation_bench_length = float(row.get("length_ft", 0) or 0)
        self.cultivation_bench_width = float(row.get("width_ft", 0) or 0)
        self.cultivation_bench_density = float(row.get("default_density", 0.75) or 0.75)
        self.cultivation_bench_supplemental_type = str(row.get("supplemental_type", "None"))
        self.cultivation_bench_supplemental_rows = int(row.get("supplemental_rows", 0) or 0)
        self.cultivation_bench_watts_row = float(row.get("watts_per_row", 0) or 0)
        self.cultivation_bench_watts_override = float(row.get("supplemental_watts_override", 0) or 0)
        self.cultivation_bench_effective_date = str(row.get("effective_date", "") or "")
        self.cultivation_bench_notes = str(row.get("notes", ""))

    @rx.event
    def submit_historical_yield(self, form_data: dict[str, Any]):
        try:
            harvest_id = save_historical_yield(dict(form_data), self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved historical yield {harvest_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def load_historical_yield_editor(self, harvest_id: str):
        row = next((item for item in self._registry_payload().get("historical_yields", []) if str(item.get("harvest_id")) == harvest_id), None)
        if not row:
            return
        self.cultivation_yield_edit_id = harvest_id
        self.cultivation_yield_crop = str(row.get("crop", ""))
        self.cultivation_yield_room = str(row.get("room", ""))
        self.cultivation_yield_strain = str(row.get("strain", ""))
        self.cultivation_yield_harvest_date = str(row.get("harvest_date", "") or "")
        self.cultivation_yield_physical_canopy = float(row.get("physical_canopy_sqft", 0) or 0)
        self.cultivation_yield_planted_canopy = float(row.get("planted_canopy_sqft", 0) or 0)
        self.cultivation_yield_planted_plants = int(row.get("planted_plants", 0) or 0)
        self.cultivation_yield_planned_ff_plants = int(row.get("planned_ff_plants", 0) or 0)
        self.cultivation_yield_actual_ff_plants = int(row.get("actual_ff_plants", 0) or 0)
        self.cultivation_yield_actual_ff_canopy = float(row.get("actual_ff_canopy_sqft", 0) or 0)
        self.cultivation_yield_wet_lbs = float(row.get("wet_yield_lbs", 0) or 0)
        self.cultivation_yield_dry_lbs = float(row.get("dry_flower_lbs", 0) or 0)
        self.cultivation_yield_ab_lbs = float(row.get("ab_flower_lbs", 0) or 0)
        self.cultivation_yield_c_lbs = float(row.get("c_flower_lbs", 0) or 0)
        self.cultivation_yield_trim_lbs = float(row.get("trim_lbs", 0) or 0)
        self.cultivation_yield_quality = float(row.get("quality_score", 0) or 0)
        self.cultivation_yield_notes = str(row.get("notes", ""))

    @rx.event
    def save_historical_yield_editor(self):
        try:
            harvest_id = save_historical_yield({
                "harvest_id": self.cultivation_yield_edit_id, "crop": self.cultivation_yield_crop,
                "room": self.cultivation_yield_room, "strain": self.cultivation_yield_strain,
                "harvest_date": self.cultivation_yield_harvest_date,
                "physical_canopy_sqft": self.cultivation_yield_physical_canopy,
                "planted_canopy_sqft": self.cultivation_yield_planted_canopy,
                "planted_plants": self.cultivation_yield_planted_plants,
                "planned_ff_plants": self.cultivation_yield_planned_ff_plants,
                "actual_ff_plants": self.cultivation_yield_actual_ff_plants,
                "actual_ff_canopy_sqft": self.cultivation_yield_actual_ff_canopy,
                "wet_yield_lbs": self.cultivation_yield_wet_lbs, "dry_flower_lbs": self.cultivation_yield_dry_lbs,
                "ab_flower_lbs": self.cultivation_yield_ab_lbs, "c_flower_lbs": self.cultivation_yield_c_lbs,
                "trim_lbs": self.cultivation_yield_trim_lbs, "quality_score": self.cultivation_yield_quality,
                "data_source": "Manual", "notes": self.cultivation_yield_notes,
            }, self.auth_name or self.auth_email or "QCC Reflex User")
            self._cultivation_registry = load_registry(); self.cultivation_registry_revision += 1
            self.cultivation_registry_message = f"Saved historical yield {harvest_id}."
            self.cultivation_registry_error = ""
        except Exception as error:
            self.cultivation_registry_error = str(error)

    @rx.event
    def change_cultivation_view(self, value: str):
        self.cultivation_view = value
        if value in {"clone_planning", "clone_allocation", "schedule", "rooms_benches", "historical_yield"} and not self.cultivation_registry_loaded:
            try:
                self._cultivation_registry = load_registry()
            except Exception as error:
                self._cultivation_registry = {
                    "programs": [default_cycle_program()],
                    "rooms": default_room_rows(), "benches": default_bench_rows(),
                    "schedule": default_schedule(), "historical_yields": [],
                }
                self.cultivation_registry_error = (
                    "Shared cultivation registry is unavailable; legacy schedule defaults remain active. "
                    + str(error)
                )
            self.cultivation_registry_loaded = True
            self.cultivation_registry_revision += 1
        if value in {"clone_planning", "clone_allocation", "schedule"}:
            selected_period = self._current_clone_period()
            current = next(
                (row for row in self._registry_payload().get("schedule", [])
                 if str(row.get("crop", "")) == selected_period["crop"]),
                None,
            )
            self.cultivation_current_schedule_id = str(
                (current or {}).get("schedule_id", "")
            )
            self.cultivation_current_crop_draft = selected_period["crop"]
        if value == "clone_allocation":
            period = self._current_clone_period()
            was_current_plan = (
                self.cultivation_flower_room == period["room"]
                and self.cultivation_cycle_name == period["crop"]
                and self.cultivation_flower_entry_date == period["flower_entry_date"]
            )
            self.cultivation_flower_room = period["room"]
            self.cultivation_cycle_name = period["crop"]
            self.cultivation_flower_entry_date = period["flower_entry_date"]
            if not was_current_plan:
                self.cultivation_bench_plans = self._registered_room_bench_plans(
                    period["room"], self.cultivation_plant_density
                )
        if value == "clone_planning" and not self.cultivation_clone_plan_history_loaded:
            yield DashboardState.load_cultivation_clone_plan_history
        if value == "metrc_plants" and not self.cultivation_plant_snapshot_loaded:
            yield DashboardState.load_cultivation_plant_snapshot

    @rx.event
    def load_cultivation_plant_snapshot(self):
        self.cultivation_plant_error = ""
        try:
            self._cultivation_plant_snapshot = load_latest_metrc_plant_snapshot()
            self.cultivation_plant_snapshot_revision += 1
            self.cultivation_plant_snapshot_loaded = True
            if not self._cultivation_plant_snapshot:
                self.cultivation_plant_message = (
                    "No Metrc plant snapshot has been published yet. Upload all four "
                    "active exports to create the first snapshot."
                )
            else:
                self.cultivation_plant_message = "Latest Metrc plant snapshot loaded."
        except Exception as error:
            self.cultivation_plant_snapshot_loaded = True
            self.cultivation_plant_error = (
                "The Metrc plant snapshot could not be loaded: " + str(error)
            )

    @rx.event
    async def import_cultivation_plant_files(self, files: list[rx.UploadFile]):
        self.cultivation_plant_error = ""
        self.cultivation_plant_message = ""
        if not self._require_active_session():
            self.cultivation_plant_error = (
                "Your session expired. Sign in again to import Metrc plant data."
            )
            return
        if not files:
            self.cultivation_plant_error = "Choose all four active Metrc plant exports."
            return
        self.cultivation_plant_importing = True
        yield
        try:
            uploaded = [(file.name, await file.read()) for file in files]
            snapshot = parse_metrc_plant_exports(uploaded)
            self._cultivation_plant_snapshot = snapshot
            self.cultivation_plant_snapshot_revision += 1
            self.cultivation_plant_snapshot_loaded = True
            try:
                save_metrc_plant_snapshot(
                    snapshot,
                    imported_by=self.auth_name or self.auth_email or "QCC Reflex User",
                )
                self.cultivation_plant_message = (
                    "Metrc plant snapshot imported and saved. The previous snapshot "
                    "remains available in Supabase history."
                )
            except Exception as save_error:
                self.cultivation_plant_message = (
                    "The snapshot is available for this session but could not be saved: "
                    + str(save_error)
                )
        except Exception as error:
            self.cultivation_plant_error = "Plant import failed: " + str(error)
        finally:
            self.cultivation_plant_importing = False
        yield rx.clear_selected_files("cultivation_plant_upload")

    @rx.event
    def change_cultivation_plant_view(self, value: str):
        self.cultivation_plant_view = value

    @rx.event
    def change_cultivation_plant_facility_filter(self, value: str):
        self.cultivation_plant_facility_filter = value
        self.cultivation_plant_location_filter = "All Locations"

    @rx.event
    def change_cultivation_plant_phase_filter(self, value: str):
        self.cultivation_plant_phase_filter = value

    @rx.event
    def change_cultivation_plant_location_filter(self, value: str):
        self.cultivation_plant_location_filter = value

    @rx.event
    def change_cultivation_plant_strain_filter(self, value: str):
        self.cultivation_plant_strain_filter = value

    @rx.event
    def change_cultivation_plant_rows_per_page(self, value: str):
        self.cultivation_plant_rows_per_page = (
            value if value in {"10", "25", "50", "100"} else "10"
        )

    def _cultivation_active_plant_rows(self) -> list[dict[str, Any]]:
        rows = [
            *self._cultivation_plant_snapshot.get("flowering", []),
            *self._cultivation_plant_snapshot.get("vegetative", []),
        ]
        result: list[dict[str, Any]] = []
        for row in rows:
            if (
                self.cultivation_plant_facility_filter != "All Facilities"
                and row.get("facility") != self.cultivation_plant_facility_filter
            ):
                continue
            if (
                self.cultivation_plant_phase_filter != "All Phases"
                and row.get("phase") != self.cultivation_plant_phase_filter
            ):
                continue
            if (
                self.cultivation_plant_location_filter != "All Locations"
                and row.get("location") != self.cultivation_plant_location_filter
            ):
                continue
            if (
                self.cultivation_plant_strain_filter != "All Strains"
                and row.get("strain") != self.cultivation_plant_strain_filter
            ):
                continue
            result.append(row)
        return result

    def _cultivation_filtered_plantings(self) -> list[dict[str, Any]]:
        if self.cultivation_plant_phase_filter != "All Phases":
            return []
        result = []
        for row in self._cultivation_plant_snapshot.get("plantings", []):
            if self.cultivation_plant_facility_filter != "All Facilities" and row.get("facility") != self.cultivation_plant_facility_filter:
                continue
            if self.cultivation_plant_location_filter != "All Locations" and row.get("location") != self.cultivation_plant_location_filter:
                continue
            if self.cultivation_plant_strain_filter != "All Strains" and row.get("strain") != self.cultivation_plant_strain_filter:
                continue
            result.append(row)
        return result

    def _cultivation_filtered_harvests(self) -> list[dict[str, Any]]:
        if self.cultivation_plant_phase_filter != "All Phases":
            return []
        result = []
        for row in self._cultivation_plant_snapshot.get("harvests", []):
            if self.cultivation_plant_facility_filter != "All Facilities" and row.get("facility") != self.cultivation_plant_facility_filter:
                continue
            if self.cultivation_plant_location_filter != "All Locations" and row.get("location") != self.cultivation_plant_location_filter:
                continue
            if self.cultivation_plant_strain_filter != "All Strains" and row.get("strain") != self.cultivation_plant_strain_filter:
                continue
            result.append(row)
        return result

    @staticmethod
    def _is_mother_plant(row: dict[str, Any]) -> bool:
        descriptor = " ".join(
            str(row.get(field, "") or "")
            for field in ("location", "sublocation", "plant_batch")
        ).casefold()
        return "mother" in descriptor or re.search(r"\bmom(?:s)?\b", descriptor) is not None

    @rx.var(cache=True)
    def cultivation_plant_snapshot_label(self) -> str:
        _ = self.cultivation_plant_snapshot_revision
        imported = str(self._cultivation_plant_snapshot.get("imported_at", "") or "")
        return imported[:19].replace("T", " ") if imported else "No snapshot"

    @rx.var(cache=True)
    def cultivation_plant_source_rows(self) -> list[list[str]]:
        _ = self.cultivation_plant_snapshot_revision
        labels = {
            "flowering": "Flowering Plants",
            "vegetative": "Vegetative Plants",
            "plantings": "Propagation",
            "harvests": "Harvests",
        }
        files = dict(self._cultivation_plant_snapshot.get("source_files") or {})
        return [
            [labels[kind], str(files.get(kind, "Missing"))]
            for kind in labels
        ]

    @rx.var(cache=True)
    def cultivation_plant_kpis(self) -> dict[str, str]:
        _ = self.cultivation_plant_snapshot_revision
        active = self._cultivation_active_plant_rows()
        plantings = self._cultivation_filtered_plantings()
        harvests = self._cultivation_filtered_harvests()
        flowering = [row for row in active if row.get("phase") == "Flowering"]
        vegetative = [row for row in active if row.get("phase") == "Vegetative"]
        mothers = [row for row in vegetative if self._is_mother_plant(row)]
        clones = sum(
            int(row.get("plants", 0) or 0)
            for row in plantings
            if "clone" in str(row.get("type", "") or "").casefold()
        )
        strains = {
            str(row.get("strain", ""))
            for row in [*active, *plantings]
            if row.get("strain")
        }
        unfinished = sum(
            1 for row in harvests
            if float(row.get("remaining_weight_lb", 0) or 0) > 0
        )
        return {
            "flowering": f"{len(flowering):,}",
            "vegetative": f"{len(vegetative):,}",
            "clones": f"{clones:,}",
            "mothers": f"{len(mothers):,}",
            "strains": f"{len(strains):,}",
            "unfinished_harvests": f"{unfinished:,}",
        }

    @rx.var(cache=True)
    def cultivation_plant_facility_options(self) -> list[str]:
        _ = self.cultivation_plant_snapshot_revision
        rows = [
            *self._cultivation_plant_snapshot.get("flowering", []),
            *self._cultivation_plant_snapshot.get("vegetative", []),
            *self._cultivation_plant_snapshot.get("plantings", []),
        ]
        return ["All Facilities", *sorted({str(row.get("facility", "")) for row in rows if row.get("facility")})]

    @rx.var(cache=True)
    def cultivation_plant_location_options(self) -> list[str]:
        _ = self.cultivation_plant_snapshot_revision
        rows = [
            *self._cultivation_plant_snapshot.get("flowering", []),
            *self._cultivation_plant_snapshot.get("vegetative", []),
        ]
        values = {
            str(row.get("location", ""))
            for row in rows
            if row.get("location")
            and (
                self.cultivation_plant_facility_filter == "All Facilities"
                or row.get("facility") == self.cultivation_plant_facility_filter
            )
        }
        return ["All Locations", *sorted(values, key=str.casefold)]

    @rx.var(cache=True)
    def cultivation_plant_strain_options(self) -> list[str]:
        _ = self.cultivation_plant_snapshot_revision
        rows = [
            *self._cultivation_plant_snapshot.get("flowering", []),
            *self._cultivation_plant_snapshot.get("vegetative", []),
            *self._cultivation_plant_snapshot.get("plantings", []),
            *self._cultivation_plant_snapshot.get("harvests", []),
        ]
        return ["All Strains", *sorted({str(row.get("strain", "")) for row in rows if row.get("strain")}, key=str.casefold)]

    @rx.var(cache=True)
    def cultivation_active_plant_count(self) -> str:
        _ = self.cultivation_plant_snapshot_revision
        return f"{len(self._cultivation_active_plant_rows()):,} matching plants"

    @rx.var(cache=True)
    def cultivation_active_plant_table_rows(self) -> list[list[Any]]:
        _ = self.cultivation_plant_snapshot_revision
        rows = sorted(
            self._cultivation_active_plant_rows(),
            key=lambda row: (str(row.get("location", "")), str(row.get("strain", "")), str(row.get("tag", ""))),
        )
        return [
            [
                row.get("tag", ""),
                row.get("strain", ""),
                row.get("phase", ""),
                row.get("facility", ""),
                row.get("location", ""),
                row.get("plant_batch", ""),
                row.get("plant_batch_date", ""),
                row.get("phase_date", ""),
                active_plant_harvest_date(row),
            ]
            for row in rows[:2000]
        ]

    @rx.var(cache=True)
    def cultivation_plant_location_summary_rows(self) -> list[list[Any]]:
        _ = self.cultivation_plant_snapshot_revision
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in self._cultivation_active_plant_rows():
            key = (str(row.get("facility", "")), str(row.get("phase", "")), str(row.get("location", "")))
            item = grouped.setdefault(key, {"plants": 0, "strains": set()})
            item["plants"] += 1
            if row.get("strain"):
                item["strains"].add(str(row["strain"]))
        return [
            [
                facility,
                phase,
                location,
                values["plants"],
                len(values["strains"]),
            ]
            for (facility, phase, location), values in sorted(grouped.items())
        ]

    @rx.var(cache=True)
    def cultivation_active_planting_rows(self) -> list[list[Any]]:
        _ = self.cultivation_plant_snapshot_revision
        rows = self._cultivation_filtered_plantings()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            filtered.append({
                "Plant Batch": row.get("plant_batch", ""),
                "Strain": row.get("strain", ""),
                "Facility": row.get("facility", ""),
                "Location": row.get("location", ""),
                "Available Plants": row.get("plants", 0),
                "Tracked": row.get("tracked", 0),
                "Packaged": row.get("packaged", 0),
                "Destroyed": row.get("destroyed", 0),
                "Batch Date": row.get("batch_date", ""),
            })
        return [
            [row.get(column, "") for column in PLANTING_COLUMNS]
            for row in sorted(
                filtered,
                key=lambda row: (row["Batch Date"], row["Strain"]),
                reverse=True,
            )
        ]

    @rx.var(cache=True)
    def cultivation_plant_harvest_rows(self) -> list[list[Any]]:
        _ = self.cultivation_plant_snapshot_revision
        rows = []
        for row in self._cultivation_filtered_harvests():
            rows.append({
                "Harvest Batch": row.get("harvest_batch", ""),
                "Strain": row.get("strain", ""),
                "Harvest Date": row.get("harvest_date", ""),
                "Plants": row.get("plants", 0),
                "Wet Weight (lb)": round(float(row.get("wet_weight_lb", 0) or 0), 2),
                "Packaged Weight (lb)": round(float(row.get("packaged_weight_lb", 0) or 0), 2),
                "Remaining Weight (lb)": round(float(row.get("remaining_weight_lb", 0) or 0), 2),
                "Packages": row.get("package_count", 0),
                "Fresh Frozen": "Yes" if row.get("fresh_frozen") else "No",
            })
        return [
            [row.get(column, "") for column in PLANT_HARVEST_COLUMNS]
            for row in sorted(
                rows,
                key=lambda row: (row["Harvest Date"], row["Harvest Batch"]),
                reverse=True,
            )[:2000]
        ]

    @rx.var(cache=True)
    def cultivation_plant_reconciliation_rows(self) -> list[list[Any]]:
        _ = self.cultivation_plant_snapshot_revision
        rows = plant_crop_reconciliation(
            self._cultivation_plant_snapshot, UPCOMING_CROP_ALLOCATIONS
        )
        return [
            [row.get(column, "") for column in PLANT_RECONCILIATION_COLUMNS]
            for row in rows
        ]

    @rx.event
    def change_cultivation_history_room_filter(self, value: str):
        self.cultivation_history_room_filter = value

    @rx.event
    def change_cultivation_history_table_view(self, value: str):
        self.cultivation_history_table_view = value

    @rx.event
    def change_cultivation_history_rows_per_page(self, value: str):
        self.cultivation_history_rows_per_page = (
            value if value in {"10", "25", "50", "100"} else "10"
        )

    @rx.event
    def change_cultivation_history_strain_filter(self, value: str):
        self.cultivation_history_strain_filter = value

    @rx.event
    def change_cultivation_demand_strain_filter(self, value: str):
        self.cultivation_demand_strain_filter = value

    @rx.event
    def change_cultivation_demand_brand_filter(self, value: str):
        self.cultivation_demand_brand_filter = value
        self.cultivation_demand_strain_filter = "All Strains"

    @rx.event
    def change_cultivation_demand_sku_filter(self, value: str):
        self.cultivation_demand_sku_filter = value

    @rx.event
    def change_cultivation_demand_rows_per_page(self, value: str):
        self.cultivation_demand_rows_per_page = (
            value if value in {"10", "25", "50", "100"} else "10"
        )

    @rx.var(cache=True)
    def cultivation_plant_page_size(self) -> int:
        return int(self.cultivation_plant_rows_per_page)

    @rx.event
    def change_cultivation_clone_plan_lookback(self, value: str):
        self.cultivation_clone_plan_lookback = (
            value
            if value in {
                "No Historical Crops", "Last 4 Crops", "Last 8 Crops"
            }
            else "No Historical Crops"
        )

    @rx.event
    def change_cultivation_historical_plan_crop(self, value: str):
        valid = {period["crop"] for period in prior_clone_planning_periods(8)}
        if value in valid:
            self.cultivation_historical_plan_crop = value
            self.cultivation_historical_plan_allocations = {}
            self.cultivation_historical_plan_entry_version += 1
            self.cultivation_clone_plan_error = ""
            self.cultivation_clone_plan_message = ""

    @rx.event
    def change_cultivation_historical_plan_allocation(
        self, strain: str, value: str
    ):
        self.cultivation_clone_plan_error = ""
        try:
            parsed = valid_bench_equivalent(value)
        except ValueError as error:
            self.cultivation_clone_plan_error = str(error)
            return
        updated = dict(self.cultivation_historical_plan_allocations)
        if parsed <= 0:
            updated.pop(strain, None)
        else:
            updated[strain] = parsed
        self.cultivation_historical_plan_allocations = updated

    @rx.event
    def change_cultivation_clone_strain_scope(self, value: str):
        self.cultivation_clone_strain_scope = (
            value
            if value in {"Clade9 Strains", "Clade9 + Craft Kings Strains"}
            else "Clade9 Strains"
        )

    @rx.event
    def change_cultivation_new_strain_name(self, value: str):
        self.cultivation_new_strain_name = value
        self.cultivation_new_strain_error = ""
        self.cultivation_new_strain_message = ""

    @rx.event
    def add_cultivation_provisional_strain(self):
        label = " ".join(self.cultivation_new_strain_name.strip().split())
        if not label:
            self.cultivation_new_strain_error = "Enter a strain name first."
            return
        existing = {
            normalized_strain(value)
            for value in self._cultivation_planning_strain_names()
        }
        if normalized_strain(label) in existing:
            self.cultivation_new_strain_error = f"{label} is already available."
            return
        self.cultivation_provisional_strains = [
            *self.cultivation_provisional_strains,
            label,
        ]
        self.cultivation_new_strain_name = ""
        self.cultivation_new_strain_error = ""
        self.cultivation_new_strain_message = (
            f"{label} is now available in Clone Planning and exact bench assignment. "
            "Until it has harvest history, projected yield uses the selected room average."
        )

    @rx.event
    def change_cultivation_clone_plan_demand_model(self, value: str):
        self.cultivation_clone_plan_demand_model = self._normalized_clone_demand_model(
            value
        )
        self.cultivation_clone_plan_demand_revision += 1
        self.cultivation_clone_plan_status = "Draft"
        self.cultivation_clone_plan_dirty = True

    @rx.event
    def change_cultivation_clone_plan_product_scope(self, value: str):
        self.cultivation_clone_plan_product_scope = (
            self._normalized_clone_demand_product_scope(value)
        )
        self.cultivation_clone_plan_demand_revision += 1
        self.cultivation_clone_plan_status = "Draft"
        self.cultivation_clone_plan_dirty = True

    @rx.event
    def change_cultivation_clone_plan_include_pre_wip(self, value: bool):
        self.cultivation_clone_plan_include_pre_wip = bool(value)

    @staticmethod
    def _normalized_clone_demand_model(value: Any) -> str:
        """Normalize current choices and legacy saved-plan labels."""
        label = str(value or "").strip()
        if label == "Experimental Availability-Adjusted":
            return "Availability-Adjusted"
        if label in {
            "AI-Adjusted",
            "Availability-Adjusted",
            "30-Day Availability-Adjusted",
            "60-Day Availability-Adjusted",
            "Current SKU Velocity",
        }:
            return label
        return "Availability-Adjusted"

    @staticmethod
    def _normalized_clone_demand_product_scope(value: Any) -> str:
        label = str(value or "").strip()
        if label in {"Flower + Pre-Rolls", "Pre-Rolls Only", "Flower Only"}:
            return label
        return "Flower + Pre-Rolls"

    @rx.event
    def change_cultivation_clone_plan_allocation(self, strain: str, value: str):
        self.cultivation_clone_plan_error = ""
        try:
            parsed = valid_bench_equivalent(value)
        except ValueError as error:
            self.cultivation_clone_plan_error = str(error)
            return
        updated = dict(self.cultivation_clone_plan_allocations)
        if parsed <= 0:
            updated.pop(strain, None)
        else:
            updated[strain] = parsed
        self.cultivation_clone_plan_allocations = updated
        self.cultivation_clone_plan_status = "Draft"
        self.cultivation_clone_plan_dirty = True

    @rx.event
    def change_cultivation_clone_plan_override_reason(self, value: str):
        self.cultivation_clone_plan_override_reason = value

    @rx.event
    def toggle_cultivation_clone_plan_override(self):
        self.cultivation_clone_plan_override = not self.cultivation_clone_plan_override

    def _clone_plan_capacity_error(self) -> str:
        period = self._current_clone_period()
        available = sum(
            float(row.get("square_feet", 0) or 0)
            for row in self._registered_room_bench_plans(period["room"])
        ) / 185.0
        planned = sum(
            float(value or 0)
            for value in self.cultivation_clone_plan_allocations.values()
        )
        if planned > available + 0.05:
            return (
                f"{period['crop']} has {available:.1f} full-bench equivalents in "
                f"{period['room']}; the plan currently assigns {planned:.1f}."
            )
        return ""

    @rx.event
    def load_cultivation_clone_plan_history(self):
        try:
            self.cultivation_clone_plan_history = load_clone_plans()
            adjustment_rows = load_fresh_frozen_adjustments()
            self.cultivation_fresh_frozen_adjustments = {
                f"{str(row.get('crop', '')).casefold()}|"
                f"{normalized_strain(row.get('strain', ''))}":
                int(row.get("planned_plants", 0) or 0)
                for row in adjustment_rows
            }
            self.cultivation_creative_use_adjustments = {
                f"{str(row.get('crop', '')).casefold()}|"
                f"{normalized_strain(row.get('strain', ''))}":
                float(row.get("creative_use_lbs", 0) or 0)
                for row in adjustment_rows
            }
            if not self.cultivation_clone_plan_dirty:
                self._restore_approved_current_clone_plan()
            self.cultivation_clone_plan_history_loaded = True
        except Exception as error:
            self.cultivation_clone_plan_error = (
                "Saved clone-plan history could not be loaded: " + str(error)
            )

    def _restore_approved_current_clone_plan(self) -> bool:
        """Hydrate the Rolling Planner from its saved approved current crop."""
        period = self._current_clone_period()
        plan = approved_clone_plan_for_crop(
            self.cultivation_clone_plan_history, period["crop"]
        )
        if plan is None:
            return False
        self.cultivation_clone_plan_allocations = {
            str(strain): float(value or 0)
            for strain, value in dict(plan.get("allocations") or {}).items()
            if float(value or 0) > 0
        }
        self.cultivation_clone_plan_demand_model = self._normalized_clone_demand_model(
            plan.get("demand_model", "Availability-Adjusted")
        )
        self.cultivation_clone_plan_product_scope = (
            self._normalized_clone_demand_product_scope(
                plan.get("demand_product_scope", "Flower + Pre-Rolls")
            )
        )
        self.cultivation_clone_plan_status = "Approved"
        self.cultivation_clone_plan_dirty = False
        self.cultivation_clone_plan_entry_version += 1
        return True

    @rx.event
    def save_cultivation_fresh_frozen_plants(
        self,
        crop: str,
        strain: str,
        harvest_date: str,
        planted_plants: int,
        value: str,
    ):
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = ""
        try:
            plants = max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            self.cultivation_clone_plan_error = (
                "Fresh Frozen plants must be entered as a whole number."
            )
            return
        maximum = max(0, int(planted_plants or 0))
        if plants > maximum:
            self.cultivation_clone_plan_error = (
                f"Fresh Frozen cannot exceed the {maximum} planted plants for "
                f"{strain} in {crop}."
            )
            return
        try:
            harvest = date.fromisoformat(str(harvest_date))
        except ValueError:
            self.cultivation_clone_plan_error = "That crop has no valid harvest date."
            return
        if date.today() > harvest:
            self.cultivation_clone_plan_error = (
                "Planned Fresh Frozen can only be changed through harvest day."
            )
            return
        self.cultivation_fresh_frozen_saving = True
        try:
            save_fresh_frozen_adjustment(
                crop=crop,
                strain=strain,
                planned_plants=plants,
                updated_by=self.auth_name or self.auth_email or "QCC Reflex User",
            )
            updated = dict(self.cultivation_fresh_frozen_adjustments)
            updated[f"{crop.casefold()}|{normalized_strain(strain)}"] = plants
            self.cultivation_fresh_frozen_adjustments = updated
            self.cultivation_clone_plan_message = (
                f"{crop} {strain}: planned Fresh Frozen updated to {plants} plants."
            )
        except Exception as error:
            self.cultivation_clone_plan_error = (
                "Fresh Frozen plan could not be saved: " + str(error)
            )
        finally:
            self.cultivation_fresh_frozen_saving = False

    @rx.event
    def save_cultivation_creative_use_lbs(
        self,
        crop: str,
        strain: str,
        maximum_lbs: float,
        value: str,
    ):
        """Save dry flower intentionally redirected to blends or co-packing."""
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = ""
        try:
            pounds = max(0.0, round(float(value or 0), 1))
        except (TypeError, ValueError):
            self.cultivation_clone_plan_error = (
                "Creative Use must be entered as a number of pounds."
            )
            return
        maximum = max(0.0, float(maximum_lbs or 0))
        if pounds > maximum + 0.05:
            self.cultivation_clone_plan_error = (
                f"Creative Use cannot exceed the {maximum:.1f} lb remaining after "
                f"Fresh Frozen for {strain} in {crop}."
            )
            return
        self.cultivation_fresh_frozen_saving = True
        try:
            save_creative_use_adjustment(
                crop=crop,
                strain=strain,
                reduction_lbs=pounds,
                updated_by=self.auth_name or self.auth_email or "QCC Reflex User",
            )
            updated = dict(self.cultivation_creative_use_adjustments)
            updated[f"{crop.casefold()}|{normalized_strain(strain)}"] = pounds
            self.cultivation_creative_use_adjustments = updated
            self.cultivation_clone_plan_message = (
                f"{crop} {strain}: Creative Use updated to {pounds:.1f} lb."
            )
        except Exception as error:
            self.cultivation_clone_plan_error = (
                "Creative Use reduction could not be saved: " + str(error)
            )
        finally:
            self.cultivation_fresh_frozen_saving = False

    @rx.event
    def edit_cultivation_clone_plan_history(self, plan_id: str):
        plan = next(
            (
                row for row in self.cultivation_clone_plan_history
                if str(row.get("plan_id", "")) == str(plan_id)
            ),
            None,
        )
        if plan is None:
            self.cultivation_clone_plan_error = "That saved clone plan could not be found."
            return
        self.cultivation_historical_plan_edit_id = str(plan.get("plan_id", ""))
        self.cultivation_historical_plan_edit_status = str(
            plan.get("status", "Approved") or "Approved"
        )
        self.cultivation_historical_plan_editing = True
        self.cultivation_historical_plan_crop = str(plan.get("crop", ""))
        self.cultivation_historical_plan_allocations = {
            str(strain): float(value or 0)
            for strain, value in dict(plan.get("allocations") or {}).items()
            if float(value or 0) > 0
        }
        self.cultivation_historical_plan_entry_version += 1
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = (
            f"Editing {self.cultivation_historical_plan_crop}. Save changes below to replace its saved allocations."
        )

    @rx.event
    def edit_cultivation_historical_lookback(self, crop: str):
        valid = {period["crop"] for period in prior_clone_planning_periods(8)}
        if crop not in valid:
            self.cultivation_clone_plan_error = "That historical crop is not editable."
            return
        plan = next(
            (
                row for row in self.cultivation_clone_plan_history
                if str(row.get("crop", "")) == crop
                and str(row.get("status", "")).casefold() == "approved"
            ),
            None,
        )
        allocations = self._clone_plan_allocations_by_crop().get(crop, {})
        self.cultivation_historical_plan_crop = crop
        self.cultivation_historical_plan_edit_id = (
            str(plan.get("plan_id", "")) if plan else ""
        )
        self.cultivation_historical_plan_edit_status = "Approved"
        self.cultivation_historical_plan_allocations = {
            str(strain): float(value or 0)
            for strain, value in allocations.items()
            if float(value or 0) > 0
        }
        self.cultivation_historical_plan_editing = True
        self.cultivation_historical_plan_entry_version += 1
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = (
            f"Editing {crop} directly in the historical lookback column."
        )

    @rx.event
    def cancel_cultivation_clone_plan_history_edit(self):
        self.cultivation_historical_plan_edit_id = ""
        self.cultivation_historical_plan_edit_status = "Approved"
        self.cultivation_historical_plan_allocations = {}
        self.cultivation_historical_plan_crop = ""
        self.cultivation_historical_plan_editing = False
        self.cultivation_historical_plan_entry_version += 1
        self.cultivation_clone_plan_message = ""

    @rx.event
    def save_cultivation_historical_plan(self):
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = ""
        allocations = dict(self.cultivation_historical_plan_allocations)
        if not allocations:
            self.cultivation_clone_plan_error = (
                "Enter at least one historical bench allocation before saving."
            )
            return
        if not self.cultivation_historical_plan_editing:
            self.cultivation_clone_plan_error = "Choose Edit on a historical column first."
            return
        existing_plan = next(
            (
                row for row in self.cultivation_clone_plan_history
                if str(row.get("plan_id", "")) == self.cultivation_historical_plan_edit_id
            ),
            None,
        )
        period = (
            {
                "crop": str(existing_plan.get("crop", "")),
                "room": str(existing_plan.get("flower_room", "")),
                "clone_cut_date": str(existing_plan.get("clone_cut_date", "")),
            }
            if existing_plan is not None
            else next(
                (
                    row for row in prior_clone_planning_periods(8)
                    if row["crop"] == self.cultivation_historical_plan_crop
                ),
                None,
            )
        )
        if period is None:
            self.cultivation_clone_plan_error = "Select a valid historical crop."
            return
        room_capacity = round(
            sum(
                float(row.get("square_feet", 0) or 0)
                for row in self._registered_room_bench_plans(period["room"])
            ) / 185.0,
            1,
        )
        planned = round(sum(float(value or 0) for value in allocations.values()), 1)
        if planned > room_capacity + 0.05:
            self.cultivation_clone_plan_error = (
                f"{period['crop']} has {room_capacity:.1f} bench equivalents; "
                f"the historical entry currently assigns {planned:.1f}."
            )
            return
        self.cultivation_historical_plan_saving = True
        yield
        try:
            plan_id = save_clone_plan(
                crop=period["crop"],
                flower_room=period["room"],
                clone_cut_date=period["clone_cut_date"],
                demand_model=self.cultivation_clone_plan_demand_model,
                demand_product_scope=self.cultivation_clone_plan_product_scope,
                status=(
                    self.cultivation_historical_plan_edit_status
                    if existing_plan is not None else "Approved"
                ),
                allocations=allocations,
                bench_assignments=[],
                override_reason=(
                    "Planning history edit"
                    if existing_plan is not None else "Historical clone-plan backfill"
                ),
                updated_by=self.auth_name or self.auth_email or "QCC Reflex User",
            )
            self.cultivation_clone_plan_history = load_clone_plans()
            self.cultivation_clone_plan_history_loaded = True
            self.cultivation_historical_plan_allocations = {}
            self.cultivation_historical_plan_edit_id = ""
            self.cultivation_historical_plan_edit_status = "Approved"
            self.cultivation_historical_plan_crop = ""
            self.cultivation_historical_plan_editing = False
            self.cultivation_historical_plan_entry_version += 1
            self.cultivation_clone_plan_message = (
                f"Plan {plan_id} was saved and now feeds Scheduled supply."
            )
        except Exception as error:
            self.cultivation_clone_plan_error = (
                "Historical clone plan could not be saved: " + str(error)
            )
        finally:
            self.cultivation_historical_plan_saving = False

    def _clone_plan_save(self, status: str) -> str:
        period = self._current_clone_period()
        return save_clone_plan(
            crop=period["crop"],
            flower_room=period["room"],
            clone_cut_date=period["clone_cut_date"],
            demand_model=self.cultivation_clone_plan_demand_model,
            demand_product_scope=self.cultivation_clone_plan_product_scope,
            status=status,
            allocations=dict(self.cultivation_clone_plan_allocations),
            bench_assignments=[],
            override_reason=self.cultivation_clone_plan_override_reason,
            updated_by=self.auth_name or self.auth_email or "QCC Reflex User",
        )

    @rx.event
    def save_cultivation_clone_plan_draft(self):
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = ""
        if not self.cultivation_clone_plan_allocations:
            self.cultivation_clone_plan_error = "Enter at least one clone allocation before saving."
            return
        capacity_error = self._clone_plan_capacity_error()
        if capacity_error:
            self.cultivation_clone_plan_error = capacity_error
            return
        if not self.cultivation_clone_plan_editable:
            self.cultivation_clone_plan_error = (
                "This clone-cut week is locked. An admin override and reason are required."
            )
            return
        self.cultivation_clone_plan_saving = True
        yield
        try:
            plan_id = self._clone_plan_save("Draft")
            self.cultivation_clone_plan_status = "Draft"
            self.cultivation_clone_plan_dirty = False
            self.cultivation_clone_plan_message = f"Draft {plan_id} was saved."
            self.cultivation_clone_plan_history = load_clone_plans()
            self.cultivation_clone_plan_history_loaded = True
        except Exception as error:
            self.cultivation_clone_plan_error = "Clone plan could not be saved: " + str(error)
        finally:
            self.cultivation_clone_plan_saving = False

    @rx.event
    def send_clone_plan_to_bench_allocation(self):
        if not self.cultivation_clone_plan_allocations:
            self.cultivation_clone_plan_error = "Enter at least one clone allocation first."
            return
        capacity_error = self._clone_plan_capacity_error()
        if capacity_error:
            self.cultivation_clone_plan_error = capacity_error
            return
        period = self._current_clone_period()
        self.cultivation_flower_room = period["room"]
        self.cultivation_cycle_name = period["crop"]
        self.cultivation_flower_entry_date = period["flower_entry_date"]
        self.cultivation_bench_plans = self._registered_room_bench_plans(
            period["room"], self.cultivation_plant_density
        )
        self.cultivation_view = "clone_allocation"
        self.cultivation_error = ""
        self.cultivation_message = (
            f"{period['crop']} is ready. Assign its planned strains to exact benches, "
            "then return to Clone Planning to approve it."
        )

    @rx.event
    def approve_cultivation_clone_plan(self):
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = ""
        if not self.cultivation_clone_plan_allocations:
            self.cultivation_clone_plan_error = "Enter at least one clone allocation first."
            return
        capacity_error = self._clone_plan_capacity_error()
        if capacity_error:
            self.cultivation_clone_plan_error = capacity_error
            return
        if not self.cultivation_clone_plan_editable:
            self.cultivation_clone_plan_error = (
                "This clone-cut week is locked. An admin override and reason are required."
            )
            return
        self.cultivation_clone_plan_saving = True
        yield
        try:
            plan_id = self._clone_plan_save("Approved")
            self.cultivation_clone_plan_status = "Approved"
            self.cultivation_clone_plan_dirty = False
            self.cultivation_clone_plan_message = (
                f"{plan_id} was approved and replaced the prior saved version for this crop. "
                f"It is now available in the {period['crop']} Clone Allocation Plan."
            )
            self.cultivation_clone_plan_history = load_clone_plans()
            self.cultivation_clone_plan_history_loaded = True
        except Exception as error:
            self.cultivation_clone_plan_error = "Clone plan could not be approved: " + str(error)
        finally:
            self.cultivation_clone_plan_saving = False

    @rx.event
    def load_current_clone_plan(self, plan_id: str):
        period = self._current_clone_period()
        plan = next(
            (
                row for row in self.cultivation_clone_plan_history
                if str(row.get("plan_id", "")) == str(plan_id)
                and str(row.get("crop", "")) == period["crop"]
            ),
            None,
        )
        if plan is None:
            self.cultivation_clone_plan_error = "That current plan could not be loaded."
            return
        self.cultivation_clone_plan_allocations = {
            str(strain): float(value or 0)
            for strain, value in dict(plan.get("allocations") or {}).items()
            if float(value or 0) > 0
        }
        self.cultivation_clone_plan_demand_model = self._normalized_clone_demand_model(
            plan.get("demand_model", "Availability-Adjusted")
        )
        self.cultivation_clone_plan_product_scope = (
            self._normalized_clone_demand_product_scope(
                plan.get("demand_product_scope", "Flower + Pre-Rolls")
            )
        )
        self.cultivation_clone_plan_status = str(plan.get("status", "Approved"))
        self.cultivation_clone_plan_dirty = False
        self.cultivation_clone_plan_entry_version += 1
        self.cultivation_clone_plan_error = ""
        self.cultivation_clone_plan_message = (
            f"{period['crop']} was loaded into the Rolling Clone Planner. "
            "Make changes and click Approve Plan to replace the saved version."
        )

    @rx.event
    def load_approved_clone_plan_to_allocation(self, plan_id: str):
        plan = next(
            (
                row for row in self.cultivation_clone_plan_history
                if str(row.get("plan_id", "")) == str(plan_id)
                and str(row.get("status", "")).casefold() == "approved"
            ),
            None,
        )
        if plan is None:
            self.cultivation_error = "That approved plan could not be loaded."
            return
        clone_cut = date.fromisoformat(str(plan.get("clone_cut_date", "")))
        self.cultivation_clone_plan_allocations = {
            str(strain): float(value or 0)
            for strain, value in dict(plan.get("allocations") or {}).items()
            if float(value or 0) > 0
        }
        self.cultivation_clone_plan_demand_model = self._normalized_clone_demand_model(
            plan.get("demand_model", "Availability-Adjusted")
        )
        self.cultivation_clone_plan_product_scope = (
            self._normalized_clone_demand_product_scope(
                plan.get("demand_product_scope", "Flower + Pre-Rolls")
            )
        )
        self.cultivation_clone_plan_status = "Approved"
        self.cultivation_clone_plan_dirty = False
        self.cultivation_flower_room = str(plan.get("flower_room", ""))
        self.cultivation_cycle_name = str(plan.get("crop", ""))
        self.cultivation_flower_entry_date = (clone_cut + timedelta(days=40)).isoformat()
        stored_benches = [
            dict(row) for row in list(plan.get("bench_assignments") or [])
            if isinstance(row, dict) and str(row.get("bench", "")).strip()
        ]
        if stored_benches:
            settings = stored_benches[0]
            self.cultivation_plant_density = round(
                min(2.0, max(0.1, float(
                    settings.get("saved_plant_density", self.cultivation_plant_density)
                    or self.cultivation_plant_density
                ))),
                2,
            )
            self.cultivation_overage_percent = min(
                30,
                max(25, int(
                    settings.get("saved_overage_percent", self.cultivation_overage_percent)
                    or self.cultivation_overage_percent
                )),
            )
            self.cultivation_post_harvest_days = max(
                0,
                int(
                    settings.get(
                        "saved_post_harvest_days",
                        self.cultivation_post_harvest_days,
                    ) or self.cultivation_post_harvest_days
                ),
            )
        defaults = {
            str(row.get("bench", "")): dict(row)
            for row in self._registered_room_bench_plans(
                self.cultivation_flower_room, self.cultivation_plant_density
            )
        }
        if stored_benches:
            restored: list[BenchPlan] = []
            stored_by_name = {
                str(row.get("bench", "")): row for row in stored_benches
            }
            for bench_name, default in defaults.items():
                merged = dict(default)
                merged.update(stored_by_name.get(bench_name, {}))
                restored.append(merged)  # type: ignore[arg-type]
            self.cultivation_bench_plans = restored
        else:
            self.cultivation_bench_plans = list(defaults.values())  # type: ignore[assignment]
        self.cultivation_layout_editing = False
        self.cultivation_error = ""
        self.cultivation_message = (
            f"{self.cultivation_cycle_name}'s saved room bench map is loaded and ready to print."
            if stored_benches
            else f"Approved plan {self.cultivation_cycle_name} is loaded. "
            "Assign its strains to the exact physical benches, then print and save it."
        )

    @rx.event
    def save_and_print_cultivation_clone_plan(self):
        """Replace the approved current plan with its finalized physical map."""
        self.cultivation_error = ""
        self.cultivation_message = ""
        if not self.cultivation_strain_summary_rows:
            self.cultivation_error = "Assign at least one strain before saving the room map."
            return
        if self.cultivation_unbalanced_benches:
            self.cultivation_error = (
                "Correct bench percentages before saving: "
                + self.cultivation_unbalanced_benches
            )
            return
        allocations = exact_bench_allocations(
            [dict(row) for row in self.cultivation_bench_plans]
        )
        if not allocations:
            self.cultivation_error = "The room map does not contain a valid strain allocation."
            return
        self.cultivation_saving = True
        yield
        try:
            saved_benches = [
                {
                    **dict(bench),
                    "saved_plant_density": self.cultivation_plant_density,
                    "saved_overage_percent": self.cultivation_overage_percent,
                    "saved_post_harvest_days": self.cultivation_post_harvest_days,
                }
                for bench in self.cultivation_bench_plans
            ]
            plan_id = save_clone_plan(
                crop=self.cultivation_cycle_name,
                flower_room=self.cultivation_flower_room,
                clone_cut_date=self.cultivation_cut_date,
                demand_model=self.cultivation_clone_plan_demand_model,
                demand_product_scope=self.cultivation_clone_plan_product_scope,
                status="Approved",
                allocations=allocations,
                bench_assignments=saved_benches,
                override_reason="Finalized room bench map",
                updated_by=self.auth_name or self.auth_email or "QCC Reflex User",
            )
            self.cultivation_clone_plan_allocations = allocations
            self.cultivation_clone_plan_history = load_clone_plans()
            self.cultivation_clone_plan_history_loaded = True
            self.cultivation_clone_plan_dirty = False
            self.cultivation_message = (
                f"{self.cultivation_cycle_name} was saved with its exact bench map "
                f"and replaced approved plan {plan_id}. Printing is ready."
            )
            yield rx.call_script(
                "(() => {"
                "const c='qcc-print-clone-dome';"
                "const done=()=>document.body.classList.remove(c);"
                "document.body.classList.add(c);"
                "window.addEventListener('afterprint',done,{once:true});"
                "window.print();window.setTimeout(done,3000);"
                "})()"
            )
        except Exception as error:
            self.cultivation_error = "Clone plan could not be saved and printed: " + str(error)
        finally:
            self.cultivation_saving = False

    @rx.event
    def change_cultivation_flower_room(self, value: str):
        self.cultivation_flower_room = value
        self.cultivation_bench_plans = self._registered_room_bench_plans(
            value, self.cultivation_plant_density
        )
        self.cultivation_allocations = []
        self.cultivation_layout_editing = False
        self.cultivation_error = ""
        self.cultivation_message = f"{value}'s default bench layout is ready."

    @rx.event
    def change_cultivation_cycle_name(self, value: str):
        self.cultivation_cycle_name = value

    @rx.event
    def change_cultivation_flower_entry_date(self, value: str):
        self.cultivation_flower_entry_date = value

    @rx.event
    def change_cultivation_post_harvest_days(self, value: str):
        digits = re.sub(r"[^0-9]", "", str(value))
        self.cultivation_post_harvest_days = max(0, int(digits or 30))

    @rx.event
    def toggle_cultivation_layout_editing(self):
        self.cultivation_layout_editing = not self.cultivation_layout_editing
        self.cultivation_message = (
            "Room dimensions can now be edited."
            if self.cultivation_layout_editing
            else "Room layout locked."
        )

    @rx.event
    def reset_cultivation_room_layout(self):
        self.cultivation_bench_plans = self._registered_room_bench_plans(
            self.cultivation_flower_room, self.cultivation_plant_density
        )
        self.cultivation_allocations = []
        self.cultivation_error = ""
        self.cultivation_message = "The confirmed default room layout was restored."

    def _update_cultivation_bench(
        self, bench_label: str, updates: dict[str, Any]
    ) -> None:
        refreshed: list[BenchPlan] = []
        for current in self.cultivation_bench_plans:
            row = dict(current)
            if str(row.get("bench", "")) == str(bench_label):
                row.update(updates)
                length = max(0.0, float(row.get("length", 0) or 0))
                width = max(0.0, float(row.get("width", 0) or 0))
                square_feet = round(length * width, 1)
                row["square_feet"] = square_feet
                row["target_plants"] = bench_plant_capacity(
                    square_feet, self.cultivation_plant_density
                )
            refreshed.append(row)  # type: ignore[arg-type]
        self.cultivation_bench_plans = refreshed

    @rx.event
    def change_cultivation_bench_dimension(
        self, bench_label: str, dimension: str, value: str
    ):
        try:
            parsed = max(0.0, float(value or 0))
        except (TypeError, ValueError):
            parsed = 0.0
        if dimension not in {"length", "width"}:
            return
        self._update_cultivation_bench(bench_label, {dimension: parsed})

    @rx.event
    def change_cultivation_bench_strain_count(
        self, bench_label: str, value: str
    ):
        digits = re.sub(r"[^0-9]", "", str(value))
        count = min(3, max(1, int(digits or 1)))
        p1, p2, p3 = default_split_percentages(count)
        updates: dict[str, Any] = {
            "strain_count": count,
            "percent_1": p1,
            "percent_2": p2,
            "percent_3": p3,
        }
        if count < 3:
            updates["strain_3"] = ""
        if count < 2:
            updates["strain_2"] = ""
        self._update_cultivation_bench(bench_label, updates)

    @rx.event
    def change_cultivation_bench_strain(
        self, bench_label: str, slot: str, value: str
    ):
        if slot not in {"strain_1", "strain_2", "strain_3"}:
            return
        self._update_cultivation_bench(bench_label, {slot: value})

    @rx.event
    def change_cultivation_bench_percent(
        self, bench_label: str, slot: str, value: str
    ):
        if slot not in {"percent_1", "percent_2", "percent_3"}:
            return
        try:
            parsed = min(100.0, max(0.0, float(value or 0)))
        except (TypeError, ValueError):
            parsed = 0.0
        self._update_cultivation_bench(bench_label, {slot: parsed})

    @rx.event
    def change_cultivation_overage(self, value: str):
        digits = re.sub(r"[^0-9]", "", str(value))
        self.cultivation_overage_percent = min(30, max(25, int(digits or 30)))

    @rx.event
    def change_cultivation_plant_density(self, value: str):
        try:
            density = float(value or 0.75)
        except (TypeError, ValueError):
            density = 0.75
        self.cultivation_plant_density = round(min(2.0, max(0.1, density)), 2)
        refreshed: list[BenchPlan] = []
        for current in self.cultivation_bench_plans:
            row = dict(current)
            row["target_plants"] = bench_plant_capacity(
                float(row.get("square_feet", 0) or 0),
                self.cultivation_plant_density,
            )
            refreshed.append(row)  # type: ignore[arg-type]
        self.cultivation_bench_plans = refreshed
        self.cultivation_message = (
            f"Plant counts, clone cuts, and dome counts now use "
            f"{self.cultivation_plant_density:.2f} plants per sq ft."
        )

    @rx.event
    def clear_cultivation_allocations(self):
        refreshed: list[BenchPlan] = []
        for current in self.cultivation_bench_plans:
            row = dict(current)
            row.update({
                "strain_count": 1,
                "strain_1": "",
                "percent_1": 100.0,
                "strain_2": "",
                "percent_2": 0.0,
                "strain_3": "",
                "percent_3": 0.0,
            })
            refreshed.append(row)  # type: ignore[arg-type]
        self.cultivation_bench_plans = refreshed
        self.cultivation_allocations = []
        self.cultivation_error = ""
        self.cultivation_message = "Clone allocation cleared."

    @rx.event
    def load_saved_clone_allocations(self):
        try:
            self.cultivation_saved_allocations = load_clone_allocations()
            self.cultivation_saved_loaded = True
        except Exception as error:
            self.cultivation_error = (
                "Saved clone allocations could not be loaded: " + str(error)
            )

    @rx.event
    def save_cultivation_allocation(self):
        self.cultivation_error = ""
        self.cultivation_message = ""
        if not self.cultivation_cycle_name.strip():
            self.cultivation_error = "Enter a cycle or crop name before saving."
            return
        if not self.cultivation_strain_summary_rows:
            self.cultivation_error = "Allocate at least one strain before saving."
            return
        if self.cultivation_unbalanced_benches:
            self.cultivation_error = (
                "Correct bench percentages before saving: "
                + self.cultivation_unbalanced_benches
            )
            return
        self.cultivation_saving = True
        yield
        try:
            allocation_id = create_clone_allocation(
                cycle_name=self.cultivation_cycle_name,
                flower_room=self.cultivation_flower_room,
                flower_entry_date=self.cultivation_flower_entry_date,
                clone_cut_date=self.cultivation_cut_date,
                veg_transfer_date=self.cultivation_veg_transfer_date,
                harvest_date=self.cultivation_harvest_date,
                available_date=self.cultivation_available_date,
                overage_percent=self.cultivation_overage_percent,
                post_harvest_days=self.cultivation_post_harvest_days,
                bench_plans=[dict(row) for row in self.cultivation_bench_plans],
                strain_summary=[
                    dict(row) for row in self.cultivation_strain_summary_rows
                ],
                created_by=self.auth_name or self.auth_email or "QCC Reflex User",
            )
            self.cultivation_saved_allocations = load_clone_allocations()
            self.cultivation_saved_loaded = True
            self.cultivation_message = (
                f"{self.cultivation_cycle_name} was saved as facility clone "
                f"allocation {allocation_id}."
            )
        except Exception as error:
            self.cultivation_error = "Clone allocation could not be saved: " + str(error)
        finally:
            self.cultivation_saving = False

    @rx.event
    def load_cultivation_allocation(self, allocation_id: str):
        plan = next(
            (
                row for row in self.cultivation_saved_allocations
                if str(row.get("allocation_id", "")) == str(allocation_id)
            ),
            None,
        )
        if not plan:
            self.cultivation_error = "That saved clone allocation was not found."
            return
        self.cultivation_cycle_name = str(plan.get("cycle_name", ""))
        self.cultivation_flower_room = str(
            plan.get("flower_room", "Flower Room 1")
        )
        self.cultivation_flower_entry_date = str(
            plan.get("flower_entry_date", self.cultivation_flower_entry_date)
        )
        self.cultivation_overage_percent = int(
            plan.get("overage_percent", 30) or 30
        )
        self.cultivation_post_harvest_days = int(
            plan.get("post_harvest_days", DEFAULT_POST_HARVEST_DAYS)
            or DEFAULT_POST_HARVEST_DAYS
        )
        stored_benches = plan.get("bench_plans") or []
        defaults = {
            str(row.get("bench", "")): row
            for row in self._registered_room_bench_plans(
                self.cultivation_flower_room, self.cultivation_plant_density
            )
        }
        if stored_benches:
            restored: list[BenchPlan] = []
            for stored in stored_benches:
                bench_name = str(stored.get("bench", ""))
                merged = dict(defaults.get(bench_name, {}))
                merged.update(dict(stored))
                restored.append(merged)  # type: ignore[arg-type]
            self.cultivation_bench_plans = restored
        else:
            self.cultivation_bench_plans = list(defaults.values())
        self.cultivation_layout_editing = False
        self.cultivation_error = ""
        self.cultivation_message = (
            f"Loaded {self.cultivation_cycle_name} for review or reuse."
        )

    @rx.var(cache=True)
    def cultivation_cut_date(self) -> str:
        try:
            return cultivation_timeline(self.cultivation_flower_entry_date)[
                "clone_cut_date"
            ]
        except (TypeError, ValueError):
            return "—"

    @rx.var(cache=True)
    def cultivation_veg_transfer_date(self) -> str:
        try:
            return cultivation_timeline(self.cultivation_flower_entry_date)[
                "veg_transfer_date"
            ]
        except (TypeError, ValueError):
            return "—"

    @rx.var(cache=True)
    def cultivation_harvest_date(self) -> str:
        try:
            return projected_harvest_dates(
                self.cultivation_flower_entry_date,
                self.cultivation_post_harvest_days,
            )["harvest_date"]
        except (TypeError, ValueError):
            return "—"

    @rx.var(cache=True)
    def cultivation_available_date(self) -> str:
        try:
            return projected_harvest_dates(
                self.cultivation_flower_entry_date,
                self.cultivation_post_harvest_days,
            )["available_date"]
        except (TypeError, ValueError):
            return "—"

    def _cultivation_planning_strain_names(self) -> list[str]:
        values: dict[str, str] = {}

        def add(value: Any):
            label = " ".join(str(value or "").strip().split())
            key = normalized_strain(label)
            if key and key not in values:
                values[key] = label

        for strain in CLADE9_CLONE_STRAINS:
            add(strain)
        if self.cultivation_clone_strain_scope == "Clade9 + Craft Kings Strains":
            for strain in CRAFT_KINGS_CLONE_STRAINS:
                add(strain)
        for strain in self.cultivation_provisional_strains:
            add(strain)
        for strain in self.cultivation_clone_plan_allocations:
            add(strain)
        for strain in self.cultivation_historical_plan_allocations:
            add(strain)
        for plan in self.cultivation_clone_plan_history:
            for strain in dict(plan.get("allocations") or {}):
                add(strain)
        for bench in self.cultivation_bench_plans:
            for slot in range(1, 4):
                add(bench.get(f"strain_{slot}", ""))
        return sorted(values.values(), key=str.casefold)

    @rx.var(cache=True)
    def cultivation_strain_options(self) -> list[str]:
        return self._cultivation_planning_strain_names()

    @rx.var(cache=True)
    def cultivation_clone_plan_periods(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        history_count = {
            "Last 4 Crops": 4,
            "Last 8 Crops": 8,
        }.get(self.cultivation_clone_plan_lookback, 0)
        registered = sorted(
            [dict(row) for row in self._registry_payload().get("schedule", [])],
            key=lambda row: str(row.get("clone_cut_date", "")),
        )
        current_period = self._current_clone_period()
        current_index = next(
            (index for index, row in enumerate(registered)
             if str(row.get("crop")) == current_period["crop"]),
            0,
        )
        if registered:
            future_periods = registered[current_index:current_index + 13]
            historical_periods = registered[
                max(0, current_index - history_count):current_index
            ] if history_count else []
            if len(historical_periods) < history_count:
                try:
                    harvest = date.fromisoformat(current_period["harvest_date"])
                    available = date.fromisoformat(current_period["available_date"])
                    generated_history = prior_clone_planning_periods(
                        history_count,
                        first_crop=current_period["crop"],
                        first_cut_date=date.fromisoformat(
                            current_period["clone_cut_date"]
                        ),
                        post_harvest_days=max(0, (available - harvest).days),
                    )
                except (TypeError, ValueError):
                    generated_history = []
                # Workbook-era periods fill registry gaps; saved registry rows
                # win whenever both sources describe the same crop.
                combined = {
                    str(period.get("crop", "")): dict(period)
                    for period in generated_history
                }
                combined.update({
                    str(period.get("crop", "")): dict(period)
                    for period in registered[:current_index]
                })
                historical_periods = sorted(
                    combined.values(),
                    key=lambda period: str(period.get("clone_cut_date", "")),
                )[-history_count:]
            periods = [*historical_periods, *future_periods]
            actual_history_count = len(historical_periods)
        else:
            historical_periods = list(reversed(prior_clone_planning_periods(history_count))) if history_count else []
            periods = [*historical_periods, *clone_planning_periods(13)]
            actual_history_count = history_count
        for index, period in enumerate(periods):
            cut = date.fromisoformat(period["clone_cut_date"])
            harvest = date.fromisoformat(period["harvest_date"])
            rows.append({
                **period,
                "date_label": f"{cut.strftime('%b')} {cut.day}",
                "harvest_date_label": f"{harvest.strftime('%b')} {harvest.day}",
                "harvest_date_full_label": (
                    f"{harvest.strftime('%B')} {harvest.day}, {harvest.year}"
                ),
                "header": f"{period['crop']} · {cut.strftime('%b')} {cut.day}",
                "is_current": index == actual_history_count,
                "is_historical": index < actual_history_count,
            })
        return rows

    @rx.var(cache=True)
    def cultivation_clone_plan_editable(self) -> bool:
        period = self._current_clone_period()
        override_allowed = (
            self.cultivation_clone_plan_override
            and bool(self.cultivation_clone_plan_override_reason.strip())
            and self.auth_role.casefold() in {"admin", "administrator"}
        )
        return clone_plan_is_editable(
            period["clone_cut_date"], override=override_allowed
        )

    @rx.var(cache=True)
    def cultivation_clone_plan_edit_window_label(self) -> str:
        period = self._current_clone_period()
        start, end = clone_plan_edit_window(period["clone_cut_date"])
        return (
            f"Editable through {end.strftime('%b')} {end.day}, {end.year} "
            f"(clone-cut week starts {start.strftime('%b')} {start.day})."
        )

    def _clone_plan_weekly_demand_by_strain(
        self,
        demand_model: str | None = None,
        product_scope: str | None = None,
    ) -> dict[str, float]:
        totals: dict[str, float] = {}
        selected_model = demand_model or self.cultivation_clone_plan_demand_model
        selected_scope = product_scope or self.cultivation_clone_plan_product_scope
        adjusted_period = {
            "Availability-Adjusted": "All Time",
            "30-Day Availability-Adjusted": "30 Days",
            "60-Day Availability-Adjusted": "60 Days",
        }.get(selected_model)
        if adjusted_period:
            selected_window = self.availability_adjusted_velocity_windows.get(
                adjusted_period
            )
            if selected_window is None:
                # Sales history loads in the background. A newly selected window
                # can therefore be absent briefly; use the best populated demand
                # series instead of presenting every strain as zero demand.
                selected_window = (
                    self.availability_adjusted_velocity_windows.get("All Time")
                    or self.velocity
                )
            elif (
                not selected_window
                and not any(self.availability_adjusted_velocity_windows.values())
            ):
                selected_window = self.velocity
            demand_rows = selected_window
        else:
            # Keep this model independent from the SKU Planning screen's own
            # timeframe/toggle selection. It is the unadjusted all-time series.
            demand_rows = self.velocity_windows.get("All Time", self.velocity)
        for row in demand_rows:
            sku = str(row.get("SKU Type", "") or "")
            sku_lower = sku.casefold()
            is_flower = any(size in sku_lower for size in ("1g flower", "3.5g flower", "7g flower"))
            is_preroll = "pre-roll" in sku_lower or "preroll" in sku_lower
            if selected_scope == "Pre-Rolls Only":
                included = is_preroll
            elif selected_scope == "Flower Only":
                included = is_flower
            else:
                included = is_flower or is_preroll
            if not included:
                continue
            key = normalized_strain(row.get("Strain", ""))
            grams_per_unit = sku_fill_grams(sku)
            if not key or grams_per_unit <= 0:
                continue
            units = self._number(row, "Avg Weekly Units")
            totals[key] = totals.get(key, 0.0) + units * grams_per_unit / 453.59237
        return totals

    def _clone_plan_two_week_demand_by_strain(
        self,
        periods: list[dict[str, Any]],
        demand_model: str | None = None,
        product_scope: str | None = None,
    ) -> dict[str, list[float]]:
        """Return period-specific demand while preserving legacy flat models."""
        selected_model = demand_model or self.cultivation_clone_plan_demand_model
        selected_scope = product_scope or self.cultivation_clone_plan_product_scope
        if selected_model == "AI-Adjusted":
            forecast = ai_two_week_demand_forecast(
                periods=periods,
                adjusted_windows=self.availability_adjusted_velocity_windows,
                weekly_rows=self.availability_demand_weekly,
                fallback_rows=(
                    self.availability_adjusted_velocity_windows.get("All Time")
                    or self.velocity_windows.get("All Time")
                    or self.velocity
                ),
                product_scope=selected_scope,
            )
            if forecast:
                return forecast
        weekly = self._clone_plan_weekly_demand_by_strain(
            selected_model, selected_scope
        )
        return {
            strain: [
                0.0 if bool(period.get("is_historical", False)) else 2.0 * value
                for period in periods
            ]
            for strain, value in weekly.items()
        }

    def _clone_plan_actual_crop_lbs(self) -> dict[tuple[str, str], float]:
        totals: dict[tuple[str, str], float] = {}
        crop_names = {
            str(row["crop"]).casefold() for row in UPCOMING_CROP_ALLOCATIONS
        } | {crop.casefold() for crop in HISTORICAL_CLONE_ALLOCATIONS}
        crop_names.update(
            str(plan.get("crop", "") or "").casefold()
            for plan in self.cultivation_clone_plan_history
            if str(plan.get("crop", "") or "").strip()
        )
        for row in self.all_inventory:
            if not inventory_counts_as_current_cultivation_supply(row):
                continue
            evidence = " ".join(
                str(row.get(field, "") or "")
                for field in (
                    "Source Harvest Name(s)", "Source Harvest", "Source Production Batch",
                    "Production Batch Number", "Lot", "Production Batch",
                )
            ).casefold()
            crop = next((name for name in crop_names if name in evidence), "")
            strain = normalized_strain(row.get("Strain", ""))
            if not crop or not strain:
                continue
            key = (crop, strain)
            totals[key] = totals.get(key, 0.0) + max(
                0.0, self._number(row, "Calculated Weight (g)")
            ) / 453.59237
        return totals

    def _clone_plan_actual_fresh_frozen(
        self,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Return actual Fresh Frozen harvest plants keyed by crop and strain."""
        totals: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self._cultivation_plant_snapshot.get("harvests", []):
            batch = str(row.get("harvest_batch", "") or "").strip()
            is_fresh_frozen = bool(row.get("fresh_frozen")) or bool(
                re.search(r"(?:WPFF|FRESH[ _-]*FROZEN)", batch, re.IGNORECASE)
            )
            if not is_fresh_frozen:
                continue
            crop = crop_code(batch)
            strain = normalized_strain(row.get("strain", ""))
            plants = max(0, int(row.get("plants", 0) or 0))
            if not crop or not strain or plants <= 0:
                continue
            key = (crop.casefold(), strain)
            detail = totals.setdefault(key, {
                "plants": 0,
                "wet_weight_lbs": 0.0,
                "batches": [],
            })
            detail["plants"] += plants
            detail["wet_weight_lbs"] += max(
                0.0, float(row.get("wet_weight_lb", 0) or 0)
            )
            if batch and batch not in detail["batches"]:
                detail["batches"].append(batch)
        return totals

    def _clone_plan_allocations_by_crop(self) -> dict[str, dict[str, float]]:
        """Return workbook history with saved plans taking precedence."""
        allocations = {
            crop: dict(values)
            for crop, values in HISTORICAL_CLONE_ALLOCATIONS.items()
        }
        for plan in self.cultivation_clone_plan_history:
            if str(plan.get("status", "") or "").casefold() != "approved":
                continue
            crop = str(plan.get("crop", "") or "").strip()
            if crop:
                allocations[crop] = {
                    str(strain): float(value or 0)
                    for strain, value in dict(plan.get("allocations") or {}).items()
                    if float(value or 0) > 0
                }
        return allocations

    def _clone_plan_scheduled_by_period(
        self,
        periods: list[dict[str, Any]],
        *,
        exclude_current_plan: bool = True,
        post_harvest_days: int | None = None,
    ) -> tuple[dict[str, list[float]], dict[str, list[list[ScheduledSupplyDetail]]]]:
        result: dict[str, list[float]] = {}
        detail_result: dict[str, list[list[ScheduledSupplyDetail]]] = {}
        actual = self._clone_plan_actual_crop_lbs()
        actual_fresh_frozen = self._clone_plan_actual_fresh_frozen()
        today = date.today()

        def add_projection(
            crop_name: str,
            room: str,
            harvest_text: str,
            strain: str,
            square_feet: float,
        ) -> None:
            harvest = date.fromisoformat(harvest_text)
            availability_days = (
                self.cultivation_post_harvest_days
                if post_harvest_days is None
                else max(0, int(post_harvest_days))
            )
            available = harvest + timedelta(days=availability_days)
            distances = [
                abs((date.fromisoformat(period["clone_cut_date"]) - available).days)
                for period in periods
            ]
            if not distances:
                return
            position = min(range(len(distances)), key=distances.__getitem__)
            if distances[position] > 7:
                return
            strain_key = normalized_strain(strain)
            gross = estimated_yield_pounds(square_feet, strain, room)
            planted_plants = bench_plant_capacity(square_feet)
            adjustment_key = f"{crop_name.casefold()}|{strain_key}"
            planned_fresh_frozen = int(
                self.cultivation_fresh_frozen_adjustments.get(
                    adjustment_key, 0
                ) or 0
            )
            actual_fresh_frozen_detail = actual_fresh_frozen.get(
                (crop_name.casefold(), strain_key)
            )
            reconciliation = scheduled_supply_reconciliation(
                gross,
                planted_plants,
                planned_fresh_frozen,
                actual.get((crop_name.casefold(), strain_key), 0.0),
                harvest,
                today,
                SCHEDULED_SUPPLY_EXPIRY_DAYS,
                creative_use_reduction_lbs=float(
                    self.cultivation_creative_use_adjustments.get(
                        adjustment_key, 0
                    ) or 0
                ),
                actual_fresh_frozen_plants=(
                    int(actual_fresh_frozen_detail.get("plants", 0) or 0)
                    if actual_fresh_frozen_detail is not None
                    else None
                ),
            )
            result.setdefault(strain_key, [0.0] * len(periods))[position] += (
                reconciliation["forecast_counted_lbs"]
            )
            detail_result.setdefault(strain_key, [[] for _ in periods])[position].append({
                "crop": crop_name,
                "room": room,
                "strain": strain,
                "harvest_date": harvest.isoformat(),
                "available_date": available.isoformat(),
                **reconciliation,
                "actual_fresh_frozen_wet_lbs": round(
                    float(
                        (actual_fresh_frozen_detail or {}).get(
                            "wet_weight_lbs", 0
                        ) or 0
                    ),
                    1,
                ),
                "actual_fresh_frozen_batches": ", ".join(
                    (actual_fresh_frozen_detail or {}).get("batches", [])
                ),
                "can_edit_fresh_frozen": (
                    today <= harvest
                    and not reconciliation["actual_fresh_frozen_detected"]
                ),
                "can_edit_creative_use": (
                    not reconciliation["expired"]
                    and not reconciliation["actual_detected"]
                ),
            })

        grouped: dict[tuple[str, str, str, str], float] = {}
        for crop in UPCOMING_CROP_ALLOCATIONS:
            group_key = (
                str(crop["crop"]),
                str(crop["room"]),
                str(crop["harvest_date"]),
                normalized_strain(crop["strain"]),
            )
            grouped[group_key] = grouped.get(group_key, 0.0) + float(crop["square_feet"])

        for (crop_name, room, harvest_date, strain_key), square_feet in grouped.items():
            add_projection(
                crop_name, room, harvest_date, strain_key, square_feet
            )

        known_crops = {str(row["crop"]).casefold() for row in UPCOMING_CROP_ALLOCATIONS}
        plan_periods = {
            str(row.get("crop", "")): dict(row)
            for row in self._registry_payload().get("schedule", [])
            if str(row.get("crop", ""))
        }
        plan_periods.update({
            row["crop"]: row for row in prior_clone_planning_periods(8)
            if row["crop"] not in plan_periods
        })
        saved_by_crop = {
            str(plan.get("crop", "") or "").strip(): plan
            for plan in self.cultivation_clone_plan_history
            if str(plan.get("status", "") or "").casefold() == "approved"
        }
        for crop_name, allocations in self._clone_plan_allocations_by_crop().items():
            if (
                crop_name.casefold() in known_crops
                or (
                    exclude_current_plan
                    and crop_name == self._current_clone_period()["crop"]
                )
            ):
                continue
            plan = saved_by_crop.get(crop_name, {})
            period = plan_periods.get(crop_name)
            if not plan and period is None:
                continue
            try:
                cut = date.fromisoformat(
                    str(plan.get("clone_cut_date", ""))
                    if plan else str(period["clone_cut_date"])
                )
            except ValueError:
                continue
            room = str(
                plan.get("flower_room", "") if plan else period.get("room", "")
            )
            harvest = cut + timedelta(days=40 + 68)
            for strain, bench_value in allocations.items():
                benches = float(bench_value or 0)
                strain_key = normalized_strain(strain)
                if benches <= 0 or not strain_key:
                    continue
                add_projection(
                    crop_name, room, harvest.isoformat(), strain, benches * 185.0
                )
        return result, detail_result

    @rx.var(cache=True)
    def cultivation_clone_plan_lookback_rows(self) -> list[dict[str, Any]]:
        if self.cultivation_clone_plan_lookback == "No Historical Crops":
            return []
        limit = 8 if self.cultivation_clone_plan_lookback == "Last 8 Crops" else 4
        current_crop = self._current_clone_period()["crop"]
        historical = [
            row for row in self.cultivation_clone_plan_history
            if str(row.get("crop", "")) != current_crop
        ]
        return historical[:limit]

    @rx.var(cache=True)
    def cultivation_current_plan_history_rows(self) -> list[dict[str, Any]]:
        current_crop = self._current_clone_period()["crop"]
        return [
            row for row in self.cultivation_clone_plan_history
            if str(row.get("crop", "")) == current_crop
        ]

    @rx.var(cache=True)
    def cultivation_approved_current_plan_rows(self) -> list[dict[str, Any]]:
        current_crop = self._current_clone_period()["crop"]
        rows: list[dict[str, Any]] = []
        for plan in self.cultivation_clone_plan_history:
            if (
                str(plan.get("crop", "")) != current_crop
                or str(plan.get("status", "")).casefold() != "approved"
            ):
                continue
            try:
                flower_entry = date.fromisoformat(
                    str(plan.get("clone_cut_date", ""))
                ) + timedelta(days=40)
                flower_entry_text = flower_entry.isoformat()
            except ValueError:
                flower_entry_text = ""
            rows.append({
                **plan,
                "flower_entry_date": flower_entry_text,
            })
        return rows

    @rx.var(cache=True)
    def cultivation_current_clone_plan_title(self) -> str:
        crop = self.cultivation_cycle_name.strip() or self._current_clone_period()["crop"]
        return f"{crop} Clone Allocation Plan"

    @rx.var(cache=True)
    def cultivation_historical_plan_crop_options(self) -> list[str]:
        options = [period["crop"] for period in prior_clone_planning_periods(8)]
        if (
            self.cultivation_historical_plan_edit_id
            and self.cultivation_historical_plan_crop not in options
        ):
            options.insert(0, self.cultivation_historical_plan_crop)
        return options

    @rx.var(cache=True)
    def cultivation_historical_plan_total(self) -> str:
        total = sum(
            float(value or 0)
            for value in self.cultivation_historical_plan_allocations.values()
        )
        return f"{total:.1f} benches"

    @rx.var(cache=True)
    def cultivation_historical_plan_entry_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "strain": strain,
                "allocation": float(
                    self.cultivation_historical_plan_allocations.get(strain, 0.0) or 0.0
                ),
            }
            for strain in self.cultivation_strain_options
        ]

    @rx.var(cache=True)
    def cultivation_clone_plan_matrix_rows(self) -> list[ClonePlanMatrixRow]:
        # These direct reads are intentional. Reflex cache dependency tracking
        # cannot see state accessed only inside the private demand helper.
        _ = self.cultivation_clone_plan_demand_revision
        _ = self.cultivation_clone_plan_demand_model
        _ = self.cultivation_clone_plan_product_scope
        _ = self.cultivation_clone_plan_include_pre_wip
        _ = self.velocity
        _ = self.velocity_windows
        _ = self.availability_adjusted_velocity_windows
        _ = self.availability_demand_weekly
        periods = self.cultivation_clone_plan_periods
        current_breakdown = self._cultivation_current_inventory_breakdown_by_strain()
        current = {
            key: float(values.get("total_lbs", 0) or 0)
            for key, values in current_breakdown.items()
        }
        two_week_demand = self._clone_plan_two_week_demand_by_strain(periods)
        scheduled, scheduled_details = self._clone_plan_scheduled_by_period(periods)
        historical_allocations = self._clone_plan_allocations_by_crop()
        actual_crop_lbs = self._clone_plan_actual_crop_lbs()
        actual_fresh_frozen = self._clone_plan_actual_fresh_frozen()
        plan_period = self._current_clone_period()
        plan_available = date.fromisoformat(plan_period["available_date"])
        plan_bucket = min(
            range(len(periods)),
            key=lambda index: abs(
                (
                    date.fromisoformat(periods[index]["clone_cut_date"])
                    - plan_available
                ).days
            ),
        )
        strains = self._cultivation_planning_strain_names()
        rows: list[ClonePlanMatrixRow] = []

        def matrix_value(
            value: float,
            *,
            highlight: bool = False,
            show_breakdown: bool = False,
            breakdown: dict[str, float] | None = None,
            available: bool = True,
            editable_allocation: bool = False,
            historical_allocation: bool = False,
            historical_editable: bool = False,
            crop: str = "",
            details: list[ScheduledSupplyDetail] | None = None,
        ) -> dict[str, Any]:
            detail = breakdown or {}
            return {
                "value": value,
                "highlight": highlight,
                "show_breakdown": show_breakdown,
                "cpg_lbs": round(float(detail.get("cpg_lbs", 0) or 0), 1),
                "wip_lbs": round(float(detail.get("wip_lbs", 0) or 0), 1),
                "pre_wip_lbs": round(
                    float(detail.get("pre_wip_lbs", 0) or 0), 1
                ),
                "current_total_lbs": round(float(detail.get("total_lbs", 0) or 0), 1),
                "available": available,
                "editable_allocation": editable_allocation,
                "historical_allocation": historical_allocation,
                "historical_editable": historical_editable,
                "crop": crop,
                "scheduled_details": list(details or []),
            }

        for strain in strains:
            key = normalized_strain(strain)
            breakdown = current_breakdown.get(key, {})
            demand_values = list(
                two_week_demand.get(key, [0.0] * len(periods))
            )
            if len(demand_values) < len(periods):
                demand_values.extend([0.0] * (len(periods) - len(demand_values)))
            weekly = next(
                (
                    demand_values[index] / 2.0
                    for index, period in enumerate(periods)
                    if not bool(period.get("is_historical", False))
                ),
                0.0,
            )
            scheduled_values = list(scheduled.get(key, [0.0] * len(periods)))
            strain_details = scheduled_details.get(key, [[] for _ in periods])
            allocation = float(self.cultivation_clone_plan_allocations.get(strain, 0.0) or 0.0)
            if allocation > 0:
                square_feet = allocation * 185.0
                gross = estimated_yield_pounds(
                    square_feet, strain, plan_period["room"]
                )
                harvest = date.fromisoformat(plan_period["harvest_date"])
                planted_plants = bench_plant_capacity(square_feet)
                adjustment_key = f"{plan_period['crop'].casefold()}|{key}"
                actual_fresh_frozen_detail = actual_fresh_frozen.get(
                    (plan_period["crop"].casefold(), key)
                )
                reconciliation = scheduled_supply_reconciliation(
                    gross,
                    planted_plants,
                    int(self.cultivation_fresh_frozen_adjustments.get(adjustment_key, 0) or 0),
                    actual_crop_lbs.get((plan_period["crop"].casefold(), key), 0.0),
                    harvest,
                    date.today(),
                    SCHEDULED_SUPPLY_EXPIRY_DAYS,
                    creative_use_reduction_lbs=float(
                        self.cultivation_creative_use_adjustments.get(
                            adjustment_key, 0
                        ) or 0
                    ),
                    actual_fresh_frozen_plants=(
                        int(actual_fresh_frozen_detail.get("plants", 0) or 0)
                        if actual_fresh_frozen_detail is not None
                        else None
                    ),
                )
                scheduled_values[plan_bucket] += reconciliation["forecast_counted_lbs"]
                strain_details[plan_bucket] = [
                    *strain_details[plan_bucket],
                    {
                        "crop": plan_period["crop"],
                        "room": plan_period["room"],
                        "strain": strain,
                        "harvest_date": plan_period["harvest_date"],
                        "available_date": plan_period["available_date"],
                        **reconciliation,
                        "actual_fresh_frozen_wet_lbs": round(
                            float(
                                (actual_fresh_frozen_detail or {}).get(
                                    "wet_weight_lbs", 0
                                ) or 0
                            ),
                            1,
                        ),
                        "actual_fresh_frozen_batches": ", ".join(
                            (actual_fresh_frozen_detail or {}).get("batches", [])
                        ),
                        "can_edit_fresh_frozen": (
                            date.today() <= harvest
                            and not reconciliation["actual_fresh_frozen_detected"]
                        ),
                        "can_edit_creative_use": (
                            not reconciliation["expired"]
                            and not reconciliation["actual_detected"]
                        ),
                    },
                ]
            balance = max(0.0, current.get(key, 0.0))
            balance_values: list[float] = []
            for index, supply in enumerate(scheduled_values):
                if bool(periods[index].get("is_historical", False)):
                    balance_values.append(0.0)
                    continue
                balance_values.append(round(max(0.0, balance), 1))
                balance = max(0.0, balance + supply - demand_values[index])

            allocation_values: list[dict[str, Any]] = []
            for period in periods:
                is_historical = bool(period.get("is_historical", False))
                is_historical_editable = (
                    is_historical
                    and self.cultivation_historical_plan_editing
                    and self.cultivation_historical_plan_crop == str(period["crop"])
                )
                historical_value = next(
                    (
                        float(value or 0)
                        for label, value in (
                            self.cultivation_historical_plan_allocations
                            if is_historical_editable
                            else historical_allocations.get(str(period["crop"]), {})
                        ).items()
                        if normalized_strain(label) == key
                    ),
                    0.0,
                )
                allocation_values.append(matrix_value(
                    historical_value if is_historical else (
                        allocation if bool(period.get("is_current", False)) else 0.0
                    ),
                    available=is_historical or bool(period.get("is_current", False)),
                    editable_allocation=bool(period.get("is_current", False)),
                    historical_allocation=is_historical,
                    historical_editable=is_historical_editable,
                    crop=str(period["crop"]),
                ))
            rows.extend([
                {
                    "strain": strain, "metric": "Clone Allocation",
                    "allocation": allocation,
                    "weekly_demand": weekly,
                    "values": allocation_values,
                },
                {
                    "strain": strain, "metric": "Current Pounds",
                    "allocation": 0.0, "weekly_demand": weekly,
                    "values": [
                        matrix_value(
                            value,
                            show_breakdown=bool(periods[index].get("is_current", False)),
                            breakdown=breakdown,
                            available=not bool(periods[index].get("is_historical", False)),
                        )
                        for index, value in enumerate(balance_values)
                    ],
                },
                {
                    "strain": strain, "metric": "Scheduled",
                    "allocation": 0.0,
                    "weekly_demand": weekly,
                    "values": [
                        matrix_value(
                            round(value, 1),
                            highlight=bool(
                                self.cultivation_clone_plan_dirty
                                and allocation > 0
                                and index == plan_bucket
                            ),
                            available=not bool(periods[index].get("is_historical", False)),
                            details=strain_details[index],
                        )
                        for index, value in enumerate(scheduled_values)
                    ],
                },
                {
                    "strain": strain, "metric": "Two-Week Demand",
                    "allocation": 0.0,
                    "weekly_demand": weekly,
                    "values": [
                        matrix_value(
                            round(demand_values[index], 1),
                            available=not bool(period.get("is_historical", False)),
                        )
                        for index, period in enumerate(periods)
                    ],
                },
            ])
        return rows

    @rx.var(cache=True)
    def cultivation_clone_plan_total_benches(self) -> str:
        total = sum(float(value or 0) for value in self.cultivation_clone_plan_allocations.values())
        return f"{total:.1f} benches"

    @rx.var(cache=True)
    def cultivation_clone_plan_room_capacity(self) -> str:
        period = self._current_clone_period()
        equivalents = sum(
            float(row.get("square_feet", 0) or 0)
            for row in self._registered_room_bench_plans(period["room"])
        ) / 185.0
        return f"{equivalents:.1f} benches"

    @rx.var(cache=True)
    def cultivation_clone_plan_total_clones(self) -> str:
        plants = bench_plant_capacity(
            sum(float(value or 0) for value in self.cultivation_clone_plan_allocations.values()) * 185.0
        )
        recommendation = recommend_clone_trays(plants, self.cultivation_overage_percent)
        return f"{recommendation['recommended_clones']:,} clones · {recommendation['trays']} trays"

    def _cultivation_allocation_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for bench in self.cultivation_bench_plans:
            count = min(3, max(1, int(bench.get("strain_count", 1) or 1)))
            target = int(bench.get("target_plants", 0) or 0)
            square_feet = float(bench.get("square_feet", 0) or 0)
            for index in range(1, count + 1):
                strain = str(bench.get(f"strain_{index}", "") or "").strip()
                percent = float(bench.get(f"percent_{index}", 0) or 0)
                if not strain or percent <= 0:
                    continue
                records.append({
                    "bench": str(bench.get("bench", "")),
                    "strain": strain,
                    "percent": round(percent, 1),
                    "square_feet": round(square_feet * percent / 100, 1),
                    "target_plants": max(0, math.floor((target * percent / 100) + 0.5)),
                })
        return records

    @rx.var(cache=True)
    def cultivation_unbalanced_benches(self) -> str:
        problems: list[str] = []
        for bench in self.cultivation_bench_plans:
            count = min(3, max(1, int(bench.get("strain_count", 1) or 1)))
            total = sum(
                float(bench.get(f"percent_{index}", 0) or 0)
                for index in range(1, count + 1)
            )
            selected = all(
                str(bench.get(f"strain_{index}", "") or "").strip()
                for index in range(1, count + 1)
            )
            if selected and abs(total - 100) > 0.1:
                problems.append(f"{bench.get('bench', '')} ({total:.0f}%)")
        return ", ".join(problems)

    @rx.var(cache=True)
    def cultivation_allocated_bench_count(self) -> str:
        benches = {row["bench"] for row in self._cultivation_allocation_records()}
        return f"{len(benches)} of {len(self.cultivation_bench_plans)} benches"

    @rx.var(cache=True)
    def cultivation_room_square_feet(self) -> str:
        total = sum(float(row.get("square_feet", 0) or 0) for row in self.cultivation_bench_plans)
        return f"{total:,.1f} sq ft"

    @rx.var(cache=True)
    def cultivation_strain_summary_rows(self) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        bench_order = {
            str(row.get("bench", "")): position
            for position, row in enumerate(self.cultivation_bench_plans)
        }
        for row in self._cultivation_allocation_records():
            key = normalized_strain(row["strain"])
            bucket = grouped.setdefault(key, {
                "strain": row["strain"], "benches": set(), "square_feet": 0.0,
                "target_plants": 0,
            })
            bucket["benches"].add(row["bench"])
            bucket["square_feet"] += float(row["square_feet"])
            bucket["target_plants"] += int(row["target_plants"])
        summary: list[dict[str, Any]] = []
        for bucket in grouped.values():
            recommendation = recommend_clone_trays(
                int(bucket["target_plants"]), self.cultivation_overage_percent
            )
            square_feet = round(float(bucket["square_feet"]), 1)
            strain = str(bucket["strain"])
            ordered_benches = sorted(
                bucket["benches"],
                key=lambda value: bench_order.get(str(value), 999),
            )
            summary.append({
                "strain": strain,
                "benches": ", ".join(ordered_benches),
                "bench_sort": min(
                    (bench_order.get(str(value), 999) for value in ordered_benches),
                    default=999,
                ),
                "square_feet": square_feet,
                "plant_density": f"{self.cultivation_plant_density:.2f}",
                "target_plants": int(bucket["target_plants"]),
                "trays": recommendation["trays"],
                "domes": recommendation["trays"],
                "recommended_clones": recommendation["recommended_clones"],
                "actual_overage_percent": recommendation["actual_overage_percent"],
                "projected_yield_lbs": estimated_yield_pounds(
                    square_feet, strain, self.cultivation_flower_room
                ),
            })
        return sorted(
            summary,
            key=lambda row: (
                int(row.get("bench_sort", 999)),
                str(row["strain"]).lower(),
            ),
        )

    @rx.var(cache=True)
    def cultivation_total_target_plants(self) -> str:
        return f"{sum(int(row.get('target_plants', 0) or 0) for row in self.cultivation_strain_summary_rows):,}"

    @rx.var(cache=True)
    def cultivation_total_clone_cuts(self) -> str:
        return f"{sum(int(row.get('recommended_clones', 0) or 0) for row in self.cultivation_strain_summary_rows):,}"

    @rx.var(cache=True)
    def cultivation_total_trays(self) -> str:
        return f"{sum(int(row.get('trays', 0) or 0) for row in self.cultivation_strain_summary_rows):,}"

    @rx.var(cache=True)
    def cultivation_projected_yield(self) -> str:
        total = sum(
            float(row.get("projected_yield_lbs", 0) or 0)
            for row in self.cultivation_strain_summary_rows
        )
        return f"{total:,.1f} lb"

    def _cultivation_current_inventory_breakdown_by_strain(
        self,
    ) -> dict[str, dict[str, float]]:
        totals: dict[str, dict[str, float]] = {}
        for row in self.all_inventory:
            bucket = cultivation_flower_supply_bucket(row, include_pre_wip=True)
            if not bucket:
                continue
            key = normalized_strain(row.get("Strain", ""))
            if not key:
                continue
            pounds = max(
                0.0, self._number(row, "Calculated Weight (g)")
            ) / 453.59237
            detail = totals.setdefault(key, {
                "cpg_lbs": 0.0,
                "wip_lbs": 0.0,
                "pre_wip_lbs": 0.0,
                "total_lbs": 0.0,
            })
            field = {
                "CPG": "cpg_lbs",
                "WIP-Cultivation": "wip_lbs",
                "Pre-WIP-Cultivation": "pre_wip_lbs",
            }[bucket]
            detail[field] += pounds
            if (
                bucket != "Pre-WIP-Cultivation"
                or self.cultivation_clone_plan_include_pre_wip
            ):
                detail["total_lbs"] += pounds
        return totals

    def _cultivation_current_inventory_by_strain(self) -> dict[str, float]:
        return {
            key: float(values.get("total_lbs", 0) or 0)
            for key, values in self._cultivation_current_inventory_breakdown_by_strain().items()
        }

    @staticmethod
    def _rolling_months(count: int = 12) -> list[date]:
        current = date.today().replace(day=1)
        months: list[date] = []
        year, month = current.year, current.month
        for _ in range(max(1, count)):
            months.append(date(year, month, 1))
            month += 1
            if month > 12:
                month = 1
                year += 1
        return months

    def _wip_report_payload(self) -> dict[str, Any]:
        """Assemble one auditable snapshot for the four CFO report models."""
        months = self._rolling_months(12)
        horizon_end = (
            date(months[-1].year + (1 if months[-1].month == 12 else 0),
                 1 if months[-1].month == 12 else months[-1].month + 1, 1)
        )
        period_dates: list[date] = []
        cursor = months[0]
        while cursor < horizon_end:
            period_dates.append(cursor)
            cursor += timedelta(days=14)
        periods = [
            {
                "crop": f"REPORT-{index + 1}",
                "room": "",
                "clone_cut_date": value.isoformat(),
                "flower_entry_date": value.isoformat(),
                "harvest_date": value.isoformat(),
                "available_date": value.isoformat(),
                "is_historical": False,
            }
            for index, value in enumerate(period_dates)
        ]

        current = self._cultivation_current_inventory_breakdown_by_strain()
        opening = {
            key: float(detail.get("wip_lbs", 0) or 0)
            + float(detail.get("pre_wip_lbs", 0) or 0)
            for key, detail in current.items()
        }
        _, scheduled_detail = self._clone_plan_scheduled_by_period(
            periods,
            exclude_current_plan=False,
            post_harvest_days=30,
        )
        scheduled: dict[str, dict[str, float]] = {}
        label_candidates: dict[str, str] = {}
        for row in self.all_inventory:
            key = normalized_strain(row.get("Strain", ""))
            label = normalize_strain_name(row.get("Strain", ""))
            if key and label:
                label_candidates.setdefault(key, label)
        for rows in self.availability_adjusted_velocity_windows.values():
            for row in rows:
                key = normalized_strain(row.get("Strain", ""))
                label = normalize_strain_name(row.get("Strain", ""))
                if key and label:
                    label_candidates.setdefault(key, label)
        for key, buckets in scheduled_detail.items():
            for details in buckets:
                for detail in details:
                    try:
                        available = date.fromisoformat(
                            str(detail.get("available_date", ""))
                        )
                    except ValueError:
                        continue
                    month_key = available.strftime("%Y-%m")
                    if month_key not in {month.strftime("%Y-%m") for month in months}:
                        continue
                    scheduled.setdefault(key, {})[month_key] = (
                        scheduled.setdefault(key, {}).get(month_key, 0.0)
                        + max(0.0, float(detail.get("forecast_counted_lbs", 0) or 0))
                    )
                    label = normalize_strain_name(detail.get("strain", ""))
                    if label:
                        label_candidates.setdefault(key, label)

        model_names = [
            "Current SKU Velocity",
            "30-Day Availability-Adjusted",
            "60-Day Availability-Adjusted",
            "AI-Adjusted",
        ]
        model_lookup = {
            "Current SKU Velocity": "Current SKU Velocity",
            "30-Day Availability-Adjusted": "30-Day Availability-Adjusted",
            "60-Day Availability-Adjusted": "60-Day Availability-Adjusted",
            "AI-Adjusted": "AI-Adjusted",
        }
        requested_by_model: dict[str, dict[str, dict[str, float]]] = {}
        for report_model in model_names:
            forecast = self._clone_plan_two_week_demand_by_strain(
                periods,
                model_lookup[report_model],
                "Flower + Pre-Rolls",
            )
            requested: dict[str, dict[str, float]] = {}
            for strain_key, period_values in forecast.items():
                for index, two_week_lbs in enumerate(period_values):
                    if index >= len(period_dates):
                        break
                    start = period_dates[index]
                    remaining_days = 14
                    segment_start = start
                    while remaining_days > 0 and segment_start < horizon_end:
                        next_month = date(
                            segment_start.year + (1 if segment_start.month == 12 else 0),
                            1 if segment_start.month == 12 else segment_start.month + 1,
                            1,
                        )
                        segment_days = min(
                            remaining_days, (next_month - segment_start).days
                        )
                        month_key = segment_start.strftime("%Y-%m")
                        requested.setdefault(strain_key, {})[month_key] = (
                            requested.setdefault(strain_key, {}).get(month_key, 0.0)
                            + max(0.0, float(two_week_lbs or 0))
                            * segment_days
                            / 14.0
                        )
                        remaining_days -= segment_days
                        segment_start += timedelta(days=segment_days)
            requested_by_model[report_model] = requested

        active_keys = set(opening) | set(scheduled)
        for requested in requested_by_model.values():
            active_keys.update(requested)
        active_keys = {
            key for key in active_keys
            if opening.get(key, 0) > 0
            or any(scheduled.get(key, {}).values())
            or any(
                any(model.get(key, {}).values())
                for model in requested_by_model.values()
            )
        }
        for strain in self._cultivation_planning_strain_names():
            key = normalized_strain(strain)
            if key:
                label_candidates.setdefault(key, normalize_strain_name(strain))
        labels = {
            key: label_candidates.get(key) or normalize_strain_name(key) or key.title()
            for key in active_keys
        }
        strains = sorted(labels.values(), key=str.casefold)

        def relabel(source: dict[str, Any]) -> dict[str, Any]:
            return {labels[key]: value for key, value in source.items() if key in labels}

        return {
            "months": months,
            "strains": strains,
            "opening_wip": relabel(opening),
            "scheduled": relabel(scheduled),
            "requested_by_model": {
                model: relabel(values)
                for model, values in requested_by_model.items()
            },
        }

    @rx.var(cache=True)
    def cultivation_demand_brand_options(self) -> list[str]:
        values = sorted({
            str(row.get("Brand", "") or "").strip()
            for row in self.availability_demand_summary
            if str(row.get("Brand", "") or "").strip()
        })
        return ["All Brands", *values]

    @rx.var(cache=True)
    def cultivation_demand_strain_options(self) -> list[str]:
        values = sorted({
            str(row.get("Strain", "") or "").strip()
            for row in self.availability_demand_summary
            if (
                self.cultivation_demand_brand_filter == "All Brands"
                or row.get("Brand") == self.cultivation_demand_brand_filter
            )
            if str(row.get("Strain", "") or "").strip()
        })
        return ["All Strains", *values]

    @rx.var(cache=True)
    def cultivation_demand_summary_rows(self) -> list[list[Any]]:
        rows = self.availability_demand_summary
        if self.cultivation_demand_brand_filter != "All Brands":
            rows = [
                row for row in rows
                if row.get("Brand") == self.cultivation_demand_brand_filter
            ]
        if self.cultivation_demand_strain_filter != "All Strains":
            rows = [
                row for row in rows
                if row.get("Strain") == self.cultivation_demand_strain_filter
            ]
        if self.cultivation_demand_sku_filter != "All Compared SKUs":
            rows = [
                row for row in rows
                if row.get("SKU Type") == self.cultivation_demand_sku_filter
            ]
        return [[
            row.get("Brand", ""),
            row.get("Strain", ""),
            row.get("SKU Type", ""),
            row.get("First Ship Week", ""),
            row.get("Last Ship Week", ""),
            row.get("Calendar Weeks", 0),
            row.get("Shipping Weeks", 0),
            row.get("Likely Constrained Weeks", 0),
            row.get("Recent Gap Weeks", 0),
            row.get("Current Velocity", 0),
            row.get("Experimental Adjusted Velocity", 0),
            row.get("Adjustment", ""),
            row.get("Signal", ""),
        ] for row in rows]

    @rx.var(cache=True)
    def cultivation_demand_weekly_rows(self) -> list[list[Any]]:
        if self.cultivation_demand_strain_filter == "All Strains":
            return []
        rows = [
            row for row in self.availability_demand_weekly
            if row.get("Strain") == self.cultivation_demand_strain_filter
        ]
        if self.cultivation_demand_brand_filter != "All Brands":
            rows = [
                row for row in rows
                if row.get("Brand") == self.cultivation_demand_brand_filter
            ]
        if self.cultivation_demand_sku_filter != "All Compared SKUs":
            rows = [
                row for row in rows
                if row.get("SKU Type") == self.cultivation_demand_sku_filter
            ]
        return [[
            row.get("Week Starting", ""),
            row.get("SKU Type", ""),
            row.get("Units Shipped", 0),
            row.get("Availability Signal", ""),
        ] for row in rows]

    @rx.var(cache=True)
    def cultivation_demand_page_size(self) -> int:
        return int(self.cultivation_demand_rows_per_page)

    @rx.var(cache=True)
    def cultivation_demand_sku_count(self) -> str:
        return f"{len(self.availability_demand_summary):,}"

    @rx.var(cache=True)
    def cultivation_demand_strain_count(self) -> str:
        return f"{len({str(row.get('Strain', '') or '') for row in self.availability_demand_summary if row.get('Strain')}):,}"

    @rx.var(cache=True)
    def cultivation_demand_constraint_count(self) -> str:
        return f"{sum(int(row.get('Likely Constrained Weeks', 0) or 0) for row in self.availability_demand_summary):,}"

    def _cultivation_expiring_by_strain(self, horizon_days: int) -> dict[str, float]:
        totals: dict[str, float] = {}
        for row in self.all_inventory:
            if not inventory_counts_as_current_cultivation_supply(row):
                continue
            days = self._optional_number(row, "Days to Spoil")
            if days is None or days <= 0 or days > horizon_days:
                continue
            key = normalized_strain(row.get("Strain", ""))
            grams = self._number(row, "Calculated Weight (g)")
            totals[key] = totals.get(key, 0.0) + max(0.0, grams) / 453.59237
        return totals

    def _cultivation_weekly_demand_by_strain(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for row in self.velocity:
            key = normalized_strain(row.get("Strain", ""))
            grams_per_unit = sku_fill_grams(row.get("SKU Type", ""))
            if not key or grams_per_unit <= 0:
                continue
            weekly_units = self._number(row, "Avg Weekly Units")
            totals[key] = totals.get(key, 0.0) + (
                max(0.0, weekly_units) * grams_per_unit / 453.59237
            )
        return totals

    @rx.var(cache=True)
    def cultivation_future_yield_rows(self) -> list[dict[str, Any]]:
        planned = self.cultivation_strain_summary_rows
        if not planned:
            return []
        try:
            available = date.fromisoformat(self.cultivation_available_date)
        except (TypeError, ValueError):
            return []
        today = date.today()
        horizon_days = max(1, (available - today).days)
        horizon_weeks = horizon_days / 7
        current = self._cultivation_current_inventory_by_strain()
        expiring = self._cultivation_expiring_by_strain(horizon_days)
        demand = self._cultivation_weekly_demand_by_strain()
        scheduled: dict[str, float] = {}
        scheduled_arrivals: dict[str, list[date]] = {}
        actual_crop_lbs = self._clone_plan_actual_crop_lbs()
        grouped_crops: dict[tuple[str, str, str, str], float] = {}
        for crop in UPCOMING_CROP_ALLOCATIONS:
            group_key = (
                str(crop["crop"]), str(crop["room"]),
                str(crop["harvest_date"]), normalized_strain(crop["strain"]),
            )
            grouped_crops[group_key] = grouped_crops.get(group_key, 0.0) + float(
                crop["square_feet"]
            )
        for (crop_name, room, harvest_text, key), square_feet in grouped_crops.items():
            harvest = date.fromisoformat(harvest_text)
            crop_available = harvest + timedelta(days=self.cultivation_post_harvest_days)
            if not crop_is_scheduled_supply(
                harvest, today, available, self.cultivation_post_harvest_days
            ):
                continue
            gross = estimated_yield_pounds(square_feet, key, room)
            reconciliation = scheduled_supply_reconciliation(
                gross,
                bench_plant_capacity(square_feet),
                int(self.cultivation_fresh_frozen_adjustments.get(
                    f"{crop_name.casefold()}|{key}", 0
                ) or 0),
                actual_crop_lbs.get((crop_name.casefold(), key), 0.0),
                harvest,
                today,
                SCHEDULED_SUPPLY_EXPIRY_DAYS,
                creative_use_reduction_lbs=float(
                    self.cultivation_creative_use_adjustments.get(
                        f"{crop_name.casefold()}|{key}", 0
                    ) or 0
                ),
            )
            forecast = reconciliation["forecast_counted_lbs"]
            if forecast > 0:
                scheduled[key] = scheduled.get(key, 0.0) + forecast
                scheduled_arrivals.setdefault(key, []).append(crop_available)
        rows: list[dict[str, Any]] = []
        for plan in planned:
            key = normalized_strain(plan["strain"])
            current_lbs = current.get(key, 0.0)
            scheduled_lbs = scheduled.get(key, 0.0)
            proposed_lbs = float(plan.get("projected_yield_lbs", 0) or 0)
            weekly_lbs = demand.get(key, 0.0)
            expiring_lbs = expiring.get(key, 0.0)
            arrival_dates = scheduled_arrivals.get(key, [])
            if not arrival_dates:
                scheduled_window = "—"
            else:
                first_arrival = min(arrival_dates)
                last_arrival = max(arrival_dates)
                first_label = f"{first_arrival.strftime('%b')} {first_arrival.day}"
                last_label = f"{last_arrival.strftime('%b')} {last_arrival.day}"
                scheduled_window = (
                    first_label
                    if first_arrival == last_arrival
                    else f"{first_label} – {last_label}"
                )
            balance = max(
                0.0,
                current_lbs + scheduled_lbs + proposed_lbs
                - (weekly_lbs * horizon_weeks) - expiring_lbs,
            )
            weeks_supply = None if weekly_lbs <= 0 else balance / weekly_lbs
            rows.append({
                "strain": plan["strain"],
                "current_lbs": round(current_lbs, 1),
                "scheduled_lbs": round(scheduled_lbs, 1),
                "scheduled_window": scheduled_window,
                "proposed_lbs": round(proposed_lbs, 1),
                "weekly_demand_lbs": round(weekly_lbs, 1),
                "expiring_lbs": round(expiring_lbs, 1),
                "projected_balance_lbs": round(balance, 1),
                "weeks_supply": "—" if weeks_supply is None else f"{weeks_supply:.1f}",
                "risk": projected_risk(weeks_supply),
            })
        return sorted(rows, key=lambda row: (str(row["risk"]), str(row["strain"])))

    @rx.var(cache=True)
    def cultivation_upcoming_crop_rows(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str], float] = {}
        today = date.today()
        for crop in UPCOMING_CROP_ALLOCATIONS:
            harvest = date.fromisoformat(crop["harvest_date"])
            crop_available = harvest + timedelta(days=self.cultivation_post_harvest_days)
            if crop_available < today:
                continue
            key = (crop["crop"], crop["room"], crop["harvest_date"])
            grouped[key] = grouped.get(key, 0.0) + estimated_yield_pounds(
                crop["square_feet"], crop["strain"], crop["room"]
            )

        # Saved clone plans extend the source-of-truth crop schedule beyond the
        # originally bundled active-room snapshot. Drafts remain visible here
        # because the table is a planning aid; approval still requires exact
        # physical bench assignments.
        bundled_crops = {
            str(crop["crop"]).casefold() for crop in UPCOMING_CROP_ALLOCATIONS
        }
        saved_by_crop: dict[str, dict[str, Any]] = {}
        for plan in reversed(self.cultivation_clone_plan_history):
            crop_name = str(plan.get("crop", "") or "").strip()
            if not crop_name or crop_name.casefold() in bundled_crops:
                continue
            saved_by_crop[crop_name.casefold()] = plan
        for plan in saved_by_crop.values():
            try:
                harvest = date.fromisoformat(str(plan.get("clone_cut_date", ""))) + timedelta(
                    days=40 + 68
                )
            except (TypeError, ValueError):
                continue
            if harvest + timedelta(days=self.cultivation_post_harvest_days) < today:
                continue
            room = str(plan.get("flower_room", "") or "")
            crop_name = str(plan.get("crop", "") or "")
            estimated = sum(
                estimated_yield_pounds(
                    float(benches or 0) * 185.0, strain, room
                )
                for strain, benches in dict(plan.get("allocations") or {}).items()
                if float(benches or 0) > 0
            )
            if estimated > 0:
                grouped[(crop_name, room, harvest.isoformat())] = estimated

        # The on-screen plan should be represented immediately, including
        # unsaved edits, and should replace any older saved F5.10 draft.
        current_period = self._current_clone_period()
        current_crop = str(current_period["crop"])
        current_key = current_crop.casefold()
        for key in [key for key in grouped if key[0].casefold() == current_key]:
            grouped.pop(key, None)
        current_estimated = sum(
            estimated_yield_pounds(
                float(benches or 0) * 185.0,
                strain,
                str(current_period["room"]),
            )
            for strain, benches in self.cultivation_clone_plan_allocations.items()
            if float(benches or 0) > 0
        )
        if current_estimated > 0:
            grouped[
                (
                    current_crop,
                    str(current_period["room"]),
                    str(current_period["harvest_date"]),
                )
            ] = current_estimated
        return [
            {
                "crop": key[0], "room": key[1], "harvest_date": key[2],
                "estimated_yield_lbs": round(value, 1),
            }
            for key, value in sorted(grouped.items(), key=lambda item: item[0][2])
        ]

    @rx.var(cache=True)
    def cultivation_history_harvest_rows(self) -> list[dict[str, Any]]:
        return historical_harvest_rows(self.cultivation_history_room_filter)

    @rx.var(cache=True)
    def cultivation_history_room_rows(self) -> list[dict[str, Any]]:
        return historical_room_rows()

    @rx.var(cache=True)
    def cultivation_history_room_chart_rows(self) -> list[dict[str, Any]]:
        return historical_room_chart_rows()

    @rx.var(cache=True)
    def cultivation_history_cycle_rows(self) -> list[dict[str, Any]]:
        return historical_cycle_rows()

    @rx.var(cache=True)
    def cultivation_history_harvest_table_data(self) -> list[list[Any]]:
        return historical_harvest_table_data(
            self.cultivation_history_room_filter
        )

    @rx.var(cache=True)
    def cultivation_history_room_table_data(self) -> list[list[Any]]:
        return historical_room_table_data(
            self.cultivation_history_room_filter
        )

    @rx.var(cache=True)
    def cultivation_history_cycle_table_data(self) -> list[list[Any]]:
        return historical_cycle_table_data()

    @rx.var(cache=True)
    def cultivation_history_strain_rows(self) -> list[dict[str, Any]]:
        return historical_strain_rows()

    @rx.var(cache=True)
    def cultivation_history_strain_options(self) -> list[str]:
        return historical_strain_options()

    @rx.var(cache=True)
    def cultivation_history_strain_table_data(self) -> list[list[Any]]:
        return historical_strain_table_data(
            self.cultivation_history_strain_filter
        )

    @rx.var(cache=True)
    def cultivation_history_page_size(self) -> int:
        return int(self.cultivation_history_rows_per_page)

    @rx.var(cache=True)
    def cultivation_history_strain_chart_rows(self) -> list[dict[str, Any]]:
        return historical_strain_chart_rows(
            strain_filter=self.cultivation_history_strain_filter
        )

    def _cultivation_history_kpis(self) -> dict[str, str]:
        return historical_kpis(self.cultivation_history_room_filter)

    @rx.var(cache=True)
    def cultivation_history_harvest_count(self) -> str:
        return self._cultivation_history_kpis()["harvests"]

    @rx.var(cache=True)
    def cultivation_history_total_finished(self) -> str:
        return self._cultivation_history_kpis()["total_finished"]

    @rx.var(cache=True)
    def cultivation_history_average_finished(self) -> str:
        return self._cultivation_history_kpis()["average_finished"]

    @rx.var(cache=True)
    def cultivation_history_weighted_yield(self) -> str:
        return self._cultivation_history_kpis()["weighted_yield"]

    @rx.var(cache=True)
    def cultivation_history_average_conversion(self) -> str:
        return self._cultivation_history_kpis()["average_conversion"]

    @rx.event
    def start_production_from_sku(
        self, brand: str, strain: str, sku_type: str
    ):
        """Open Production Planning without mutating shared Inventory filters.

        SKU Planning already has the compact Sales snapshot in memory. The
        previous handler performed another synchronous database read after it
        changed the global Brand/Strain/SKU filters. A transient database
        failure therefore left every Inventory table stuck on the selected
        product and surfaced Reflex's generic administrator error. Navigation
        is now immediate and optional Production data warms independently.
        """
        self.production_brand = str(brand or "")
        self.production_strain = str(strain or "")
        self.production_sku = str(sku_type or "")
        self.production_edit_plan_id = ""
        self.production_template_choice = "No Template"
        self._reset_production_builder()
        self.production_action_message = (
            "Product target loaded from SKU Planning & Coverage. "
            "Choose the WIP lots to commit."
        )
        self.production_action_error = ""
        self.sales_demand_view = "production"
        self.production_view = "build"
        # Render the selected target before warming the larger Saved Plans and
        # Calendar collections. The background loader catches database errors,
        # so this click can no longer poison the active user session.
        yield
        if "production" not in self.sales_loaded_views:
            yield DashboardState.load_sales_background
        else:
            self._set_initial_calendar_month()

    @rx.var(cache=True)
    def filtered_stockouts(self) -> list[dict[str, Any]]:
        return [
            row for row in self.stockouts
            if self._matches(row) and self._matches_lifecycle(row)
        ]

    @rx.var(cache=True)
    def filtered_saved_plans(self) -> list[dict[str, Any]]:
        return list(self.saved_plans)

    @rx.var(cache=True)
    def filtered_calendar(self) -> list[CalendarEvent]:
        return list(self.calendar)

    @rx.var(cache=True)
    def filtered_saved_plan_cards(self) -> list[SavedPlanCard]:
        search = self.saved_plan_search.strip().lower()
        rows = []
        for row in self.saved_plan_cards:
            if (
                self.saved_plan_status_filter != "All Plan Statuses"
                and row.get("Status") != self.saved_plan_status_filter
            ):
                continue
            if search and search not in " ".join(
                str(value or "") for value in row.values()
            ).lower():
                continue
            rows.append(row)
        return rows

    @rx.var(cache=True)
    def saved_plan_status_options(self) -> list[str]:
        return [
            "All Plan Statuses",
            *sorted({
                str(row.get("Status", ""))
                for row in self.saved_plan_cards
                if str(row.get("Status", ""))
            }),
        ]

    @rx.var(cache=True)
    def filtered_customers(self) -> list[dict[str, Any]]:
        return [row for row in self.customers if self._matches(row)]

    def _filtered_retail_deliveries(self) -> list[dict[str, Any]]:
        dated_rows: list[tuple[date, dict[str, Any]]] = []
        selected_status = (
            "Awaiting Acceptance" if self.retail_show_pending else "Accepted"
        )
        for row in self.retail_delivery_history:
            if str(row.get("Transfer Status", "")) != selected_status:
                continue
            try:
                delivery_date = date.fromisoformat(
                    str(row.get("Activity Date", ""))[:10]
                )
            except ValueError:
                continue
            dated_rows.append((delivery_date, row))
        if not dated_rows:
            return []
        anchor = max(item[0] for item in dated_rows)
        weeks = {
            "1 Week": 1, "2 Weeks": 2, "3 Weeks": 3, "4 Weeks": 4,
        }.get(self.retail_timeframe, 4)
        start = anchor - timedelta(days=weeks * 7 - 1)
        filtered: list[dict[str, Any]] = []
        for delivery_date, row in dated_rows:
            if delivery_date < start:
                continue
            if (
                self.retail_brand_filter != "All Brands"
                and str(row.get("Brand", "")) != self.retail_brand_filter
            ):
                continue
            if (
                self.retail_strain_filter != "All Strains"
                and str(row.get("Strain", "")) != self.retail_strain_filter
            ):
                continue
            if (
                self.retail_sku_filter != "All SKU Types"
                and str(row.get("SKU Type", "")) != self.retail_sku_filter
            ):
                continue
            if (
                self.retail_customer_filter != "All Retailers"
                and str(row.get("Customer", "")) != self.retail_customer_filter
            ):
                continue
            filtered.append(row)
        return filtered

    def _retail_aggregate_records(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in self._filtered_retail_deliveries():
            key = (
                str(row.get("Destination License", "")),
                str(row.get("Customer", "")),
                str(row.get("Brand", "")),
                str(row.get("Strain", "")),
                str(row.get("SKU Type", "")),
            )
            delivery_date = str(row.get("Activity Date", ""))
            record = grouped.setdefault(key, {
                "Destination License": key[0], "Retailer": key[1],
                "Brand": key[2], "Strain": key[3], "SKU Type": key[4],
                "Units Shipped": 0.0, "Packages": 0, "Manifests": 0,
                "First Metrc Date": delivery_date,
                "Latest Metrc Date": delivery_date,
            })
            record["Units Shipped"] += float(row.get("Units Shipped", 0) or 0)
            record["Packages"] += int(row.get("Packages", 0) or 0)
            record["Manifests"] += int(row.get("Manifests", 0) or 0)
            record["First Metrc Date"] = min(
                str(record["First Metrc Date"]), delivery_date
            )
            record["Latest Metrc Date"] = max(
                str(record["Latest Metrc Date"]), delivery_date
            )
        records = list(grouped.values())
        for record in records:
            record["Units Shipped"] = round(
                float(record["Units Shipped"]), 2
            )
        records.sort(key=lambda row: (
            str(row.get("Latest Metrc Date", "")),
            float(row.get("Units Shipped", 0) or 0),
        ), reverse=True)
        return records

    @rx.var(cache=True)
    def retail_timeframe_options(self) -> list[str]:
        return ["1 Week", "2 Weeks", "3 Weeks", "4 Weeks"]

    def _retail_options(self, column: str, all_label: str) -> list[str]:
        return [all_label, *sorted({
            str(row.get(column, "")).strip()
            for row in self.retail_delivery_history
            if str(row.get(column, "")).strip()
        })]

    def _retail_strains_for_brand(self, brand: str) -> list[str]:
        selected_brand = str(brand or "").strip()
        return sorted({
            str(row.get("Strain", "")).strip()
            for row in self.retail_delivery_history
            if str(row.get("Strain", "")).strip()
            and (
                selected_brand == "All Brands"
                or str(row.get("Brand", "")).strip() == selected_brand
            )
        })

    @rx.var(cache=True)
    def retail_brand_options(self) -> list[str]:
        return self._retail_options("Brand", "All Brands")

    @rx.var(cache=True)
    def retail_strain_options(self) -> list[str]:
        return [
            "All Strains",
            *self._retail_strains_for_brand(self.retail_brand_filter),
        ]

    @rx.var(cache=True)
    def retail_sku_options(self) -> list[str]:
        return self._retail_options("SKU Type", "All SKU Types")

    @rx.var(cache=True)
    def retail_customer_options(self) -> list[str]:
        return self._retail_options("Customer", "All Retailers")

    @rx.var(cache=True)
    def retail_availability_rows(self) -> list[list[Any]]:
        columns = [
            "Retailer", "Destination License", "Brand", "Strain", "SKU Type",
            "Units Shipped", "Packages", "Manifests", "First Metrc Date",
            "Latest Metrc Date",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self._retail_aggregate_records()
        ]

    @rx.var(cache=True)
    def retail_retailers_metric(self) -> str:
        retailers = {
            str(row.get("Retailer", ""))
            for row in self._retail_aggregate_records()
            if str(row.get("Retailer", ""))
        }
        return f"{len(retailers):,}"

    @rx.var(cache=True)
    def retail_units_metric(self) -> str:
        units = sum(
            float(row.get("Units Shipped", 0) or 0)
            for row in self._retail_aggregate_records()
        )
        return f"{units:,.0f}"

    @rx.var(cache=True)
    def retail_skus_metric(self) -> str:
        return f"{len(self._retail_aggregate_records()):,}"

    @rx.var(cache=True)
    def retail_latest_delivery(self) -> str:
        rows = self._retail_aggregate_records()
        return str(rows[0].get("Latest Metrc Date", "—")) if rows else "—"

    def _retailer_location_match(
        self, destination_license: str, retailer: str
    ) -> tuple[dict[str, Any], str]:
        """Match a Metrc destination to its saved location record."""
        license_key = destination_license.strip().upper()
        name_key = normalized_retailer_name(retailer)
        by_source: dict[str, dict[str, Any]] = {}
        for location in self.retailer_locations:
            source_id = str(location.get("source_id", "")).strip()
            if source_id:
                by_source[source_id] = location
            saved_license = str(
                location.get("destination_license", "")
            ).strip().upper()
            if license_key and saved_license == license_key:
                return location, "Destination License"
        for location in self.retailer_locations:
            saved_names = {
                normalized_retailer_name(location.get("metrc_business_name", "")),
                normalized_retailer_name(location.get("public_store_name", "")),
            }
            if name_key and name_key in saved_names:
                return location, "Exact Store Name"
        directory_match = find_clade9_location(retailer)
        if directory_match:
            source_id = str(directory_match.get("source_id", "")).strip()
            saved = by_source.get(source_id)
            if saved:
                return saved, "Clade9 Directory"
            return {
                "public_store_name": directory_match.get("name", ""),
                "street_address": directory_match.get("address", ""),
                "locality": directory_match.get("locality", ""),
                "website": directory_match.get("website", ""),
                "source": "Clade9 Store Locator",
                "source_id": source_id,
            }, "Clade9 Directory"
        return {}, "No Match"

    @staticmethod
    def _valid_coordinate(value: Any, maximum: float) -> float | None:
        try:
            coordinate = float(value)
        except (TypeError, ValueError):
            return None
        return coordinate if math.isfinite(coordinate) and abs(coordinate) <= maximum else None

    def _retail_map_locations(self) -> list[RetailMapLocation]:
        """Return one coordinate-aware map row for every matching retailer."""
        locations: dict[str, RetailMapLocation] = {}
        for row in self._retail_aggregate_records():
            retailer = str(row.get("Retailer", "")).strip()
            destination_license = str(
                row.get("Destination License", "")
            ).strip()
            if not retailer:
                continue
            delivery_date = str(row.get("Latest Metrc Date", ""))
            location_key = destination_license or normalized_retailer_name(retailer)
            current = locations.get(location_key)
            if current and current["Latest Metrc Date"] >= delivery_date:
                continue
            match, match_method = self._retailer_location_match(
                destination_license, retailer
            )
            street_address = str(
                match.get("street_address", match.get("address", ""))
            ).strip()
            locality = str(match.get("locality", "")).strip()
            address_is_complete = bool(
                re.search(r"\b(?:NJ|New Jersey)\b", street_address, re.I)
                and re.search(r"\b\d{5}(?:-\d{4})?\b", street_address)
            )
            address = street_address if address_is_complete else ", ".join(
                part for part in (street_address, locality) if part
            )
            if not address:
                address = str(match.get("full_address", "")).strip()
            latitude = self._valid_coordinate(match.get("latitude"), 90)
            longitude = self._valid_coordinate(match.get("longitude"), 180)
            has_coordinates = latitude is not None and longitude is not None
            route_address = address or f"{retailer}, New Jersey"
            map_query = (
                f"{latitude},{longitude}" if has_coordinates else route_address
            )
            locations[location_key] = {
                "Retailer": retailer,
                "Destination License": destination_license,
                "Address": address or "Address not matched in the Clade9 directory",
                "Latest Metrc Date": delivery_date,
                "Date Label": (
                    "Sent at" if self.retail_show_pending else "Received at"
                ),
                "Website": str(match.get("website", "")).strip(),
                "Map URL": (
                    "https://www.google.com/maps/search/?api=1&query="
                    + quote_plus(map_query)
                ),
                "Route Address": route_address,
                "Latitude": latitude,
                "Longitude": longitude,
                "Match Method": match_method,
                "Coordinate Status": (
                    "Saved Coordinates" if has_coordinates
                    else "Address Only" if address else "Needs Address"
                ),
                "Verified": bool(match.get("verified", False)),
                "Location Status": str(match.get("location_status", "")),
                "Notes": str(match.get("notes", "")),
            }
        return sorted(locations.values(), key=lambda item: item["Retailer"].lower())

    @rx.var(cache=True)
    def retail_map_location_cards(self) -> list[RetailMapLocation]:
        return self._retail_map_locations()

    @rx.var(cache=True)
    def retail_location_review_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "Destination License": row["Destination License"],
                "Metrc Customer": row["Retailer"],
                "Matched Address": row["Address"],
                "Latitude": row["Latitude"] if row["Latitude"] is not None else "",
                "Longitude": row["Longitude"] if row["Longitude"] is not None else "",
                "Coordinate Status": row["Coordinate Status"],
                "Match Method": row["Match Method"],
                "Verified": row["Verified"],
                "Location Status": row["Location Status"],
                "Notes": row["Notes"],
            }
            for row in self._retail_map_locations()
        ]

    @rx.event
    def download_retail_location_review(self):
        return rx.download(
            data=self._csv_bytes(self.retail_location_review_rows),
            filename=(
                "qcc_retailer_location_review_"
                f"{date.today().isoformat()}.csv"
            ),
        )

    @rx.var(cache=True)
    def retail_map_src_doc(self) -> str:
        rows = [
            {
                "retailer": row["Retailer"],
                "address": row["Address"],
                "route_address": row["Route Address"],
                "last_delivery": row["Latest Metrc Date"],
                "date_label": row["Date Label"],
                "latitude": row["Latitude"],
                "longitude": row["Longitude"],
            }
            for row in self._retail_map_locations()
        ]
        return _retail_map_document(rows, self.retail_start_address.strip())

    @rx.var(cache=True)
    def retail_all_map_note(self) -> str:
        count = len(self._retail_map_locations())
        if count == 0:
            return "No matching retailer locations are available to map."
        mapped = sum(
            row.get("Coordinate Status") == "Saved Coordinates"
            for row in self._retail_map_locations()
        )
        unresolved = count - mapped
        if unresolved:
            return (
                f"{count:,} retailers match. {mapped:,} have saved coordinates. "
                f"The map temporarily checks up to 15 of the {unresolved:,} "
                "address-only records; download the review list to complete them."
            )
        return (
            f"The map immediately displays all {count:,} matching retailer "
            "locations from saved coordinates."
        )

    @rx.var(cache=True)
    def retail_activity_heading(self) -> str:
        return (
            "Outgoing Transfers Awaiting Acceptance"
            if self.retail_show_pending else "Accepted Retail Deliveries"
        )

    @rx.var(cache=True)
    def retail_activity_description(self) -> str:
        if self.retail_show_pending:
            return (
                "Shows outbound transfers still marked Shipped in Metrc. Dates "
                "are when QCC created the outgoing transfer; these shops have "
                "not yet recorded acceptance in Metrc."
            )
        return (
            "Shows retailer deliveries marked Accepted in Metrc. Dates are the "
            "Metrc Received At dates. Acceptance does not guarantee that the "
            "retailer still has the product in stock."
        )

    @rx.var(cache=True)
    def retail_window_label(self) -> str:
        return "Outbound Window" if self.retail_show_pending else "Received Window"

    @rx.var(cache=True)
    def retail_latest_date_label(self) -> str:
        return "Latest Outbound" if self.retail_show_pending else "Latest Receipt"

    @rx.var(cache=True)
    def retail_latest_date_caption(self) -> str:
        return (
            "Newest matching transfer-created date"
            if self.retail_show_pending
            else "Newest matching Metrc receipt date"
        )

    @rx.var(cache=True)
    def filtered_exceptions(self) -> list[dict[str, Any]]:
        return [row for row in self.exceptions if self._matches(row)]

    @rx.var(cache=True)
    def selected_exception_state(self) -> str:
        return {
            "Open Transfers": "Shipped",
            "Rejected Transfers": "Rejected",
            "Returned Transfers": "Returned",
        }.get(self.shipment_exception_view, "Shipped")

    @rx.var(cache=True)
    def filtered_exception_packages(self) -> list[dict[str, Any]]:
        return [
            row for row in self.exception_packages
            if str(row.get("State", "")) == self.selected_exception_state
            and self._matches(row)
        ]

    @rx.var(cache=True)
    def selected_exception_manifests_metric(self) -> str:
        return f"{self.exception_server_manifests:,}"

    @rx.var(cache=True)
    def selected_exception_packages_metric(self) -> str:
        return f"{self.exception_server_total:,}"

    @rx.var(cache=True)
    def selected_exception_value_metric(self) -> str:
        return f"${self.exception_server_value:,.2f}"

    @rx.var(cache=True)
    def exception_total_pages(self) -> int:
        return max(
            (self.exception_server_total + self.exception_page_size - 1)
            // self.exception_page_size,
            1,
        )

    @rx.var(cache=True)
    def exception_page_label(self) -> str:
        count = self.exception_server_total
        if count == 0:
            return "No matching package rows"
        page = min(max(self.exception_page, 1), self.exception_total_pages)
        start = (page - 1) * self.exception_page_size + 1
        end = min(page * self.exception_page_size, count)
        return (
            f"Rows {start:,}-{end:,} of {count:,} · "
            f"Page {page:,} of {self.exception_total_pages:,}"
        )

    @rx.event
    def previous_exception_page(self):
        self.exception_page = max(self.exception_page - 1, 1)
        yield DashboardState.load_distribution_operations_background

    @rx.event
    def next_exception_page(self):
        self.exception_page = min(
            self.exception_page + 1, self.exception_total_pages
        )
        yield DashboardState.load_distribution_operations_background

    @rx.var(cache=True)
    def shipment_exception_description(self) -> str:
        if self.shipment_exception_view == "Rejected Transfers":
            return (
                "Rejected package lines are shown separately from returned "
                "product. A manifest may also contain packages that were accepted."
            )
        if self.shipment_exception_view == "Returned Transfers":
            return (
                "Returned package lines are shown separately from outright "
                "rejections. Review package detail before treating the full manifest "
                "as a return."
            )
        return (
            "Open transfers are still marked Shipped in Metrc and have not yet "
            "been accepted, rejected, or returned."
        )

    @rx.var(cache=True)
    def filtered_transfer_data(self) -> list[dict[str, Any]]:
        return [row for row in self._transfer_data if self._matches(row)]

    def _filtered_inventory(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered = []
        for row in rows:
            if not self._matches(row):
                continue
            if (
                self.inventory_stage_filter != "All Production Stages"
                and row.get("Production Stage") != self.inventory_stage_filter
            ):
                continue
            if (
                self.inventory_license_filter != "All Licenses"
                and row.get("License") != self.inventory_license_filter
            ):
                continue
            if (
                self.inventory_qa_filter != "All QA Statuses"
                and row.get("QA Status") != self.inventory_qa_filter
            ):
                continue
            if (
                self.inventory_category_filter != "All Categories"
                and row.get("Category") != self.inventory_category_filter
            ):
                continue
            if (
                self.inventory_location_filter != "All Locations"
                and row.get("Location") != self.inventory_location_filter
            ):
                continue
            if (
                self.inventory_ownership_filter == "QCC-Owned Inventory"
                and not str(row.get("Ownership Status", "")).startswith("QCC-Owned")
            ):
                continue
            if (
                self.inventory_ownership_filter not in {
                    "All Ownership Statuses", "QCC-Owned Inventory",
                }
                and row.get("Ownership Status") != self.inventory_ownership_filter
            ):
                continue
            filtered.append(row)
        return filtered

    def _inventory_options(self, column: str, all_label: str) -> list[str]:
        values = sorted({
            str(row.get(column, "")).strip()
            for row in self.all_inventory
            if str(row.get(column, "")).strip()
        })
        return [all_label, *values]

    def _inventory_view_rows(self, flag: str) -> list[dict[str, Any]]:
        """Return one Streamlit-classified view from the master package list."""
        return [row for row in self.all_inventory if bool(row.get(flag, False))]

    @rx.var(cache=True)
    def executive_facility_options(self) -> list[str]:
        return self._inventory_options("Current Facility", "All Facilities")

    @rx.var(cache=True)
    def executive_ownership_options(self) -> list[str]:
        values = self._inventory_options(
            "Ownership Status", "All Ownership Statuses"
        )[1:]
        return ["QCC-Owned Inventory", "All Ownership Statuses", *values]

    @rx.var(cache=True)
    def inventory_ownership_options(self) -> list[str]:
        values = self._inventory_options(
            "Ownership Status", "All Ownership Statuses"
        )[1:]
        return ["All Ownership Statuses", "QCC-Owned Inventory", *values]

    def _executive_inventory(
        self, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for row in rows:
            stage = str(row.get("Production Stage", ""))
            if stage in {
                "WIP-Cultivation", "Pre-WIP-Cultivation",
                "WIP-Purchased 1A", "Pre-WIP-Purchased 1A",
                "WIP-Manufacturing", "Pre-WIP",
            }:
                row_strain = normalize_strain_name(
                    str(row.get("Strain", ""))
                ).strip().lower()
                if self.strain_filter != "All Strains" and row_strain != (
                    normalize_strain_name(self.strain_filter).strip().lower()
                ):
                    continue
                if self.brand_filter != "All Brands":
                    if self._compatible_brand(row) != self.brand_filter:
                        continue
                    if self.sku_filter != "All SKU Types":
                        eligible_strains = {
                            normalize_strain_name(
                                str(item.get("Strain", ""))
                            ).strip().lower()
                            for item in self.velocity
                            if str(item.get("Brand", "")) == self.brand_filter
                            and str(item.get("SKU Type", "")) == self.sku_filter
                        }
                        if row_strain not in eligible_strains:
                            continue
                search = self.search_text.strip().lower()
                if search and search not in " ".join(
                    str(value or "") for value in row.values()
                ).lower():
                    continue
            elif not self._matches(row):
                continue
            if (
                self.executive_facility_filter != "All Facilities"
                and row.get("Current Facility") != self.executive_facility_filter
            ):
                continue
            ownership = str(row.get("Ownership Status", ""))
            if self.executive_ownership_filter == "QCC-Owned Inventory":
                if not ownership.startswith("QCC-Owned"):
                    continue
            elif (
                self.executive_ownership_filter != "All Ownership Statuses"
                and ownership != self.executive_ownership_filter
            ):
                continue
            filtered.append(row)
        return filtered

    @staticmethod
    def _number(row: dict[str, Any], column: str) -> float:
        try:
            return float(row.get(column, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _weight_label(weight_grams: float) -> str:
        if weight_grams >= 453.59237:
            return f"{weight_grams / 453.59237:,.1f} lb"
        return f"{weight_grams:,.1f} g"

    def _inventory_weight_value(self, weight_grams: float) -> float:
        if self.inventory_weight_unit == "Grams":
            return round(weight_grams, 2)
        return round(weight_grams / 453.59237, 2)

    def _inventory_weight_label(self, weight_grams: float) -> str:
        if self.inventory_weight_unit == "Grams":
            return f"{weight_grams:,.1f} g"
        return f"{weight_grams / 453.59237:,.1f} lb"

    def _inventory_columns_for_view(self, view_name: str) -> list[str]:
        unit = "g" if self.inventory_weight_unit == "Grams" else "lb"
        identity_columns = ["Brand"]
        if view_name in {"bulk", "wip", "aging_bulk"}:
            identity_columns = ["Compatible Brand"]
        elif view_name == "all":
            identity_columns = ["Brand", "Compatible Brand"]
        columns = [
            *identity_columns, "Strain", "SKU Type", "Unit Count",
            f"Total Weight ({unit})", "Age (Days)", "Location",
            "QA Status", "Metrc Tag",
        ]
        if view_name == "all":
            columns.insert(columns.index("Location"), "Packaged Date")
            columns.insert(columns.index("QA Status"), "Source Harvest")
        return columns

    @rx.var(cache=True)
    def inventory_columns(self) -> list[str]:
        return self._inventory_columns_for_view(self.inventory_view_name)

    @rx.var(cache=True)
    def inventory_grouping_caption(self) -> str:
        if self.inventory_view_name in {"bulk", "wip", "aging_bulk"}:
            identity = "Compatible Brand, Strain, and SKU Type"
        elif self.inventory_view_name == "all":
            identity = "Brand, Compatible Brand, Strain, and SKU Type"
        else:
            identity = "Brand, Strain, and SKU Type"
        return (
            "Detailed mode shows one row per Metrc package. Summary mode groups "
            f"exact {identity} combinations, totals units and weight, and uses "
            "the oldest package age. Summary weight above always remains in pounds."
        )

    @rx.var(cache=True)
    def inventory_weight_metric_label(self) -> str:
        return "Filtered Total Weight (lb)"

    @rx.var(cache=True)
    def inventory_weight_caption(self) -> str:
        return "Filtered sum always displayed in pounds"

    @rx.var(cache=True)
    def executive_pulse_rows(self) -> list[dict[str, Any]]:
        return [row for row in self.business_pulse if self._matches(row)]

    @rx.var(cache=True)
    def executive_last_30_units(self) -> str:
        value = sum(self._number(row, "Units") for row in self.executive_pulse_rows)
        return f"{value:,.0f}"

    @rx.var(cache=True)
    def executive_last_30_value(self) -> str:
        value = sum(self._number(row, "Value") for row in self.executive_pulse_rows)
        return f"${value:,.0f}"

    @rx.var(cache=True)
    def executive_last_30_customers(self) -> str:
        values = {
            str(row.get("Customer License", "")).strip()
            for row in self.executive_pulse_rows
            if str(row.get("Customer License", "")).strip()
        }
        return f"{len(values):,}"

    @rx.var(cache=True)
    def executive_last_30_manifests(self) -> str:
        values = {
            str(row.get("Manifest", "")).strip()
            for row in self.executive_pulse_rows
            if str(row.get("Manifest", "")).strip()
        }
        return f"{len(values):,}"

    @rx.var(cache=True)
    def executive_inventory_rows(self) -> list[dict[str, Any]]:
        return [
            row for row in self._executive_inventory(self.all_inventory)
            if self._number(row, "Quantity") > 0
            and row.get("Production Stage") not in {
                "Retention Storage", "Secure Waste",
            }
        ]

    @rx.var(cache=True)
    def executive_cpg_rows(self) -> list[dict[str, Any]]:
        return [
            row for row in self._executive_inventory(
                self._inventory_view_rows("View CPG")
            )
            if self._number(row, "Quantity") > 0
            and row.get("Production Stage") == "Packaged Goods"
        ]

    @rx.var(cache=True)
    def executive_retention_rows(self) -> list[dict[str, Any]]:
        return [
            row for row in self._executive_inventory(
                self._inventory_view_rows("View CPG")
            )
            if self._number(row, "Quantity") > 0
            and row.get("Production Stage") == "Retention Storage"
        ]

    def _executive_stage_weight(self, stage: str) -> float:
        return sum(
            self._number(row, "Calculated Weight (g)")
            for row in self.executive_inventory_rows
            if row.get("Production Stage") == stage
        )

    @rx.var(cache=True)
    def executive_cpg_packages(self) -> str:
        return f"{len(self.executive_cpg_rows):,}"

    @rx.var(cache=True)
    def executive_cpg_units(self) -> str:
        units = sum(self._number(row, "Quantity") for row in self.executive_cpg_rows)
        return f"{units:,.0f}"

    @rx.var(cache=True)
    def executive_sellable_bulk_weight(self) -> str:
        total = sum(
            self._executive_stage_weight(stage)
            for stage in ("Sellable Bulk", "1A Sellable Bulk")
        )
        return self._weight_label(total)

    @rx.var(cache=True)
    def executive_wip_cultivation_weight(self) -> str:
        return self._weight_label(self._executive_stage_weight("WIP-Cultivation"))

    @rx.var(cache=True)
    def executive_wip_manufacturing_weight(self) -> str:
        return self._weight_label(self._executive_stage_weight("WIP-Manufacturing"))

    @rx.var(cache=True)
    def executive_pre_wip_weight(self) -> str:
        total = sum(
            self._executive_stage_weight(stage)
            for stage in (
                "Pre-WIP-Cultivation", "Pre-WIP-Purchased 1A", "Pre-WIP",
            )
        )
        return self._weight_label(total)

    @rx.var(cache=True)
    def executive_pre_wip_packages(self) -> str:
        count = sum(
            row.get("Production Stage") in {
                "Pre-WIP-Cultivation", "Pre-WIP-Purchased 1A", "Pre-WIP",
            }
            for row in self.executive_inventory_rows
        )
        return f"{count:,}"

    @rx.var(cache=True)
    def executive_pre_wip_summary(self) -> str:
        return f"{self.executive_pre_wip_packages} pkg / {self.executive_pre_wip_weight}"

    @rx.var(cache=True)
    def executive_retention_summary(self) -> str:
        units = sum(
            self._number(row, "Quantity") for row in self.executive_retention_rows
        )
        return f"{len(self.executive_retention_rows):,} pkg / {units:,.0f} units"

    @rx.var(cache=True)
    def executive_needs_review(self) -> str:
        rows = self._inventory_view_rows("View Needs Review")
        return f"{len(self._executive_inventory(rows)):,}"

    def _ownership_count(self, status: str) -> int:
        return sum(
            1 for row in self.executive_inventory_rows
            if row.get("Ownership Status") == status
        )

    @rx.var(cache=True)
    def executive_qcc_owned_packages(self) -> str:
        count = sum(
            1 for row in self.executive_inventory_rows
            if str(row.get("Ownership Status", "")).startswith("QCC-Owned")
        )
        return f"{count:,}"

    @rx.var(cache=True)
    def executive_partner_managed_packages(self) -> str:
        return f"{self._ownership_count('Partner-Owned / Compliance Managed'):,}"

    @rx.var(cache=True)
    def executive_purchased_1a_packages(self) -> str:
        count = sum(
            1 for row in self.executive_inventory_rows
            if row.get("Ownership Status") == "QCC-Owned / Purchased from Building 1A"
            and row.get("Current Facility") == "Building 33 (C9)"
        )
        return f"{count:,}"

    @rx.var(cache=True)
    def executive_aging_cpg_count(self) -> str:
        rows = self._executive_inventory(
            self._inventory_view_rows("View Aging CPG")
        )
        return f"{sum(self._number(row, 'Age') >= 75 for row in rows):,}"

    @rx.var(cache=True)
    def executive_aging_bulk_count(self) -> str:
        rows = self._executive_inventory(
            self._inventory_view_rows("View Aging Bulk")
        )
        return f"{sum(self._number(row, 'Age') >= 75 for row in rows):,}"

    def _executive_sku_units(self) -> dict[tuple[str, str, str], float]:
        totals: dict[tuple[str, str, str], float] = {}
        for row in self.executive_cpg_rows:
            key = (
                str(row.get("Brand", "")), str(row.get("Strain", "")),
                str(row.get("SKU Type", "")),
            )
            totals[key] = totals.get(key, 0.0) + self._number(row, "Quantity")
        return totals

    @rx.var(cache=True)
    def executive_action_data(self) -> list[dict[str, Any]]:
        use_filtered_inventory = (
            self.executive_facility_filter != "All Facilities"
            or self.executive_ownership_filter != "QCC-Owned Inventory"
        )
        inventory_units = self._executive_sku_units()
        result: list[dict[str, Any]] = []
        for row in self.filtered_velocity:
            average = self._number(row, "Avg Weekly Units")
            key = (
                str(row.get("Brand", "")), str(row.get("Strain", "")),
                str(row.get("SKU Type", "")),
            )
            current = (
                inventory_units.get(key, 0.0)
                if use_filtered_inventory
                else self._number(row, "Current Units")
            )
            weeks = current / average if average > 0 else 0.0
            if average <= 0 or (current > 0 and weeks > 4.0):
                continue
            status = "Current Stockout" if current <= 0 else "Under 4 Weeks Supply"
            result.append({
                "Brand": key[0], "Strain": key[1], "SKU Type": key[2],
                "Current Units": round(current, 2),
                "Avg Weekly Units": round(average, 2),
                "Weeks of Supply": round(weeks, 2), "Demand Status": status,
            })
        return sorted(
            result,
            key=lambda row: (
                0 if row["Demand Status"] == "Current Stockout" else 1,
                row["Weeks of Supply"], -row["Avg Weekly Units"],
            ),
        )

    @rx.var(cache=True)
    def executive_action_rows(self) -> list[list[Any]]:
        columns = [
            "Brand", "Strain", "SKU Type", "Current Units",
            "Avg Weekly Units", "Weeks of Supply", "Demand Status",
        ]
        return [[row.get(column, "") for column in columns] for row in self.executive_action_data]

    @rx.var(cache=True)
    def executive_stockout_count(self) -> str:
        return f"{sum(row['Demand Status'] == 'Current Stockout' for row in self.executive_action_data):,}"

    @rx.var(cache=True)
    def executive_low_supply_count(self) -> str:
        return f"{sum(row['Demand Status'] == 'Under 4 Weeks Supply' for row in self.executive_action_data):,}"

    @rx.var(cache=True)
    def inventory_stage_options(self) -> list[str]:
        return self._inventory_options("Production Stage", "All Production Stages")

    @rx.var(cache=True)
    def inventory_license_options(self) -> list[str]:
        return self._inventory_options("License", "All Licenses")

    @rx.var(cache=True)
    def inventory_qa_options(self) -> list[str]:
        return self._inventory_options("QA Status", "All QA Statuses")

    @rx.var(cache=True)
    def inventory_category_options(self) -> list[str]:
        return self._inventory_options("Category", "All Categories")

    @rx.var(cache=True)
    def inventory_location_options(self) -> list[str]:
        return self._inventory_options("Location", "All Locations")

    @rx.var(cache=True)
    def filtered_cpg_inventory(self) -> list[dict[str, Any]]:
        rows = self._filtered_inventory(
            self._inventory_view_rows("View CPG")
        )
        if not self.inventory_include_retention:
            rows = [
                row for row in rows
                if row.get("Production Stage") != "Retention Storage"
            ]
        return rows

    @rx.var(cache=True)
    def filtered_bulk_inventory(self) -> list[dict[str, Any]]:
        return self._filtered_inventory(
            self._inventory_view_rows("View Bulk")
        )

    @rx.var(cache=True)
    def filtered_wip_inventory(self) -> list[dict[str, Any]]:
        return self._filtered_inventory(
            self._inventory_view_rows("View WIP")
        )

    @staticmethod
    def _optional_number(row: dict[str, Any], column: str) -> float | None:
        value = pd.to_numeric(row.get(column), errors="coerce")
        return None if pd.isna(value) else float(value)

    def _cpg_risk_band(self, row: dict[str, Any]) -> str:
        days = self._optional_number(row, "Days to Spoil")
        if days is None:
            return "Expiration Needs Review"
        if days <= 0:
            return "Expired"
        if days <= 30:
            return "0-30 Days Remaining"
        if days <= 60:
            return "31-60 Days Remaining"
        if days <= 90:
            return "61-90 Days Remaining"
        return "More Than 90 Days Remaining"

    def _bulk_age_band(self, row: dict[str, Any]) -> str:
        age = self._optional_number(row, "Age")
        if age is None:
            return "Age Needs Review"
        if age > 225:
            return "Over 225 Days"
        if age >= 180:
            return "180-225 Days"
        if age >= 91:
            return "91-179 Days"
        if age >= 60:
            return "60-90 Days"
        return "59 Days or Less"

    @rx.var(cache=True)
    def aging_cpg_base(self) -> list[dict[str, Any]]:
        return self._filtered_inventory(
            self._inventory_view_rows("View Aging CPG")
        )

    @rx.var(cache=True)
    def aging_bulk_base(self) -> list[dict[str, Any]]:
        return self._filtered_inventory(
            self._inventory_view_rows("View Aging Bulk")
        )

    @rx.var(cache=True)
    def filtered_aging_cpg(self) -> list[dict[str, Any]]:
        if self.aging_cpg_band_filter == "All Risk Bands":
            return self.aging_cpg_base
        return [
            row for row in self.aging_cpg_base
            if self._cpg_risk_band(row) == self.aging_cpg_band_filter
        ]

    @rx.var(cache=True)
    def filtered_aging_bulk(self) -> list[dict[str, Any]]:
        if self.aging_bulk_band_filter == "All Age Bands":
            return self.aging_bulk_base
        return [
            row for row in self.aging_bulk_base
            if self._bulk_age_band(row) == self.aging_bulk_band_filter
        ]

    def _aging_distribution(
        self,
        rows: list[dict[str, Any]],
        bands: list[tuple[str, str]],
        classifier: Any,
    ) -> list[dict[str, Any]]:
        counts = {label: 0 for label, _ in bands}
        weights = {label: 0.0 for label, _ in bands}
        for row in rows:
            label = classifier(row)
            counts[label] = counts.get(label, 0) + 1
            weights[label] = weights.get(label, 0.0) + self._number(
                row, "Calculated Weight (g)"
            )
        maximum = max(counts.values(), default=0) or 1
        colors = dict(bands)
        return [
            {
                "Band": label,
                "Packages": counts.get(label, 0),
                "Weight": self._inventory_weight_label(weights.get(label, 0.0)),
                "Width": f"{max(3, counts.get(label, 0) / maximum * 100):.1f}%",
                "Color": colors[label],
            }
            for label, _ in bands
        ]

    @rx.var(cache=True)
    def aging_cpg_distribution(self) -> list[dict[str, Any]]:
        return self._aging_distribution(
            self.aging_cpg_base,
            [
                ("Expired", "#dc2626"),
                ("0-30 Days Remaining", "#ea580c"),
                ("31-60 Days Remaining", "#f97316"),
                ("61-90 Days Remaining", "#fb923c"),
                ("More Than 90 Days Remaining", "#16a34a"),
                ("Expiration Needs Review", "#64748b"),
            ],
            self._cpg_risk_band,
        )

    @rx.var(cache=True)
    def aging_bulk_distribution(self) -> list[dict[str, Any]]:
        return self._aging_distribution(
            self.aging_bulk_base,
            [
                ("Over 225 Days", "#dc2626"),
                ("180-225 Days", "#ea580c"),
                ("91-179 Days", "#f97316"),
                ("60-90 Days", "#fb923c"),
                ("59 Days or Less", "#16a34a"),
                ("Age Needs Review", "#64748b"),
            ],
            self._bulk_age_band,
        )

    @rx.var(cache=True)
    def filtered_all_inventory(self) -> list[dict[str, Any]]:
        return self._filtered_inventory(self.all_inventory)

    @rx.var(cache=True)
    def filtered_needs_review(self) -> list[dict[str, Any]]:
        return self._filtered_inventory(
            self._inventory_view_rows("View Needs Review")
        )

    @rx.var(cache=True)
    def cpg_inventory_rows(self) -> list[list[Any]]:
        return self._inventory_rows(
            self.filtered_cpg_inventory, self.summarize_cpg_inventory, "cpg"
        )

    @rx.var(cache=True)
    def bulk_inventory_rows(self) -> list[list[Any]]:
        return self._inventory_rows(
            self.filtered_bulk_inventory, self.summarize_bulk_inventory, "bulk"
        )

    @rx.var(cache=True)
    def wip_inventory_rows(self) -> list[list[Any]]:
        return self._inventory_rows(
            self.filtered_wip_inventory, self.summarize_wip_inventory, "wip"
        )

    @rx.var(cache=True)
    def aging_cpg_rows(self) -> list[list[Any]]:
        return self._inventory_rows(
            self.filtered_aging_cpg, self.summarize_aging_cpg, "aging_cpg"
        )

    @rx.var(cache=True)
    def aging_bulk_rows(self) -> list[list[Any]]:
        return self._inventory_rows(
            self.filtered_aging_bulk, self.summarize_aging_bulk, "aging_bulk"
        )

    @rx.var(cache=True)
    def all_inventory_rows(self) -> list[list[Any]]:
        return self._inventory_rows(
            self.filtered_all_inventory, self.summarize_all_inventory, "all"
        )

    @rx.var(cache=True)
    def needs_review_rows(self) -> list[list[Any]]:
        return self._inventory_rows(
            self.filtered_needs_review, self.summarize_needs_review, "review"
        )

    def _unit_count(self, row: dict[str, Any]) -> float:
        unit = str(row.get("Unit", "")).strip().lower()
        stage = str(row.get("Production Stage", "")).strip()
        each_based = unit in {
            "ea", "each", "unit", "units", "count", "counts",
        } or stage in {"Packaged Goods", "Retention Storage"}
        return self._number(row, "Quantity") if each_based else 0.0

    @staticmethod
    def _mixed_label(values: set[str]) -> str:
        cleaned = sorted(value for value in values if value)
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        return f"Multiple ({len(cleaned)})"

    def _inventory_identity_values(
        self, row: dict[str, Any], view_name: str
    ) -> list[str]:
        brand = str(row.get("Brand", "") or "")
        compatible = self._compatible_brand(row)
        if view_name in {"bulk", "wip", "aging_bulk"}:
            return [compatible]
        if view_name == "all":
            return [brand, compatible]
        return [brand]

    def _inventory_rows(
        self,
        rows: list[dict[str, Any]],
        summarize: bool = False,
        view_name: str | None = None,
    ) -> list[list[Any]]:
        selected_view = view_name or self.inventory_view_name
        if summarize:
            groups: dict[tuple[str, ...], dict[str, Any]] = {}
            for row in rows:
                key = tuple([
                    *self._inventory_identity_values(row, selected_view),
                    str(row.get("Strain", "") or ""),
                    str(row.get("SKU Type", "") or ""),
                ])
                group = groups.setdefault(key, {
                    "units": 0.0,
                    "weight": 0.0,
                    "oldest_age": 0.0,
                    "packaged_dates": set(),
                    "locations": set(),
                    "source_harvests": set(),
                    "qa_statuses": set(),
                    "tags": set(),
                })
                group["units"] += self._unit_count(row)
                group["weight"] += self._number(
                    row, "Calculated Weight (g)"
                )
                group["oldest_age"] = max(
                    group["oldest_age"], self._number(row, "Age")
                )
                group["packaged_dates"].add(
                    str(row.get("Packaged Date", "") or "")
                )
                group["locations"].add(str(row.get("Location", "") or ""))
                group["source_harvests"].add(
                    str(row.get("Source Harvest", "") or "")
                )
                group["qa_statuses"].add(str(row.get("QA Status", "") or ""))
                tag = str(row.get("Metrc Tag", "") or "")
                if tag:
                    group["tags"].add(tag)
            return [
                (
                    [
                    *key, round(group["units"], 2),
                    self._inventory_weight_value(group["weight"]),
                    round(group["oldest_age"], 1),
                    ]
                    + (
                        [self._mixed_label(group["packaged_dates"])]
                        if selected_view == "all"
                        else []
                    )
                    + [
                    self._mixed_label(group["locations"]),
                    ]
                    + (
                        [self._mixed_label(group["source_harvests"])]
                        if selected_view == "all"
                        else []
                    )
                    + [
                        self._mixed_label(group["qa_statuses"]),
                        f"{len(group['tags']):,} package tag(s)",
                    ]
                )
                for key, group in sorted(groups.items())
            ]
        return [
            (
                [
                    *self._inventory_identity_values(row, selected_view),
                    str(row.get("Strain", "") or ""),
                    str(row.get("SKU Type", "") or ""),
                    round(self._unit_count(row), 2),
                    self._inventory_weight_value(
                        self._number(row, "Calculated Weight (g)")
                    ),
                    round(self._number(row, "Age"), 1),
                ]
                + (
                    [str(row.get("Packaged Date", "") or "")]
                    if selected_view == "all"
                    else []
                )
                + [
                    str(row.get("Location", "") or ""),
                ]
                + (
                    [str(row.get("Source Harvest", "") or "")]
                    if selected_view == "all"
                    else []
                )
                + [
                    str(row.get("QA Status", "") or ""),
                    str(row.get("Metrc Tag", "") or ""),
                ]
            )
            for row in rows
        ]

    def _filtered_unit_total(self, rows: list[dict[str, Any]]) -> str:
        return f"{sum(self._unit_count(row) for row in rows):,.0f}"

    def _filtered_weight_total(self, rows: list[dict[str, Any]]) -> str:
        weight = sum(
            self._number(row, "Calculated Weight (g)") for row in rows
        )
        return f"{weight / 453.59237:,.1f} lb"

    @rx.var(cache=True)
    def cpg_inventory_count(self) -> str:
        return f"{len(self.filtered_cpg_inventory):,} records"

    @rx.var(cache=True)
    def cpg_inventory_units(self) -> str:
        return self._filtered_unit_total(self.filtered_cpg_inventory)

    @rx.var(cache=True)
    def cpg_inventory_weight(self) -> str:
        return self._filtered_weight_total(self.filtered_cpg_inventory)

    @rx.var(cache=True)
    def excluded_retention_count(self) -> str:
        if self.inventory_include_retention:
            return "0"
        rows = self._filtered_inventory(
            self._inventory_view_rows("View CPG")
        )
        count = sum(
            row.get("Production Stage") == "Retention Storage"
            for row in rows
        )
        return f"{count:,}"

    @rx.var(cache=True)
    def bulk_inventory_count(self) -> str:
        return f"{len(self.filtered_bulk_inventory):,} records"

    @rx.var(cache=True)
    def bulk_inventory_units(self) -> str:
        return self._filtered_unit_total(self.filtered_bulk_inventory)

    @rx.var(cache=True)
    def bulk_inventory_weight(self) -> str:
        return self._filtered_weight_total(self.filtered_bulk_inventory)

    @rx.var(cache=True)
    def wip_inventory_count(self) -> str:
        return f"{len(self.filtered_wip_inventory):,} records"

    @rx.var(cache=True)
    def wip_inventory_weight(self) -> str:
        return self._filtered_weight_total(self.filtered_wip_inventory)

    @rx.var(cache=True)
    def wip_inventory_units(self) -> str:
        return self._filtered_unit_total(self.filtered_wip_inventory)

    @rx.var(cache=True)
    def filtered_pre_wip_inventory(self) -> list[dict[str, Any]]:
        return [
            row for row in self.filtered_wip_inventory
            if row.get("Production Stage") in {
                "Pre-WIP-Cultivation", "Pre-WIP-Purchased 1A",
                "Pre-WIP-Manufacturing",
            }
        ]

    def _inventory_stage_rows(self, stage: str) -> list[dict[str, Any]]:
        return [
            row for row in self.filtered_wip_inventory
            if row.get("Production Stage") == stage
        ]

    @staticmethod
    def _stage_package_weight_summary(
        rows: list[dict[str, Any]], stage: str
    ) -> str:
        stage_rows = [
            row for row in rows if row.get("Production Stage") == stage
        ]
        weight = sum(
            DashboardState._number(row, "Calculated Weight (g)")
            for row in stage_rows
        )
        return f"{len(stage_rows):,} pkg / {weight / 453.59237:,.1f} lb"

    def _inventory_stage_summary(self, stage: str) -> str:
        return self._stage_package_weight_summary(
            self.filtered_wip_inventory, stage
        )

    @rx.var(cache=True)
    def cultivation_wip_summary(self) -> str:
        _ = self.filtered_wip_inventory
        return self._inventory_stage_summary("WIP-Cultivation")

    @rx.var(cache=True)
    def cultivation_pre_wip_summary(self) -> str:
        _ = self.filtered_wip_inventory
        return self._inventory_stage_summary("Pre-WIP-Cultivation")

    @rx.var(cache=True)
    def manufacturing_wip_summary(self) -> str:
        _ = self.filtered_wip_inventory
        return self._inventory_stage_summary("WIP-Manufacturing")

    @rx.var(cache=True)
    def manufacturing_pre_wip_summary(self) -> str:
        _ = self.filtered_wip_inventory
        return self._inventory_stage_summary("Pre-WIP-Manufacturing")

    @rx.var(cache=True)
    def all_inventory_cultivation_pre_wip_summary(self) -> str:
        return self._stage_package_weight_summary(
            self.filtered_all_inventory, "Pre-WIP-Cultivation"
        )

    @rx.var(cache=True)
    def all_inventory_cultivation_wip_summary(self) -> str:
        return self._stage_package_weight_summary(
            self.filtered_all_inventory, "WIP-Cultivation"
        )

    @rx.var(cache=True)
    def all_inventory_manufacturing_pre_wip_summary(self) -> str:
        return self._stage_package_weight_summary(
            self.filtered_all_inventory, "Pre-WIP-Manufacturing"
        )

    @rx.var(cache=True)
    def all_inventory_manufacturing_wip_summary(self) -> str:
        return self._stage_package_weight_summary(
            self.filtered_all_inventory, "WIP-Manufacturing"
        )

    @rx.var(cache=True)
    def all_inventory_cpg_units_summary(self) -> str:
        rows = [
            row for row in self.filtered_all_inventory
            if row.get("Production Stage") == "Packaged Goods"
        ]
        return self._filtered_unit_total(rows)

    def _all_inventory_cpg_weight_by_license(self, license_type: str) -> str:
        rows = [
            row for row in self.filtered_all_inventory
            if row.get("Production Stage") == "Packaged Goods"
            and str(row.get("License", "")).strip().casefold()
            == license_type.casefold()
        ]
        return self._filtered_weight_total(rows)

    @rx.var(cache=True)
    def all_inventory_cpg_flower_weight_summary(self) -> str:
        return self._all_inventory_cpg_weight_by_license("Cultivation")

    @rx.var(cache=True)
    def all_inventory_cpg_manufacturing_weight_summary(self) -> str:
        return self._all_inventory_cpg_weight_by_license("Manufacturing")

    @rx.var(cache=True)
    def pre_wip_inventory_count(self) -> str:
        return f"{len(self.filtered_pre_wip_inventory):,}"

    @rx.var(cache=True)
    def pre_wip_inventory_weight(self) -> str:
        weight = sum(
            self._number(row, "Calculated Weight (g)")
            for row in self.filtered_pre_wip_inventory
        )
        return f"{weight / 453.59237:,.1f} lb"

    @rx.var(cache=True)
    def aging_cpg_count(self) -> str:
        return f"{len(self.filtered_aging_cpg):,} records"

    @rx.var(cache=True)
    def aging_cpg_units(self) -> str:
        return self._filtered_unit_total(self.filtered_aging_cpg)

    @rx.var(cache=True)
    def aging_cpg_weight(self) -> str:
        return self._filtered_weight_total(self.filtered_aging_cpg)

    @rx.var(cache=True)
    def aging_bulk_count(self) -> str:
        return f"{len(self.filtered_aging_bulk):,} records"

    @rx.var(cache=True)
    def aging_bulk_units(self) -> str:
        return self._filtered_unit_total(self.filtered_aging_bulk)

    @rx.var(cache=True)
    def aging_bulk_weight(self) -> str:
        return self._filtered_weight_total(self.filtered_aging_bulk)

    @rx.var(cache=True)
    def all_inventory_count(self) -> str:
        return f"{len(self.filtered_all_inventory):,} records"

    @rx.var(cache=True)
    def all_inventory_units(self) -> str:
        return self._filtered_unit_total(self.filtered_all_inventory)

    @rx.var(cache=True)
    def all_inventory_weight(self) -> str:
        return self._filtered_weight_total(self.filtered_all_inventory)

    @rx.var(cache=True)
    def needs_review_count(self) -> str:
        return f"{len(self.filtered_needs_review):,} records"

    @rx.var(cache=True)
    def needs_review_units(self) -> str:
        return self._filtered_unit_total(self.filtered_needs_review)

    @rx.var(cache=True)
    def needs_review_weight(self) -> str:
        return self._filtered_weight_total(self.filtered_needs_review)

    @rx.var(cache=True)
    def active_inventory_data(self) -> list[dict[str, Any]]:
        """Full filtered records for totals, exports, and server paging."""
        if self.inventory_view_name == "bulk":
            return self.filtered_bulk_inventory
        if self.inventory_view_name == "wip":
            return self.filtered_wip_inventory
        if self.inventory_view_name == "aging_cpg":
            return self.filtered_aging_cpg
        if self.inventory_view_name == "aging_bulk":
            return self.filtered_aging_bulk
        if self.inventory_view_name == "all":
            return self.filtered_all_inventory
        if self.inventory_view_name == "review":
            return self.filtered_needs_review
        return self.filtered_cpg_inventory

    @rx.var(cache=True)
    def active_inventory_summarize(self) -> bool:
        if self.inventory_view_name == "bulk":
            return self.summarize_bulk_inventory
        if self.inventory_view_name == "wip":
            return self.summarize_wip_inventory
        if self.inventory_view_name == "aging_cpg":
            return self.summarize_aging_cpg
        if self.inventory_view_name == "aging_bulk":
            return self.summarize_aging_bulk
        if self.inventory_view_name == "all":
            return self.summarize_all_inventory
        if self.inventory_view_name == "review":
            return self.summarize_needs_review
        return self.summarize_cpg_inventory

    @rx.var(cache=True)
    def active_inventory_all_rows(self) -> list[list[Any]]:
        # Reuse the per-view cached row matrices instead of converting the
        # active inventory records again on every tab change. This keeps the
        # current client-side search, sorting, and pagination behavior while
        # making repeat navigation between Inventory tabs substantially
        # cheaper for both the Reflex server and the browser.
        if self.inventory_view_name == "bulk":
            return self.bulk_inventory_rows
        if self.inventory_view_name == "wip":
            return self.wip_inventory_rows
        if self.inventory_view_name == "aging_cpg":
            return self.aging_cpg_rows
        if self.inventory_view_name == "aging_bulk":
            return self.aging_bulk_rows
        if self.inventory_view_name == "all":
            return self.all_inventory_rows
        if self.inventory_view_name == "review":
            return self.needs_review_rows
        return self.cpg_inventory_rows

    def _prewarm_slowest_inventory_views(self) -> tuple[int, int, float]:
        """Prime the two largest measured Inventory row-matrix caches."""
        started_at = perf_counter()
        all_count = len(self.all_inventory_rows)
        aging_bulk_count = len(self.aging_bulk_rows)
        return (
            all_count,
            aging_bulk_count,
            (perf_counter() - started_at) * 1000,
        )

    @rx.var(cache=True)
    def active_inventory_rows(self) -> list[list[Any]]:
        # The table owns paging so changing 10/25/50/100 immediately changes
        # the visible row count without a second server-side pager.
        return self.active_inventory_all_rows

    @rx.var(cache=True)
    def inventory_total_pages(self) -> int:
        count = len(self.active_inventory_all_rows)
        return max((count + self.inventory_page_size - 1) // self.inventory_page_size, 1)

    @rx.var(cache=True)
    def inventory_page_label(self) -> str:
        page = min(max(self.inventory_page, 1), self.inventory_total_pages)
        count = len(self.active_inventory_all_rows)
        if count == 0:
            return "No matching rows"
        start = (page - 1) * self.inventory_page_size + 1
        end = min(page * self.inventory_page_size, count)
        return (
            f"Rows {start:,}-{end:,} of {count:,} · "
            f"Page {page:,} of {self.inventory_total_pages:,}"
        )

    @rx.var(cache=True)
    def inventory_page_size_value(self) -> str:
        return str(self.inventory_page_size)

    @rx.var(cache=True)
    def inventory_pagination(self) -> dict[str, int]:
        # Grid.js calls its page-size option `limit`.
        return {"limit": self.inventory_page_size}

    @rx.var(cache=True)
    def inventory_previous_disabled(self) -> bool:
        return self.inventory_page <= 1

    @rx.var(cache=True)
    def inventory_next_disabled(self) -> bool:
        return self.inventory_page >= self.inventory_total_pages

    @rx.var(cache=True)
    def active_inventory_count(self) -> str:
        return f"{len(self.active_inventory_data):,} records"

    @rx.var(cache=True)
    def active_inventory_units(self) -> str:
        return self._filtered_unit_total(self.active_inventory_data)

    @rx.var(cache=True)
    def active_inventory_samples(self) -> str:
        count = sum(
            bool(re.search(r"\bsamples?\b", str(row.get("Item", "")), re.I))
            and not bool(row.get("View Needs Review", False))
            for row in self.active_inventory_data
        )
        return f"{count:,}"

    @rx.var(cache=True)
    def active_inventory_shows_samples(self) -> bool:
        return self.inventory_view_name in {"cpg", "aging_cpg"}

    @rx.var(cache=True)
    def active_inventory_weight(self) -> str:
        return self._filtered_weight_total(self.active_inventory_data)

    @rx.event
    def change_inventory_view(self, value: str):
        # Radix can repeat on_change when a controlled tab receives its newly
        # selected value. Ignore that no-op so it neither rebuilds state nor
        # overwrites the real navigation measurement with "X to X".
        if value == self.inventory_view_name:
            return
        previous_view = self.inventory_view_name
        started_at = perf_counter()
        self.inventory_view_name = value
        self.inventory_page = 1
        # Flush the actual tab/table update first. When the generator resumes,
        # Reflex has completed the server-side state-delta preparation for the
        # navigation event; browser paint time is intentionally not included.
        yield

        server_update_ms = (perf_counter() - started_at) * 1000
        rows = self.active_inventory_all_rows
        view_labels = {
            "cpg": "CPG Inventory",
            "bulk": "Bulk Inventory",
            "wip": "WIP & Pre-WIP",
            "aging_cpg": "Aging Risk CPG",
            "aging_bulk": "Aging Risk Bulk",
            "all": "All Inventory",
            "review": "Needs Review",
        }
        diagnostic = (
            f"{view_labels.get(previous_view, previous_view)} to "
            f"{view_labels.get(value, value)} | server update "
            f"{server_update_ms:,.0f} ms | {len(rows):,} table rows"
        )
        print(
            "INVENTORY_NAV_DIAGNOSTIC "
            + diagnostic,
            flush=True,
        )

    @rx.event
    def previous_inventory_page(self):
        self.inventory_page = max(self.inventory_page - 1, 1)

    @rx.event
    def next_inventory_page(self):
        self.inventory_page = min(
            self.inventory_page + 1, self.inventory_total_pages
        )

    @rx.event
    def change_inventory_page_size(self, value: str):
        try:
            self.inventory_page_size = int(value)
        except (TypeError, ValueError):
            self.inventory_page_size = 10
        self.inventory_page = 1

    @rx.event
    def change_active_inventory_summarize(self, value: bool):
        if self.inventory_view_name == "bulk":
            self.summarize_bulk_inventory = value
        elif self.inventory_view_name == "wip":
            self.summarize_wip_inventory = value
        elif self.inventory_view_name == "aging_cpg":
            self.summarize_aging_cpg = value
        elif self.inventory_view_name == "aging_bulk":
            self.summarize_aging_bulk = value
        elif self.inventory_view_name == "all":
            self.summarize_all_inventory = value
        elif self.inventory_view_name == "review":
            self.summarize_needs_review = value
        else:
            self.summarize_cpg_inventory = value
        self.inventory_page = 1

    @rx.event
    def download_active_inventory(self):
        labels = {
            "cpg": "cpg_inventory", "bulk": "bulk_inventory",
            "wip": "wip_pre_wip", "aging_cpg": "aging_cpg",
            "aging_bulk": "aging_bulk", "all": "all_inventory",
            "review": "needs_review",
        }
        return self._download_inventory_view(
            self.active_inventory_data,
            self.active_inventory_summarize,
            labels.get(self.inventory_view_name, "inventory"),
        )

    @rx.var(cache=True)
    def top_sku_rows(self) -> list[list[Any]]:
        return [[
            row.get("Brand", ""), row.get("Strain", ""),
            row.get("SKU Type", ""), row.get("Units", 0),
            row.get("Value", 0), row.get("Customers", 0),
        ] for row in self.filtered_top_skus]

    @rx.var(cache=True)
    def executive_action_page_size(self) -> int:
        return int(self.executive_action_rows_per_page)

    @rx.var(cache=True)
    def top_sku_page_size(self) -> int:
        return int(self.top_sku_rows_per_page)

    @rx.var(cache=True)
    def stockout_page_size(self) -> int:
        return int(self.stockout_rows_per_page)

    @rx.var(cache=True)
    def customer_page_size(self) -> int:
        return int(self.customer_rows_per_page)

    @rx.var(cache=True)
    def transfer_import_page_size(self) -> int:
        return int(self.transfer_import_rows_per_page)

    @rx.var(cache=True)
    def velocity_rows(self) -> list[list[Any]]:
        return [[
            str(row.get("Brand", "") or ""), str(row.get("Strain", "") or ""),
            str(row.get("SKU Type", "") or ""), f"{float(row.get('Units Shipped', 0) or 0):,.0f}",
            f"{float(row.get('Avg Weekly Units', 0) or 0):,.1f}",
            f"{float(row.get('Packages', 0) or 0):,.0f}",
            f"{float(row.get('Current Units', 0) or 0):,.0f}",
            f"{float(row.get('Weeks of Supply', 0) or 0):,.1f}",
            str(row.get("Potential Matching WIP", "") or ""),
            str(row.get("Committed WIP", "") or ""),
            str(row.get("Matching Pre-WIP Weight", "") or ""),
            f"{float(row.get('Customers', 0) or 0):,.0f}", str(row.get("Demand Status", "") or ""),
            str(row.get("Last Shipped", "") or ""),
            str(row.get("Lifecycle Status", "") or ""),
        ] for row in self.filtered_velocity]

    @rx.var(cache=True)
    def stockout_rows(self) -> list[list[Any]]:
        return [[
            row.get("Brand", ""), row.get("Strain", ""),
            row.get("SKU Type", ""),
            round(float(row.get("Avg Weekly Units", 0) or 0), 1),
            row.get("Current Units", 0),
            round(float(row.get("Weeks of Supply", 0) or 0), 1),
            row.get("Demand Status", ""),
            row.get("Last Shipped", ""),
            row.get("Lifecycle Status", ""),
            "Confirm availability and prioritize production",
        ] for row in self.filtered_stockouts]

    @rx.var(cache=True)
    def saved_plan_rows(self) -> list[list[Any]]:
        return [[
            row.get("Target Date", ""), row.get("Plan Name", ""),
            row.get("Status", ""), row.get("Department", ""),
            row.get("Brand", ""), row.get("Strain", ""),
            row.get("SKU Type", ""), row.get("Allocation %", 0),
            row.get("Projected Units", 0), row.get("Batch Weight (g)", 0),
        ] for row in self.filtered_saved_plans]

    @rx.var(cache=True)
    def calendar_rows(self) -> list[list[Any]]:
        return [[
            row.get("Target Date", ""), row.get("Plan Name", ""),
            row.get("Production Line", ""), row.get("Status", ""),
            row.get("Department", ""),
            row.get("Output Summary", ""),
        ] for row in self.filtered_calendar]

    @rx.var(cache=True)
    def customer_rows(self) -> list[list[Any]]:
        columns = [
            "Destination License", "Customer", "Units Shipped",
            "Shipment Value", "Manifests", "SKUs Purchased",
            "First Shipment", "Last Shipment", "Median Receipt Hours",
            "Average Manifest Value",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self.filtered_customers
        ]

    @rx.var(cache=True)
    def exception_rows(self) -> list[list[Any]]:
        columns = [
            "Manifest", "State", "Destination License", "Customer",
            "Created", "Received", "Packages", "Items", "Shipper Value",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self.filtered_exceptions
        ]

    @rx.var(cache=True)
    def exception_package_rows(self) -> list[list[Any]]:
        columns = [
            "Manifest", "State", "Destination License", "Customer",
            "Package Tag", "Metrc Item", "Brand", "Strain", "SKU Type",
            "Shipped Units", "Shipper Value", "Created", "Received",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self.filtered_exception_packages
        ]

    @rx.var(cache=True)
    def package_lineage_rows(self) -> list[list[Any]]:
        columns = [
            "Relationship", "Package Tag", "Item", "Strain",
            "Source Harvest", "Production Batch", "First Seen",
            "Last Seen", "Snapshots",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self.package_lineage_records
        ]

    @rx.var(cache=True)
    def package_lineage_timeline_rows(self) -> list[list[Any]]:
        columns = [
            "Date", "Event", "Package Tag", "Manifest",
            "Customer / Location", "Status", "Quantity", "Unit", "Item",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self.package_lineage_timeline
        ]

    @rx.var(cache=True)
    def transfer_rows(self) -> list[list[Any]]:
        columns = [
            "Manifest", "Invoice Number", "Created", "Received", "State",
            "Destination License", "Customer", "Package Tag", "Metrc Item",
            "Brand", "Strain", "SKU Type", "Shipped Units",
            "Shipper Value", "Demand Record",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self.transfer_page_data
        ]

    @rx.var(cache=True)
    def transfer_total_pages(self) -> int:
        count = self.transfer_server_total
        return max((count + self.transfer_page_size - 1) // self.transfer_page_size, 1)

    @rx.var(cache=True)
    def transfer_page_data(self) -> list[dict[str, Any]]:
        return self.filtered_transfer_data

    @rx.var(cache=True)
    def transfer_page_label(self) -> str:
        count = self.transfer_server_total
        if count == 0:
            return "No matching transfer rows"
        page = min(max(self.transfer_page, 1), self.transfer_total_pages)
        start = (page - 1) * self.transfer_page_size + 1
        end = min(page * self.transfer_page_size, count)
        return f"Rows {start:,}-{end:,} of {count:,} · Page {page:,} of {self.transfer_total_pages:,}"

    @rx.event
    def previous_transfer_page(self):
        self.transfer_page = max(self.transfer_page - 1, 1)
        yield DashboardState.load_distribution_operations_background

    @rx.event
    def next_transfer_page(self):
        self.transfer_page = min(
            self.transfer_page + 1, self.transfer_total_pages
        )
        yield DashboardState.load_distribution_operations_background

    @rx.var(cache=True)
    def import_log_rows(self) -> list[list[Any]]:
        columns = [
            "source_filename", "source_rows", "stored_rows",
            "inserted_rows", "updated_rows", "created_min", "created_max",
            "imported_at",
        ]
        return [
            [row.get(column, "") for column in columns]
            for row in self.transfer_import_log
        ]

    @rx.var(cache=True)
    def filtered_stockout_count(self) -> str:
        return f"{len(self.filtered_stockouts):,}"

    @staticmethod
    def _csv_bytes(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    @rx.event
    def download_saved_plans(self):
        return rx.download(
            data=self._csv_bytes(self.filtered_saved_plans),
            filename=f"qcc_reflex_saved_plans_{date.today().isoformat()}.csv",
        )

    @rx.event
    def download_velocity(self):
        return rx.download(
            data=self._csv_bytes(self.filtered_velocity),
            filename=f"qcc_reflex_sku_planning_{date.today().isoformat()}.csv",
        )

    @rx.event
    def download_stockouts(self):
        return rx.download(
            data=self._csv_bytes(self.filtered_stockouts),
            filename=f"qcc_reflex_stockouts_{date.today().isoformat()}.csv",
        )

    @rx.event
    def download_executive_actions(self):
        return rx.download(
            data=self._csv_bytes(self.executive_action_data),
            filename=f"qcc_reflex_executive_actions_{date.today().isoformat()}.csv",
        )

    @rx.event
    def download_cultivation_wip_report(self):
        self.executive_report_error = ""
        self.executive_report_message = "Building the four-sheet WIP report..."
        self.executive_report_building = True
        try:
            payload = self._wip_report_payload()
            if not payload["strains"]:
                raise ValueError(
                    "No cultivation WIP, scheduled supply, or Flower/Pre-Roll demand is loaded."
                )
            content = build_wip_rollforward_workbook(
                **payload,
                as_of=self.loaded_at,
                minimum_floor_lbs=5.0,
                excess_threshold_lbs=50.0,
            )
            self.executive_report_message = (
                f"Prepared four demand-model sheets for {len(payload['strains'])} strains."
            )
            return rx.download(
                data=content,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                filename=(
                    "qcc_cultivation_wip_rollforward_"
                    f"{date.today().isoformat()}.xlsx"
                ),
            )
        except Exception as error:
            self.executive_report_error = (
                "The cultivation WIP report could not be built: " + str(error)
            )
            self.executive_report_message = ""
        finally:
            self.executive_report_building = False

    @rx.event
    def download_customers(self):
        return rx.download(
            data=self._csv_bytes(self.filtered_customers),
            filename=f"qcc_reflex_customers_{date.today().isoformat()}.csv",
        )

    @rx.event
    def download_exceptions(self):
        return rx.download(
            data=self._csv_bytes(self.filtered_exceptions),
            filename=f"qcc_reflex_shipment_exceptions_{date.today().isoformat()}.csv",
        )

    @rx.event
    def download_exception_packages(self):
        return rx.download(
            data=self._csv_bytes(self.filtered_exception_packages),
            filename=(
                "qcc_reflex_shipment_exception_packages_"
                f"{date.today().isoformat()}.csv"
            ),
        )

    @rx.event
    def download_transfers(self):
        return rx.download(
            data=self._csv_bytes(self.filtered_transfer_data),
            filename=f"qcc_reflex_transfer_data_{date.today().isoformat()}.csv",
        )

    def _download_inventory(self, rows: list[dict[str, Any]], label: str):
        return rx.download(
            data=self._csv_bytes(rows),
            filename=f"qcc_reflex_{label}_{date.today().isoformat()}.csv",
        )

    def _download_inventory_view(
        self,
        rows: list[dict[str, Any]],
        summarize: bool,
        label: str,
        view_name: str | None = None,
    ):
        selected_view = view_name or self.inventory_view_name
        columns = self._inventory_columns_for_view(selected_view)
        rows = [
            dict(zip(columns, row))
            for row in self._inventory_rows(rows, summarize, selected_view)
        ]
        if summarize:
            label += "_sku_summary"
        return self._download_inventory(rows, label)

    @rx.event
    def download_cpg_inventory(self):
        return self._download_inventory_view(
            self.filtered_cpg_inventory, self.summarize_cpg_inventory,
            "cpg_inventory", "cpg",
        )

    @rx.event
    def download_bulk_inventory(self):
        return self._download_inventory_view(
            self.filtered_bulk_inventory, self.summarize_bulk_inventory,
            "bulk_inventory", "bulk",
        )

    @rx.event
    def download_wip_inventory(self):
        return self._download_inventory_view(
            self.filtered_wip_inventory, self.summarize_wip_inventory,
            "wip_pre_wip", "wip",
        )

    @rx.event
    def download_aging_cpg(self):
        return self._download_inventory_view(
            self.filtered_aging_cpg, self.summarize_aging_cpg,
            "aging_cpg", "aging_cpg",
        )

    @rx.event
    def download_aging_bulk(self):
        return self._download_inventory_view(
            self.filtered_aging_bulk, self.summarize_aging_bulk,
            "aging_bulk", "aging_bulk",
        )

    @rx.event
    def download_all_inventory(self):
        return self._download_inventory_view(
            self.filtered_all_inventory, self.summarize_all_inventory,
            "all_inventory", "all",
        )

    @rx.event
    def download_needs_review(self):
        return self._download_inventory_view(
            self.filtered_needs_review, self.summarize_needs_review,
            "needs_review", "review",
        )


def metric_card(label: str, value: rx.Var, caption: str) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(label, size="2", color=MUTED, weight="medium"),
            rx.heading(value, size="6", color=DARK),
            rx.text(caption, size="1", color=MUTED),
            spacing="1",
            align="start",
        ),
        width="100%",
        border_top=f"4px solid {ACCENT}",
        box_shadow="0 8px 24px rgba(15, 23, 42, 0.07)",
    )


def snapshot_stat_card(label: str, value: rx.Var, accent: str) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(label, size="1", color=MUTED, weight="bold"),
            rx.heading(value, size="5", color=DARK),
            spacing="1", align="start",
        ),
        width="100%",
        min_height="92px",
        padding="0.95rem 1.1rem",
        border_top=f"4px solid {accent}",
        box_shadow="0 5px 16px rgba(15, 23, 42, 0.06)",
    )


def executive_metric_card(
    label: str,
    value: rx.Var,
    caption: str,
    accent: str,
    tint: str,
) -> rx.Component:
    """A compact, color-coded executive metric card."""
    return rx.card(
        rx.vstack(
            rx.flex(
                rx.box(
                    width="10px", height="10px", border_radius="999px",
                    background=accent,
                ),
                rx.text(
                    label, size="2", color="#334155", weight="bold",
                    letter_spacing="0.01em",
                ),
                align="center", gap="2",
            ),
            rx.heading(value, size="7", color=DARK, line_height="1.05"),
            rx.text(caption, size="1", color=MUTED, line_height="1.35"),
            spacing="2",
            align="start",
        ),
        width="100%",
        min_height="142px",
        padding="1.15rem",
        background=tint,
        border=f"1px solid {accent}33",
        border_left=f"6px solid {accent}",
        box_shadow="0 8px 22px rgba(15, 23, 42, 0.06)",
    )


def executive_section(title: str, caption: str) -> rx.Component:
    return rx.box(
        rx.heading(title, size="5", color=DARK),
        rx.text(caption, size="2", color=MUTED),
        width="100%",
        padding_top="0.35rem",
    )


def data_grid(
    data: rx.Var,
    columns: list[str],
    height: str = "480px",
    show_search: bool = True,
    class_name: str = "",
    column_width: int = 165,
    minimum_width: int = 900,
    page_size: Any = 25,
    resizable: bool = True,
) -> rx.Component:
    table_width = max(minimum_width, len(columns) * column_width)
    return rx.box(
        rx.data_table(
            data=data,
            columns=columns,
            pagination={"limit": page_size},
            search=show_search,
            sort=True,
            resizable=resizable,
            height=height,
            width=f"{table_width}px",
            min_width=f"{table_width}px",
            key=class_name or "qcc-data-grid",
        ),
        class_name=class_name,
        width="100%",
        overflow_x="auto",
        border="1px solid #d8e0e8",
        border_radius="8px",
    )


def cultivation_history_data_grid(
    data: rx.Var,
    columns: list[str],
    height: str,
    class_name: str,
    column_width: int,
    minimum_width: int,
) -> rx.Component:
    """Historical grid with its row-limit control beside the Grid.js summary."""
    return rx.vstack(
        data_grid(
            data,
            columns,
            height=height,
            show_search=False,
            class_name=class_name,
            column_width=column_width,
            minimum_width=minimum_width,
            page_size=DashboardState.cultivation_history_page_size,
        ),
        rx.flex(
            rx.text("Rows", size="1", weight="bold", color=MUTED),
            rx.select(
                ["10", "25", "50", "100"],
                value=DashboardState.cultivation_history_rows_per_page,
                on_change=DashboardState.change_cultivation_history_rows_per_page,
                width="74px",
                size="1",
            ),
            class_name="qcc-historical-footer-row-control",
            align="center",
            gap="2",
        ),
        class_name="qcc-historical-grid-with-footer-control",
        align="start",
        spacing="2",
        width="100%",
    )


def cultivation_plant_data_grid(
    data: rx.Var,
    columns: list[str],
    height: str,
    minimum_width: int = 1100,
) -> rx.Component:
    """Metrc cultivation grid with the standard cultivation row selector."""
    return rx.vstack(
        data_grid(
            data,
            columns,
            height=height,
            show_search=True,
            class_name="qcc-cultivation-plant-grid",
            column_width=165,
            minimum_width=minimum_width,
            page_size=DashboardState.cultivation_plant_page_size,
        ),
        rx.flex(
            rx.text("Rows", size="1", weight="bold", color=MUTED),
            rx.select(
                ["10", "25", "50", "100"],
                value=DashboardState.cultivation_plant_rows_per_page,
                on_change=DashboardState.change_cultivation_plant_rows_per_page,
                width="82px",
                size="1",
            ),
            align="center",
            gap="2",
        ),
        align="start",
        spacing="2",
        width="100%",
    )


def historical_yield_table(
    data: rx.Var,
    columns: list[str],
    height: str,
) -> rx.Component:
    """Render bundled cultivation history without the Grid.js data adapter.

    The historical rows contain a deliberate mix of text and numeric cells.
    Grid.js can infer an empty client-side data set from that mixed schema even
    though the same state values render correctly in KPI cards and charts. A
    regular Radix table keeps those values intact and is fast for this data set.
    """
    table_width = max(900, len(columns) * 155)
    return rx.box(
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    *[
                        rx.table.column_header_cell(
                            column,
                            position="sticky",
                            top="0",
                            z_index="2",
                            background="#111827",
                            color="white",
                            white_space="normal",
                            line_height="1.15",
                            min_width="130px",
                        )
                        for column in columns
                    ]
                )
            ),
            rx.table.body(
                rx.foreach(
                    data,
                    lambda row: rx.table.row(
                        *[
                            rx.table.cell(
                                row[column],
                                white_space="normal" if column == "Notes" else "nowrap",
                                min_width="260px" if column == "Notes" else "130px",
                            )
                            for column in columns
                        ]
                    ),
                )
            ),
            width=f"{table_width}px",
            min_width=f"{table_width}px",
            variant="surface",
            size="2",
        ),
        width="100%",
        height=height,
        overflow="auto",
        border="1px solid #d8e0e8",
        border_radius="8px",
        background="white",
    )


def inventory_data_grid(data: rx.Var) -> rx.Component:
    """Wide sortable inventory grid that never truncates column headings."""
    return rx.box(
        rx.data_table(
            # Keep the same Grid.js instance mounted while tabs change. The
            # data and column props update normally; only a page-size change
            # intentionally remounts the grid so Grid.js applies the new
            # client-side pagination limit immediately.
            key=("inventory-table-" + DashboardState.inventory_page_size_value),
            data=data,
            columns=DashboardState.inventory_columns,
            pagination=DashboardState.inventory_pagination,
            search=True,
            sort=True,
            resizable=False,
            height="650px",
            width=rx.cond(
                DashboardState.inventory_view_name == "all",
                "2160px",
                "1900px",
            ),
            min_width=rx.cond(
                DashboardState.inventory_view_name == "all",
                "2160px",
                "1900px",
            ),
        ),
        class_name=rx.cond(
            DashboardState.inventory_view_name == "all",
            "qcc-inventory-grid qcc-all-inventory-grid",
            "qcc-inventory-grid",
        ),
        width="100%",
        overflow_x="auto",
        border="1px solid #d8e0e8",
        border_radius="8px",
    )


def aging_band_row(item: rx.Var, select_event: Any) -> rx.Component:
    return rx.button(
        rx.grid(
            rx.text(item["Band"], weight="bold", size="2", text_align="left"),
            rx.box(
                rx.box(
                    height="15px",
                    width=item["Width"],
                    background=item["Color"],
                    border_radius="999px",
                ),
                width="100%",
                background="#e2e8f0",
                border_radius="999px",
                overflow="hidden",
            ),
            rx.text(
                item["Packages"].to_string() + " packages · " + item["Weight"].to_string(),
                size="1", color=MUTED, text_align="right",
            ),
            columns="210px minmax(180px, 1fr) 170px",
            gap="3", align_items="center", width="100%",
        ),
        on_click=select_event(item["Band"]),
        variant="ghost",
        width="100%",
        height="auto",
        padding="0.4rem 0.55rem",
        color=DARK,
    )


def aging_distribution_card(
    title: str,
    caption: str,
    data: rx.Var,
    active_filter: rx.Var,
    all_label: str,
    select_event: Any,
) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.box(
                    rx.heading(title, size="3"),
                    rx.text(caption, size="1", color=MUTED),
                ),
                rx.spacer(),
                rx.badge("Filtered: " + active_filter, color_scheme="teal", size="2"),
                rx.button(
                    "Show All",
                    on_click=select_event(all_label),
                    variant="outline",
                    size="2",
                ),
                width="100%", align="center",
            ),
            rx.foreach(data, lambda item: aging_band_row(item, select_event)),
            width="100%", spacing="2",
        ),
        width="100%",
        border_top=f"4px solid {ACCENT}",
    )


READABLE_COLUMN_WIDTHS = {
    "Metrc Tag": 255,
    "Item": 280,
    "Brand": 145,
    "Strain": 190,
    "SKU Type": 220,
    "Production Stage": 180,
    "QA Status": 155,
    "Category": 205,
    "Location": 285,
    "Calculated Weight (g)": 180,
    "Calculated Weight": 175,
    "Material Type": 185,
    "Review Reason": 440,
    "Quantity": 120,
    "Unit": 95,
    "Available Weight (g)": 175,
    "Source Harvest": 240,
    "License": 150,
    "Demand Status": 235,
    "Potential Matching WIP": 205,
    "Matching Pre-WIP Weight": 210,
}


def readable_grid(
    data: rx.Var,
    columns: list[str],
    height: str = "560px",
    freeze_columns: int = 1,
) -> rx.Component:
    """Readable sortable grid using the stable HTML table renderer.

    The former canvas renderer treated every column as text and could receive
    numeric values after a Supabase refresh, causing a browser-side
    ``value.includes is not a function`` exception.  Keeping one renderer for
    all read-only grids avoids that mixed-type failure and adds consistent
    search, sorting, pagination, resizing, and horizontal scrolling.
    """
    del freeze_columns  # Retained in the signature for existing call sites.
    return data_grid(data, columns, height, show_search=True)


def package_lookup_detail_row(row: rx.Var[list[str]]) -> rx.Component:
    return rx.table.row(
        rx.table.row_header_cell(
            row[0],
            width="230px",
            min_width="230px",
            background="#f1f5f9",
            font_weight="600",
        ),
        rx.table.cell(row[1], min_width="420px", white_space="normal"),
    )


def package_lookup_detail_table() -> rx.Component:
    """Show every attribute for one package without pagination."""
    return rx.box(
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("Field", width="230px"),
                    rx.table.column_header_cell("Value", min_width="420px"),
                )
            ),
            rx.table.body(
                rx.foreach(
                    DashboardState.selected_inventory_details,
                    package_lookup_detail_row,
                )
            ),
            width="100%",
            variant="surface",
        ),
        width="100%",
        overflow_x="auto",
        border="1px solid #cbd5e1",
        border_radius="10px",
    )


PACKAGE_DETAIL_COLUMNS = [
    "Metrc Tag", "Item", "Production Stage", "Location", "QA Status",
    "Category", "Age", "Quantity", "Unit", "Calculated Weight (g)",
    "Available Weight (g)", "Source Harvest",
]


def sku_package_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(DashboardState.sku_detail_title),
            rx.dialog.description(DashboardState.sku_detail_message),
            rx.cond(
                DashboardState.selected_sku_package_details.length() > 0,
                readable_grid(
                    DashboardState.selected_sku_package_details,
                    PACKAGE_DETAIL_COLUMNS,
                    "420px",
                    1,
                ),
                rx.callout(
                    "No matching packages were found for this exact selection.",
                    icon="circle_help",
                    color_scheme="orange",
                    width="100%",
                ),
            ),
            rx.flex(
                rx.spacer(),
                rx.dialog.close(
                    rx.button("Close", on_click=DashboardState.close_sku_detail)
                ),
                width="100%",
                padding_top="0.75rem",
            ),
            max_width="95vw",
            width="1180px",
            class_name="qcc-compact-package-dialog",
        ),
        open=DashboardState.sku_detail_open,
        on_open_change=DashboardState.change_sku_detail_open,
    )


def native_filter_select(
    options: rx.Var,
    value: rx.Var,
    on_change: Any,
    width: str,
) -> rx.Component:
    """Aligned native dropdown with cumulative browser keyboard search."""
    return rx.el.select(
        rx.foreach(
            options,
            lambda option: rx.el.option(option, value=option),
        ),
        value=value,
        on_change=on_change,
        width=width,
        height="32px",
        padding="0 2rem 0 0.7rem",
        border="1px solid #cbd5e1",
        border_radius="6px",
        background="white",
        color=DARK,
        font_size="0.875rem",
        cursor="pointer",
    )


def filters() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.flex(
                rx.box(
                    rx.heading("Global Filters", size="4"),
                    rx.text(
                        "All selections apply to Sales & Demand Planning and Cannabis Inventory; Brand and Strain also filter Quality & Compliance.",
                        size="1",
                        color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.button(
                    rx.cond(
                        DashboardState.global_filters_resetting,
                        rx.hstack(
                            rx.spinner(size="1"), rx.text("Resetting..."), gap="2"
                        ),
                        "Reset Global Filters",
                    ),
                    on_click=DashboardState.reset_filters,
                    disabled=DashboardState.global_filters_resetting,
                    loading=DashboardState.global_filters_resetting,
                    variant="outline",
                ),
                rx.button(
                    "Refresh Supabase",
                    on_click=DashboardState.refresh,
                    loading=DashboardState.loading,
                    background=ACCENT,
                    color="white",
                ),
                align="center",
                gap="3",
                wrap="wrap",
                width="100%",
            ),
            rx.flex(
                rx.box(
                    rx.text("Brand", size="1", color=MUTED, weight="bold"),
                    native_filter_select(
                        DashboardState.brand_options,
                        DashboardState.brand_filter,
                        DashboardState.change_brand_filter,
                        "230px",
                    ),
                ),
                rx.box(
                    rx.text("Strain", size="1", color=MUTED, weight="bold"),
                    native_filter_select(
                        DashboardState.strain_options,
                        DashboardState.strain_filter,
                        DashboardState.change_strain_filter,
                        "250px",
                    ),
                ),
                rx.box(
                    rx.text("SKU Type", size="1", color=MUTED, weight="bold"),
                    native_filter_select(
                        DashboardState.sku_options,
                        DashboardState.sku_filter,
                        DashboardState.change_sku_filter,
                        "260px",
                    ),
                ),
                rx.box(
                    rx.text("Search Current View", size="1", color=MUTED, weight="bold"),
                    rx.input(
                        placeholder="Customer, plan, manifest or status",
                        value=DashboardState.search_text,
                        on_change=DashboardState.change_search_text,
                        debounce_timeout=450,
                        width="310px",
                    ),
                ),
                align="end",
                gap="4",
                wrap="wrap",
                width="100%",
            ),
            width="100%",
            spacing="3",
        ),
        width="100%",
        border_top=f"5px solid {ACCENT}",
        box_shadow="0 8px 24px rgba(15, 23, 42, 0.08)",
    )


def executive_overview_panel() -> rx.Component:
    """Concise operating view for leadership and department meetings."""
    return rx.vstack(
        rx.box(
            rx.heading("Executive Dashboard", size="7", color=DARK),
            rx.text(
                "A focused view of demand, current inventory, ownership, and the work requiring attention.",
                color=MUTED,
            ),
            width="100%",
        ),
        rx.card(
            rx.flex(
                rx.box(
                    rx.text("Executive Inventory Scope", weight="bold", color=DARK),
                    rx.text(
                        "QCC-owned active inventory is the default. Facility and ownership affect inventory position and the action queue. Global Brand, Strain, and SKU filters remain active.",
                        size="1", color=MUTED,
                    ),
                    min_width="320px",
                ),
                rx.spacer(),
                rx.box(
                    rx.text("Current Facility", size="1", color=MUTED, weight="bold"),
                    rx.select(
                        DashboardState.executive_facility_options,
                        value=DashboardState.executive_facility_filter,
                        on_change=DashboardState.change_executive_facility_filter,
                        width="260px",
                    ),
                ),
                rx.box(
                    rx.text("Ownership Status", size="1", color=MUTED, weight="bold"),
                    rx.select(
                        DashboardState.executive_ownership_options,
                        value=DashboardState.executive_ownership_filter,
                        on_change=DashboardState.change_executive_ownership_filter,
                        width="340px",
                    ),
                ),
                rx.button(
                    "Reset Executive Scope",
                    on_click=DashboardState.reset_executive_filters,
                    variant="outline",
                ),
                align="end", gap="4", wrap="wrap", width="100%",
            ),
            width="100%",
            border_top=f"5px solid {ACCENT}",
            background="#f8fafc",
        ),
        executive_section(
            "Business Pulse",
            "The latest 30 shipment days. Global product filters apply; facility and ownership do not alter historical demand.",
        ),
        rx.grid(
            executive_metric_card("Last 30-Day Units", DashboardState.executive_last_30_units, "Accepted retail demand", "#0f766e", "#f0fdfa"),
            executive_metric_card("Last 30-Day Value", DashboardState.executive_last_30_value, "Shipper value", "#2563eb", "#eff6ff"),
            executive_metric_card("Customers", DashboardState.executive_last_30_customers, "Unique customer licenses", "#7c3aed", "#f5f3ff"),
            executive_metric_card("Manifests", DashboardState.executive_last_30_manifests, "Accepted demand manifests", "#0369a1", "#f0f9ff"),
            executive_metric_card("Open Manifests", DashboardState.open_manifests_metric, "Companywide current transfer status", "#d97706", "#fffbeb"),
            columns=rx.breakpoints(initial="1", sm="2", lg="5"),
            gap="4", width="100%",
        ),
        executive_section(
            "Current Inventory Position",
            "The latest published Streamlit 81.4 snapshot, using the selected executive scope.",
        ),
        rx.grid(
            executive_metric_card("Active CPG Packages", DashboardState.executive_cpg_packages, "Positive-quantity Packaged Goods; retention excluded", "#0f766e", "#f0fdfa"),
            executive_metric_card("Active CPG Units", DashboardState.executive_cpg_units, "Current sellable packaged quantity", "#0891b2", "#ecfeff"),
            executive_metric_card("Sellable Bulk", DashboardState.executive_sellable_bulk_weight, "Passed bulk available for sale", "#16a34a", "#f0fdf4"),
            executive_metric_card("WIP-Cultivation", DashboardState.executive_wip_cultivation_weight, "Potential cultivation input", "#65a30d", "#f7fee7"),
            executive_metric_card("WIP-Manufacturing", DashboardState.executive_wip_manufacturing_weight, "Potential manufacturing input", "#4f46e5", "#eef2ff"),
            executive_metric_card("Pre-WIP", DashboardState.executive_pre_wip_summary, "Packages and testing/pending weight", "#9333ea", "#faf5ff"),
            executive_metric_card("Retention / Stability", DashboardState.executive_retention_summary, "Tracked separately from active CPG", "#64748b", "#f8fafc"),
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            gap="4", width="100%",
        ),
        executive_section(
            "Ownership and Facility",
            "Separates QCC-owned inventory from partner material managed for compliance.",
        ),
        rx.grid(
            executive_metric_card("QCC-Owned Packages", DashboardState.executive_qcc_owned_packages, "All QCC ownership classifications", "#0f766e", "#f0fdfa"),
            executive_metric_card("Partner-Owned / Compliance Managed", DashboardState.executive_partner_managed_packages, "Building 1A material not owned by QCC", "#d97706", "#fffbeb"),
            executive_metric_card("Purchased 1A in Building 33", DashboardState.executive_purchased_1a_packages, "QCC-owned material purchased from Building 1A", "#2563eb", "#eff6ff"),
            columns=rx.breakpoints(initial="1", md="3"),
            gap="4", width="100%",
        ),
        executive_section(
            "Immediate Attention",
            "Counts are designed for daily operating review and direct follow-up.",
        ),
        rx.grid(
            executive_metric_card("Stockouts", DashboardState.executive_stockout_count, "Demand exists with no current units", "#dc2626", "#fef2f2"),
            executive_metric_card("Low Supply", DashboardState.executive_low_supply_count, "More than zero and no more than 4 weeks", "#ea580c", "#fff7ed"),
            executive_metric_card("Aging CPG", DashboardState.executive_aging_cpg_count, "CPG packages at least 75 days old", "#ca8a04", "#fefce8"),
            executive_metric_card("Aging Bulk", DashboardState.executive_aging_bulk_count, "Bulk packages at least 75 days old", "#a16207", "#fffbeb"),
            executive_metric_card("Needs Review", DashboardState.executive_needs_review, "Classification or ownership follow-up", "#be123c", "#fff1f2"),
            executive_metric_card("Shipment Exceptions", DashboardState.exception_manifests_metric, "Rejected or returned manifests", "#9f1239", "#fff1f2"),
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            gap="4", width="100%",
        ),
        rx.flex(
            rx.box(
                rx.heading("Stockouts and Low Inventory", size="5", color=DARK),
                rx.text(
                    "Only current stockouts and SKU combinations at 4 weeks of supply or less are shown.",
                    size="2", color=MUTED,
                ),
            ),
            rx.spacer(),
            rx.button(
                "Download Action Queue CSV",
                on_click=DashboardState.download_executive_actions,
                variant="outline",
            ),
            align="center", gap="3", wrap="wrap", width="100%",
        ),
        limited_data_grid(
            DashboardState.executive_action_rows,
            [
                "Brand", "Strain", "SKU Type", "Current Units",
                "Avg Weekly Units", "Weeks of Supply", "Demand Status",
            ],
            DashboardState.executive_action_rows_per_page,
            DashboardState.change_executive_action_rows_per_page,
            DashboardState.executive_action_page_size,
            height="520px",
        ),
        width="100%", spacing="5",
    )


def executive_reports_panel() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.heading("Reports & Exports", size="7", color=DARK),
            rx.text(
                "Repeatable finance and operating reports generated from the current Control Tower snapshot.",
                color=MUTED,
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.hstack(
                            rx.icon("file-spreadsheet", color=ACCENT),
                            rx.heading("Cultivation WIP Roll-Forward", size="5", color=DARK),
                            gap="2",
                            align="center",
                        ),
                        rx.text(
                            "A 12-month Excel report with separate Current Velocity, 30-Day Adjusted, 60-Day Adjusted, and AI Adjusted sheets.",
                            color=MUTED,
                            size="2",
                        ),
                    ),
                    rx.spacer(),
                    rx.button(
                        "Download WIP Report",
                        on_click=DashboardState.download_cultivation_wip_report,
                        loading=DashboardState.executive_report_building,
                        background=ACCENT,
                        color="white",
                    ),
                    width="100%",
                    align="center",
                    gap="4",
                    wrap="wrap",
                ),
                rx.grid(
                    executive_metric_card(
                        "Demand Scope", "Flower + Pre-Rolls",
                        "Used on all four sheets", "#0f766e", "#f0fdfa",
                    ),
                    executive_metric_card(
                        "Opening WIP", "WIP + Pre-WIP",
                        "Cultivation classifications only", "#2563eb", "#eff6ff",
                    ),
                    executive_metric_card(
                        "Minimum Floor", "5.0 lb",
                        "Retained per strain", "#7c3aed", "#f5f3ff",
                    ),
                    executive_metric_card(
                        "Excess Threshold", "50.0 lb",
                        "Reported above this level", "#d97706", "#fffbeb",
                    ),
                    columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                    gap="3",
                    width="100%",
                ),
                rx.callout(
                    "Scheduled pounds enter on the expected availability date—30 days after harvest—and already reflect Fresh Frozen and Creative Use reductions. Curing/trim loss remains zero to avoid deducting yield twice.",
                    icon="info",
                    color_scheme="blue",
                    width="100%",
                ),
                rx.cond(
                    DashboardState.executive_report_message != "",
                    rx.callout(
                        DashboardState.executive_report_message,
                        icon="circle_check",
                        color_scheme="green",
                        width="100%",
                    ),
                ),
                rx.cond(
                    DashboardState.executive_report_error != "",
                    rx.callout(
                        DashboardState.executive_report_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                ),
                width="100%",
                spacing="4",
            ),
            width="100%",
            border_top=f"5px solid {ACCENT}",
        ),
        width="100%",
        spacing="5",
    )


def executive_dashboard_panel() -> rx.Component:
    return rx.vstack(
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Overview", value="overview"),
                rx.tabs.trigger("Reports & Exports", value="reports"),
            ),
            value=DashboardState.executive_view,
            on_change=DashboardState.change_executive_view,
            width="100%",
        ),
        rx.cond(
            DashboardState.executive_view == "reports",
            executive_reports_panel(),
            executive_overview_panel(),
        ),
        width="100%",
        spacing="4",
    )


def overview_panel() -> rx.Component:
    return rx.vstack(
        rx.grid(
            metric_card("Units Shipped", DashboardState.units_metric, "Accepted retail demand"),
            metric_card("Shipment Value", DashboardState.value_metric, "Shipper value"),
            metric_card("Retail Customers", DashboardState.customers_metric, "Unique licenses"),
            metric_card("Manifests", DashboardState.manifests_metric, "Unique accepted manifests"),
            metric_card("Weighted Price / Unit", DashboardState.weighted_price_metric, "Shipment value per unit"),
            columns=rx.breakpoints(initial="1", sm="2", lg="5"),
            gap="4",
            width="100%",
        ),
        rx.grid(
            rx.card(
                rx.heading("Monthly Units", size="4"),
                rx.recharts.line_chart(
                    rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
                    rx.recharts.x_axis(data_key="Month"),
                    rx.recharts.y_axis(),
                    rx.recharts.graphing_tooltip(),
                    rx.recharts.line(data_key="Units", stroke=ACCENT, stroke_width=3),
                    data=DashboardState.monthly,
                    width="100%",
                    height=300,
                ),
            ),
            rx.card(
                rx.heading("Monthly Shipment Value", size="4"),
                rx.recharts.line_chart(
                    rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
                    rx.recharts.x_axis(data_key="Month"),
                    rx.recharts.y_axis(),
                    rx.recharts.graphing_tooltip(),
                    rx.recharts.line(data_key="Value", stroke="#7c3aed", stroke_width=3),
                    data=DashboardState.monthly,
                    width="100%",
                    height=300,
                ),
            ),
            columns=rx.breakpoints(initial="1", lg="2"),
            gap="4",
            width="100%",
        ),
        rx.heading("Top Historical SKUs", size="4"),
        limited_data_grid(
            DashboardState.top_sku_rows,
            ["Brand", "Strain", "SKU Type", "Units", "Value", "Customers"],
            DashboardState.top_sku_rows_per_page,
            DashboardState.change_top_sku_rows_per_page,
            DashboardState.top_sku_page_size,
            height="420px",
        ),
        spacing="5",
        width="100%",
    )


def stockouts_panel() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.heading("Current SKU Stockouts", size="4"),
            rx.badge(
                DashboardState.filtered_stockout_count,
                color_scheme="red",
                size="3",
            ),
            rx.spacer(),
            rx.button(
                "Download Stockouts CSV",
                on_click=DashboardState.download_stockouts,
                variant="outline",
            ),
            width="100%",
            spacing="3",
        ),
        rx.card(
            rx.flex(
                rx.box(
                    rx.text("Product Lifecycle", size="1", weight="bold", color=MUTED),
                    rx.select(
                        [
                            "Active Products Only", "Active + Dormant",
                            "Include Retirement Candidates", "White Label Products",
                            "Include All Products",
                        ],
                        value=DashboardState.demand_lifecycle_filter,
                        on_change=DashboardState.change_demand_lifecycle_filter,
                        width="280px",
                    ),
                ),
                rx.text(
                    "Active is the default. Lifecycle uses last customer shipment, current inventory, and committed production; Seasonal and Retired remain manual decisions.",
                    color=MUTED, max_width="760px",
                ),
                align="end", gap="4", wrap="wrap", width="100%",
            ),
            width="100%", border_left=f"5px solid {ACCENT}",
        ),
        limited_data_grid(
            DashboardState.stockout_rows,
            [
                "Brand", "Strain", "SKU\nType", "Avg\nWeekly\nUnits",
                "Current\nUnits", "Weeks\nof\nSupply", "Demand\nStatus",
                "Last\nShipped", "Lifecycle\nStatus",
                "Recommended Actions",
            ],
            DashboardState.stockout_rows_per_page,
            DashboardState.change_stockout_rows_per_page,
            DashboardState.stockout_page_size,
            class_name="qcc-14px-data-grid qcc-stockout-grid",
            column_width=200,
            minimum_width=2000,
        ),
        spacing="4",
        width="100%",
    )


def sku_planning_cell(value: rx.Var, width: str = "145px") -> rx.Component:
    return rx.table.cell(
        value,
        min_width=width,
        max_width=width,
        white_space="normal",
        vertical_align="middle",
        font_size="13.5px",
        padding="0.38rem 0.45rem",
    )


def sku_planning_action_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        sku_planning_cell(row["Brand"], "140px"),
        sku_planning_cell(row["Strain"], "175px"),
        sku_planning_cell(row["SKU Type"], "205px"),
        sku_planning_cell(row["Units Shipped"].to_string(), "125px"),
        sku_planning_cell(row["Avg Weekly Units"].to_string(), "145px"),
        sku_planning_cell(
            rx.cond(
                DashboardState.sku_use_availability_adjusted,
                row["Likely OOS Weeks"].to_string(),
                "—",
            ),
            "125px",
        ),
        sku_planning_cell(
            row["Avg Weekly Units - Last 30 Days"].to_string(), "145px"
        ),
        sku_planning_cell(row["Packages"].to_string(), "105px"),
        sku_planning_cell(row["Current Units"].to_string(), "125px"),
        sku_planning_cell(row["Weeks of Supply"].to_string(), "130px"),
        rx.table.cell(
            rx.flex(
                rx.tooltip(
                    rx.button(
                        row["Potential Matching WIP"],
                        on_click=DashboardState.start_production_from_sku(
                            row["Brand"], row["Strain"], row["SKU Type"]
                        ),
                        background="#0f766e",
                        color="white",
                        font_size="13.5px",
                        flex="1",
                        cursor="pointer",
                    ),
                    content=row["Potential WIP Summary"],
                ),
                rx.button(
                    rx.icon("info", size=16),
                    on_click=DashboardState.view_row_potential_wip(
                        row["Brand"], row["Strain"], row["SKU Type"]
                    ),
                    variant="outline",
                    aria_label="Open matching WIP package summary",
                ),
                gap="2", align="center", width="100%",
            ),
            min_width="205px",
            background="#ccfbf1",
            border_left="4px solid #0f766e",
            vertical_align="middle",
        ),
        rx.table.cell(
            rx.badge(
                row["Committed WIP"],
                color_scheme="blue",
                size="2",
                font_size="13.5px",
            ),
            min_width="175px",
            background="#dbeafe",
            border_left="4px solid #2563eb",
            vertical_align="middle",
        ),
        sku_planning_cell(row["Matching Pre-WIP Weight"], "200px"),
        sku_planning_cell(row["Customers"].to_string(), "105px"),
        sku_planning_cell(row["Demand Status"], "225px"),
        sku_planning_cell(row["Last Shipped"], "125px"),
        sku_planning_cell(row["Lifecycle Status"], "175px"),
    )


SKU_PLANNING_COLUMN_WIDTHS = {
    "Brand": "140px", "Strain": "175px", "SKU Type": "205px",
    "Units Shipped": "125px", "Avg Weekly Units": "145px",
    "Likely OOS Weeks": "125px",
    "Avg Weekly Units - Last 30 Days": "145px", "Packages": "105px",
    "Current Units": "125px", "Weeks of Supply": "130px",
    "Potential Matching WIP": "205px", "Committed WIP": "175px",
    "Matching Pre-WIP Weight": "200px", "Customers": "105px",
    "Demand Status": "225px", "Last Shipped": "125px",
    "Lifecycle Status": "175px",
}


def sku_planning_action_table() -> rx.Component:
    columns = [
        "Brand", "Strain", "SKU Type", "Units Shipped",
        "Avg Weekly Units", "Likely OOS Weeks",
        "Avg Weekly Units - Last 30 Days", "Packages",
        "Current Units", "Weeks of Supply", "Potential Matching WIP",
        "Committed WIP", "Matching Pre-WIP Weight", "Customers",
        "Demand Status", "Last Shipped", "Lifecycle Status",
    ]
    return rx.box(
        rx.table.root(
            rx.table.header(
                rx.table.row(*[
                    rx.table.column_header_cell(
                        (
                            rx.text(
                                "Avg Weekly Units", rx.el.br(), "Last 30 Days",
                                line_height="1.05",
                            )
                            if column == "Avg Weekly Units - Last 30 Days"
                            else rx.cond(
                                DashboardState.sku_use_availability_adjusted,
                                rx.text(
                                    "Availability-Adjusted", rx.el.br(),
                                    "Weekly Units", line_height="1.05",
                                ),
                                "Avg Weekly Units",
                            )
                            if column == "Avg Weekly Units"
                            else column
                        ),
                        background="#111111",
                        color="#ffffff",
                        font_weight="700",
                        font_size="13.5px",
                        line_height="1.15",
                        white_space="normal",
                        word_break="normal",
                        min_width=SKU_PLANNING_COLUMN_WIDTHS[column],
                        max_width=SKU_PLANNING_COLUMN_WIDTHS[column],
                        height="76px",
                        vertical_align="middle",
                        padding="0.65rem 1.6rem 0.65rem 0.5rem",
                        position="sticky",
                        top="0",
                        z_index="5",
                    )
                    for column in columns
                ])
            ),
            rx.table.body(
                rx.foreach(
                    DashboardState.sku_planning_page_records,
                    sku_planning_action_row,
                )
            ),
            size="1",
            variant="surface",
            font_size="13.5px",
            width="2620px",
        ),
        width="100%",
        overflow_x="auto",
        max_height="620px",
        overflow_y="auto",
        border="1px solid #d8e0e8",
        border_radius="8px",
    )


def sku_planning_panel() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.heading("SKU Planning & Coverage", size="4"),
            rx.badge(
                DashboardState.filtered_velocity_count,
                color_scheme="teal",
                size="3",
            ),
            rx.spacer(),
            rx.button(
                "Download CSV",
                on_click=DashboardState.download_velocity,
                variant="outline",
            ),
            width="100%",
        ),
        rx.text(
            "Velocity begins with each SKU's first recorded shipment. Current "
            "units come from the latest published inventory snapshot.",
            color=MUTED,
        ),
        rx.card(
            rx.vstack(
                rx.heading("Package and WIP Drill-Down", size="3"),
                rx.text(
                    "Potential Matching WIP is highlighted in teal. Click its value on any SKU row to open a prefilled production plan. Committed WIP is highlighted in blue.",
                    color=MUTED,
                ),
                rx.flex(
                    rx.badge(DashboardState.brand_filter, size="2"),
                    rx.badge(DashboardState.strain_filter, size="2"),
                    rx.badge(DashboardState.sku_filter, size="2"),
                    rx.spacer(),
                    rx.button(
                        "View Current Packages",
                        on_click=DashboardState.view_current_sku_packages,
                        variant="outline",
                    ),
                    rx.button(
                        "View Potential WIP",
                        on_click=DashboardState.view_potential_wip_packages,
                        background=ACCENT,
                        color="white",
                    ),
                    align="center",
                    gap="3",
                    wrap="wrap",
                    width="100%",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
            border_left=f"5px solid {ACCENT}",
        ),
        rx.flex(
            rx.box(
                rx.text("Velocity timeframe", size="1", weight="bold", color=MUTED),
                rx.select(
                    ["1 Week", "60 Days", "90 Days", "120 Days", "All Time"],
                    value=DashboardState.sku_velocity_period,
                    on_change=DashboardState.change_sku_velocity_period,
                    width="190px",
                ),
            ),
            rx.box(
                rx.text("Demand model", size="1", weight="bold", color=MUTED),
                rx.hstack(
                    rx.switch(
                        checked=DashboardState.sku_use_availability_adjusted,
                        on_change=DashboardState.change_sku_availability_adjusted,
                        color_scheme="purple",
                        size="3",
                    ),
                    rx.badge(
                        rx.cond(
                            DashboardState.sku_use_availability_adjusted,
                            "Availability-Adjusted",
                            "Current SKU Velocity",
                        ),
                        color_scheme=rx.cond(
                            DashboardState.sku_use_availability_adjusted,
                            "purple",
                            "gray",
                        ),
                        size="2",
                    ),
                    gap="2",
                    align="center",
                ),
            ),
            rx.box(
                rx.text("Sort SKU planning", size="1", weight="bold", color=MUTED),
                rx.select(
                    [
                        "Avg Weekly Units - High to Low",
                        "Units Shipped - High to Low",
                        "Current Units - Low to High",
                        "Weeks of Supply - Low to High",
                        "Brand / Strain / SKU",
                    ],
                    value=DashboardState.sku_planning_sort,
                    on_change=DashboardState.change_sku_planning_sort,
                    width="260px",
                ),
            ),
            rx.box(
                rx.text("Product lifecycle", size="1", weight="bold", color=MUTED),
                rx.select(
                    [
                        "Active Products Only", "Active + Dormant",
                        "Include Retirement Candidates", "White Label Products",
                        "Include All Products",
                    ],
                    value=DashboardState.demand_lifecycle_filter,
                    on_change=DashboardState.change_demand_lifecycle_filter,
                    width="260px",
                ),
            ),
            align="end", gap="2", wrap="wrap", width="100%",
        ),
        rx.cond(
            DashboardState.sku_use_availability_adjusted,
            rx.callout(
                "Availability-adjusted mode removes only full internal weeks identified as Likely OOS from the selected timeframe. Recent trailing gaps remain included. Unsupported SKU types retain Current SKU Velocity.",
                icon="flask-conical",
                color_scheme="purple",
                width="100%",
            ),
        ),
        sku_planning_action_table(),
        rx.flex(
            rx.box(
                rx.text("Rows per page", size="1", weight="bold", color=MUTED),
                rx.select(
                    ["10", "25", "50", "100"],
                    value=DashboardState.sku_planning_page_size_value,
                    on_change=DashboardState.change_sku_planning_page_size,
                    width="110px",
                ),
            ),
            rx.spacer(),
            rx.button(
                "Previous",
                on_click=DashboardState.previous_sku_planning_page,
                disabled=DashboardState.sku_planning_page <= 1,
                variant="outline",
            ),
            rx.badge(DashboardState.sku_planning_page_label, size="2"),
            rx.button(
                "Next",
                on_click=DashboardState.next_sku_planning_page,
                disabled=(
                    DashboardState.sku_planning_page
                    >= DashboardState.sku_planning_total_pages
                ),
                variant="outline",
            ),
            align="end", gap="2", wrap="wrap", width="100%",
        ),
        sku_package_dialog(),
        spacing="4",
        width="100%",
    )


def demand_planning_panel() -> rx.Component:
    columns = [
        "Brand", "Strain", "SKU Type", "Avg Weekly Units",
        "Forecast Units", "Current Units", "Planned Units",
        "Expected Supply", "Net Units Required", "Projected Weeks Supply",
        "Planning Status",
    ]
    return rx.vstack(
        rx.flex(
            rx.box(
                rx.heading("Demand Planning", size="5", color=DARK),
                rx.text(
                    "A transparent run-rate plan combining historical SKU velocity, current active CPG, and active saved production plans.",
                    color=MUTED,
                ),
            ),
            rx.spacer(),
            rx.box(
                rx.text("Planning Horizon", size="1", color=MUTED, weight="bold"),
                rx.select(
                    DashboardState.demand_horizon_options,
                    value=DashboardState.demand_horizon_weeks,
                    on_change=DashboardState.change_demand_horizon,
                    width="180px",
                ),
            ),
            rx.text("weeks", color=MUTED, padding_bottom="0.55rem"),
            rx.button(
                "Download Demand Plan CSV",
                on_click=DashboardState.download_demand_plan,
                variant="outline",
            ),
            align="end", gap="3", wrap="wrap", width="100%",
        ),
        rx.callout(
            "Baseline method: Forecast Units = Avg Weekly Units × selected weeks. Expected Supply = Current Units + active planned units due within the horizon. Net Units Required never falls below zero. Seasonality, promotions, and customer commitments are not yet applied.",
            icon="calculator",
            color_scheme="blue",
            width="100%",
        ),
        rx.grid(
            executive_metric_card("Forecast Units", DashboardState.demand_forecast_total, "Run-rate demand for selected horizon", "#2563eb", "#eff6ff"),
            executive_metric_card("Current Units", DashboardState.demand_current_total, "Active CPG supply", "#0f766e", "#f0fdfa"),
            executive_metric_card("Planned Units", DashboardState.demand_planned_total, "Active saved plans due in horizon", "#7c3aed", "#f5f3ff"),
            executive_metric_card("Net Unit Gap", DashboardState.demand_gap_total, "Forecast less current and planned supply", "#dc2626", "#fef2f2"),
            executive_metric_card("SKUs Requiring Production", DashboardState.demand_gap_skus, "SKU combinations with a remaining gap", "#ea580c", "#fff7ed"),
            columns=rx.breakpoints(initial="1", sm="2", lg="5"),
            gap="4", width="100%",
        ),
        rx.text(
            "Click any column heading to sort. Global Brand, Strain, SKU Type, and Search filters apply.",
            size="1", color=MUTED,
        ),
        data_grid(DashboardState.demand_planning_rows, columns, "650px"),
        width="100%", spacing="4",
    )


def plan_card(plan: rx.Var) -> rx.Component:
    return rx.accordion.item(
        value=plan["Plan ID"],
        header=rx.flex(
            rx.checkbox(
                checked=DashboardState.production_selected_plan_ids.contains(
                    plan["Plan ID"]
                ),
                on_change=lambda checked: DashboardState.toggle_saved_plan_selection(
                    plan["Plan ID"], checked
                ),
                size="3",
            ),
            rx.box(
                rx.text(plan["Plan Name"], weight="bold", color=DARK),
                rx.text(
                    plan["Plan ID"].to_string()
                    + " - "
                    + plan["Output Summary"].to_string(),
                    size="1",
                    color=MUTED,
                ),
            ),
            rx.spacer(),
            rx.badge(
                plan["Production Line"],
                color=plan["Line Color"],
                background=plan["Line Background"],
            ),
            rx.badge(plan["Status"], color_scheme="teal"),
            rx.text(plan["Target Date"], weight="medium"),
            align="center",
            gap="3",
            width="100%",
        ),
        content=rx.vstack(
            rx.grid(
                metric_card("Target Date", plan["Target Date"], "Packaging target"),
                metric_card("Production Line", plan["Production Line"], "Assigned packaging line"),
                metric_card("Department", plan["Department"], "Assigned team"),
                metric_card("Batch Weight", plan["Batch Weight (g)"].to_string() + " g", "Committed batch"),
                metric_card("Source Lots", plan["Source Count"].to_string(), "Metrc tags"),
                columns=rx.breakpoints(initial="1", sm="2", lg="5"),
                gap="3",
                width="100%",
            ),
            rx.heading("Planned SKU Outputs", size="3"),
            data_grid(
                plan["Outputs"],
                [
                    "Brand", "Strain", "SKU Type", "Allocation %",
                    "Projected Units", "Allocated Weight (g)",
                ],
                "300px",
            ),
            rx.heading("Committed Source Metrc Tags", size="3"),
            data_grid(
                plan["Sources"],
                ["Metrc Tag", "Committed Weight (g)"],
                "260px",
            ),
            rx.flex(
                rx.button(
                    "Edit Plan",
                    on_click=DashboardState.start_edit_production_plan(plan["Plan ID"]),
                    background=ACCENT,
                    color="white",
                ),
                rx.button(
                    "Duplicate",
                    on_click=DashboardState.duplicate_production_plan(plan["Plan ID"]),
                    variant="outline",
                ),
                rx.button(
                    "Create Template",
                    on_click=DashboardState.create_template_from_plan(plan["Plan ID"]),
                    variant="outline",
                ),
                rx.button(
                    "Delete",
                    on_click=DashboardState.delete_production_plan(plan["Plan ID"]),
                    color_scheme="red",
                    variant="solid",
                ),
                gap="2", wrap="wrap", width="100%",
            ),
            rx.text(
                "Created by " + plan["Created By"].to_string(),
                size="1",
                color=MUTED,
            ),
            width="100%",
            spacing="3",
        ),
    )


def saved_plans_panel() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.heading("Saved Production Plans", size="4"),
            rx.spacer(),
            rx.button(
                "Download Production Team CSV",
                on_click=DashboardState.download_saved_plans,
                background=ACCENT,
                color="white",
            ),
            width="100%",
        ),
        rx.callout(
            "Plans created in Reflex and Streamlit share the same Supabase records. "
            "Each plan is summarized once; expand it to inspect every planned SKU "
            "and committed Metrc source tag.",
            icon="info",
            color_scheme="blue",
        ),
        rx.flex(
            rx.box(
                rx.text("Find a saved plan", size="1", weight="bold", color=MUTED),
                rx.input(
                    placeholder="Plan ID, product, source tag, or department",
                    value=DashboardState.saved_plan_search,
                    on_change=DashboardState.change_saved_plan_search,
                    width="360px",
                ),
            ),
            rx.box(
                rx.text("Plan status", size="1", weight="bold", color=MUTED),
                rx.select(
                    DashboardState.saved_plan_status_options,
                    value=DashboardState.saved_plan_status_filter,
                    on_change=DashboardState.change_saved_plan_status_filter,
                    width="220px",
                ),
            ),
            rx.spacer(),
            rx.badge(
                DashboardState.production_selected_plan_ids.length().to_string()
                + " selected",
                size="2",
                color_scheme="teal",
            ),
            rx.button(
                "Select All Visible",
                on_click=DashboardState.select_all_filtered_production_plans,
                variant="outline",
            ),
            rx.button(
                "Clear Selection",
                on_click=DashboardState.clear_production_plan_selection,
                variant="outline",
            ),
            rx.button(
                "Delete Selected",
                on_click=DashboardState.delete_selected_production_plans,
                color_scheme="red",
                disabled=DashboardState.production_selected_plan_ids.length() == 0,
            ),
            gap="3", align="end", wrap="wrap", width="100%",
        ),
        rx.cond(
            DashboardState.production_action_message != "",
            rx.callout(
                DashboardState.production_action_message,
                icon="circle_check", color_scheme="green", width="100%",
            ),
        ),
        rx.cond(
            DashboardState.production_action_error != "",
            rx.callout(
                DashboardState.production_action_error,
                icon="triangle_alert", color_scheme="red", width="100%",
            ),
        ),
        rx.cond(
            DashboardState.filtered_saved_plan_cards.length() > 0,
            rx.accordion.root(
                rx.foreach(DashboardState.filtered_saved_plan_cards, plan_card),
                type="multiple",
                collapsible=True,
                width="100%",
                variant="soft",
            ),
            rx.callout(
                "No saved production plans match the current filters.",
                icon="calendar_x",
                width="100%",
            ),
        ),
        spacing="4",
        width="100%",
    )


def calendar_plan_badge(event: rx.Var) -> rx.Component:
    return rx.box(
        rx.text(event["Plan Name"], weight="bold", size="1"),
        rx.text(event["Production Line"], size="1", weight="bold"),
        rx.text(event["Department"], size="1"),
        rx.text(event["Output Summary"], size="1", color=MUTED),
        background=event["Line Background"],
        border_left="4px solid " + event["Line Color"],
        border_radius="6px",
        padding="0.4rem",
        width="100%",
    )


def calendar_day_cell(day: rx.Var) -> rx.Component:
    return rx.box(
        rx.text(day["Day"], weight="bold"),
        rx.vstack(
            rx.foreach(day["Plans"], calendar_plan_badge),
            spacing="1",
            width="100%",
        ),
        min_height="135px",
        padding="0.5rem",
        border="1px solid #dbe5ec",
        background=rx.cond(day["In Month"], SURFACE, "#f1f5f9"),
        overflow="hidden",
    )


def calendar_panel() -> rx.Component:
    return rx.vstack(
        rx.flex(
            rx.heading("Production Calendar", size="4"),
            rx.select(
                ["Month", "Week", "Day"],
                value=DashboardState.calendar_view_mode,
                on_change=DashboardState.change_calendar_view_mode,
                width="130px",
            ),
            rx.spacer(),
            rx.button(
                "Download Calendar (.ics)",
                on_click=DashboardState.download_production_calendar,
                variant="outline",
            ),
            rx.button(
                "Previous",
                on_click=DashboardState.previous_calendar_month,
                variant="outline",
            ),
            rx.heading(DashboardState.calendar_title, size="4", min_width="190px", text_align="center"),
            rx.button(
                "Next",
                on_click=DashboardState.next_calendar_month,
                variant="outline",
            ),
            align="center",
            gap="3",
            width="100%",
        ),
        rx.grid(
            rx.foreach(
                DashboardState.calendar_weekday_headers,
                lambda day_name: rx.text(
                    day_name, weight="bold", text_align="center", color=MUTED
                ),
            ),
            columns=DashboardState.calendar_grid_columns,
            width="100%",
        ),
        rx.cond(
            DashboardState.calendar.length() > 0,
            rx.grid(
                rx.foreach(DashboardState.calendar_days, calendar_day_cell),
                columns=DashboardState.calendar_grid_columns,
                gap="0",
                width="100%",
            ),
            rx.callout(
                "No dated production plans are available yet.",
                icon="calendar_x",
                width="100%",
            ),
        ),
        rx.heading("Agenda View", size="3"),
        data_grid(
            DashboardState.calendar_rows,
            [
                "Target Date", "Plan Name", "Production Line", "Status", "Department",
                "Output Summary",
            ],
            "320px",
        ),
        spacing="4",
        width="100%",
    )


def production_number_field(
    label: str, value: rx.Var, event: Any, step: str = "1"
) -> rx.Component:
    return rx.box(
        rx.text(label, size="1", color=MUTED, weight="bold"),
        rx.input(
            type="number", min="0", step=step, value=value,
            on_change=event, width="100%",
        ),
        width="100%",
    )


def production_source_choice(source: rx.Var) -> rx.Component:
    return rx.card(
        rx.flex(
            rx.checkbox(
                checked=DashboardState.production_selected_tags.contains(
                    source["Metrc Tag"]
                ),
                on_change=lambda checked: DashboardState.toggle_production_source(
                    source["Metrc Tag"], checked
                ),
                size="3",
            ),
            rx.box(
                rx.text(source["Metrc Tag"], weight="bold", color=DARK),
                rx.text(source["Item"], size="2"),
                rx.text(
                    source["Source Strain"].to_string()
                    + " | " + source["Material Type"].to_string(),
                    size="1", color=MUTED,
                ),
                rx.text(
                    source["WIP Component"].to_string()
                    + " · " + source["Location"].to_string(),
                    size="1", color=MUTED,
                ),
            ),
            rx.spacer(),
            rx.box(
                rx.badge(
                    source["Available Weight (g)"].to_string() + " g",
                    color_scheme="teal",
                ),
                rx.text(
                    source["Age"].to_string() + " days old",
                    size="1", color=MUTED, text_align="right",
                ),
            ),
            align="center", gap="3", width="100%",
        ),
        width="100%", padding="0.75rem",
    )


def production_source_strain_choice(strain: rx.Var) -> rx.Component:
    return rx.checkbox(
        strain,
        checked=DashboardState.production_selected_source_strains.contains(strain),
        on_change=lambda checked: DashboardState.toggle_production_source_strain(
            strain, checked
        ),
        size="2",
    )


def flower_formulation_panel() -> rx.Component:
    return rx.vstack(
        rx.heading("3. Allocation and Projected Output", size="4"),
        rx.text(
            "Percentages apply to the committed batch weight and must total exactly 100%.",
            color=MUTED,
        ),
        rx.grid(
            rx.cond(
                DashboardState.production_recipe
                == "Craft Kings / Royal Smalls Flower",
                production_number_field(
                    "28g Flower %", DashboardState.production_mix_28,
                    DashboardState.change_production_mix_28,
                ),
            ),
            rx.cond(
                DashboardState.production_recipe
                == "Craft Kings / Royal Smalls Flower",
                production_number_field(
                    "14g Flower %", DashboardState.production_mix_14,
                    DashboardState.change_production_mix_14,
                ),
            ),
            production_number_field(
                "7g Flower %", DashboardState.production_mix_7,
                DashboardState.change_production_mix_7,
            ),
            production_number_field(
                "3.5g Flower %", DashboardState.production_mix_35,
                DashboardState.change_production_mix_35,
            ),
            production_number_field(
                "1g Flower %", DashboardState.production_mix_1,
                DashboardState.change_production_mix_1,
            ),
            production_number_field(
                "Smalls / Shake %", DashboardState.production_mix_smalls,
                DashboardState.change_production_mix_smalls,
            ),
            production_number_field(
                "Process Loss %", DashboardState.production_mix_loss,
                DashboardState.change_production_mix_loss, "0.5",
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="5"),
            gap="3", width="100%",
        ),
        rx.cond(
            DashboardState.production_recipe
            == "Craft Kings / Royal Smalls Flower",
            rx.vstack(
                rx.text(
                    "Assign each packaged output to Craft Kings or Royal Smalls.",
                    weight="bold",
                ),
                rx.grid(
                    rx.select(
                        ["Craft Kings", "Royal Smalls"],
                        value=DashboardState.production_output_brand_28,
                        on_change=DashboardState.change_output_brand_28,
                        placeholder="28g Brand",
                    ),
                    rx.select(
                        ["Craft Kings", "Royal Smalls"],
                        value=DashboardState.production_output_brand_14,
                        on_change=DashboardState.change_output_brand_14,
                        placeholder="14g Brand",
                    ),
                    rx.select(
                        ["Craft Kings", "Royal Smalls"],
                        value=DashboardState.production_output_brand_7,
                        on_change=DashboardState.change_output_brand_7,
                        placeholder="7g Brand",
                    ),
                    rx.select(
                        ["Craft Kings", "Royal Smalls"],
                        value=DashboardState.production_output_brand_35,
                        on_change=DashboardState.change_output_brand_35,
                        placeholder="3.5g Brand",
                    ),
                    rx.select(
                        ["Craft Kings", "Royal Smalls"],
                        value=DashboardState.production_output_brand_1,
                        on_change=DashboardState.change_output_brand_1,
                        placeholder="1g Brand",
                    ),
                    columns=rx.breakpoints(initial="1", sm="2", lg="5"),
                    gap="3", width="100%",
                ),
                width="100%", spacing="2",
            ),
        ),
        rx.cond(
            DashboardState.production_mix_valid,
            rx.callout(
                "The batch mix totals 100%.", icon="circle_check",
                color_scheme="green", width="100%",
            ),
            rx.callout(
                "Current mix total: " + DashboardState.production_mix_total
                + ". Adjust the percentages to exactly 100%.",
                icon="triangle_alert", color_scheme="orange", width="100%",
            ),
        ),
        data_grid(
            DashboardState.production_output_rows,
            [
                "Brand", "Strain", "SKU Type", "Mix %",
                "Allocated Weight (g)", "Projected Units",
            ],
            "360px", show_search=False,
        ),
        rx.grid(
            metric_card(
                "Mix Total", DashboardState.production_mix_total,
                "Must equal 100%",
            ),
            metric_card(
                "Projected Retail Units",
                DashboardState.production_projected_units,
                "Whole sellable packages",
            ),
            columns=rx.breakpoints(initial="1", sm="2"),
            gap="3", width="100%",
        ),
        rx.heading("Compare Scenarios", size="3"),
        rx.flex(
            rx.input(
                value=DashboardState.production_scenario_name,
                on_change=DashboardState.change_production_scenario_name,
                placeholder="Scenario name", width="320px",
            ),
            rx.button(
                "Add Current Mix", on_click=DashboardState.add_production_scenario,
                disabled=DashboardState.production_mix_valid == False,
                background=ACCENT, color="white",
            ),
            rx.button(
                "Clear Comparison", on_click=DashboardState.clear_production_scenarios,
                variant="outline",
            ),
            gap="3", wrap="wrap", width="100%",
        ),
        rx.cond(
            DashboardState.production_scenarios.length() > 0,
            data_grid(
                DashboardState.production_scenarios,
                [
                    "Scenario", "Batch Weight (g)", "28g Units", "14g Units",
                    "7g Units", "3.5g Units", "1g Units", "Total Units",
                ],
                "320px", show_search=False,
            ),
        ),
        width="100%", spacing="3",
    )


def single_output_formulation_panel() -> rx.Component:
    return rx.vstack(
        rx.heading("3. Formulation and Projected Output", size="4"),
        rx.cond(
            DashboardState.production_recipe == "Craft Kings Gummies",
            rx.grid(
                production_number_field(
                    "Average gummy weight (g)",
                    DashboardState.production_gummy_piece_weight,
                    DashboardState.change_production_gummy_weight, "0.1",
                ),
                production_number_field(
                    "Gummies per package",
                    DashboardState.production_gummies_per_package,
                    DashboardState.change_production_gummy_count,
                ),
                columns=rx.breakpoints(initial="1", sm="2"),
                gap="3", width="100%",
            ),
            production_number_field(
                "Label weight per package (g)",
                DashboardState.production_unit_weight,
                DashboardState.change_production_unit_weight, "0.1",
            ),
        ),
        rx.grid(
            production_number_field(
                "Overfill %", DashboardState.production_overfill_percent,
                DashboardState.change_production_overfill, "0.25",
            ),
            production_number_field(
                "Process / setup loss %",
                DashboardState.production_process_loss_percent,
                DashboardState.change_production_process_loss, "0.5",
            ),
            production_number_field(
                "QA / retention weight (g)",
                DashboardState.production_qa_retention_grams,
                DashboardState.change_production_qa_retention, "0.5",
            ),
            columns=rx.breakpoints(initial="1", sm="3"),
            gap="3", width="100%",
        ),
        data_grid(
            DashboardState.production_output_rows,
            [
                "Brand", "Strain", "SKU Type", "Yield %",
                "Packageable Weight (g)", "Projected Packages",
            ],
            "260px", show_search=False,
        ),
        metric_card(
            "Projected Packages", DashboardState.production_projected_units,
            "Whole sellable packages",
        ),
        width="100%", spacing="3",
    )


def production_builder_panel() -> rx.Component:
    return rx.vstack(
        rx.callout(
            "Choose the finished product first, select compatible WIP, compare the projected output, and save only when the plan is ready. Saving commits weight against the exact Metrc tags in shared Supabase.",
            icon="factory", color_scheme="blue", width="100%",
        ),
        rx.cond(
            DashboardState.production_action_message != "",
            rx.callout(
                DashboardState.production_action_message,
                icon="circle_check", color_scheme="green", width="100%",
            ),
        ),
        rx.cond(
            DashboardState.production_edit_plan_id != "",
            rx.callout(
                rx.flex(
                    rx.text(
                        "Editing saved plan: "
                        + DashboardState.production_edit_plan_id,
                        weight="bold",
                    ),
                    rx.spacer(),
                    rx.button(
                        "Cancel Edit",
                        on_click=DashboardState.cancel_production_plan_edit,
                        variant="outline",
                    ),
                    align="center", width="100%",
                ),
                icon="pencil", color_scheme="orange", width="100%",
            ),
        ),
        rx.cond(
            DashboardState.production_template_options.length() > 1,
            rx.box(
                rx.text(
                    "Start from a saved template",
                    size="1", weight="bold", color=MUTED,
                ),
                rx.select(
                    DashboardState.production_template_options,
                    value=DashboardState.production_template_choice,
                    on_change=DashboardState.apply_production_template,
                    width="100%",
                ),
                width="100%",
            ),
        ),
        rx.heading("1. Product Target", size="4"),
        rx.grid(
            rx.box(
                rx.text("Brand", size="1", weight="bold", color=MUTED),
                rx.select(
                    DashboardState.production_brand_options,
                    value=DashboardState.production_brand,
                    on_change=DashboardState.change_production_brand,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("Strain / Product Flavor", size="1", weight="bold", color=MUTED),
                rx.select(
                    DashboardState.production_strain_options,
                    value=DashboardState.production_strain,
                    on_change=DashboardState.change_production_strain,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("SKU Type", size="1", weight="bold", color=MUTED),
                rx.select(
                    DashboardState.production_sku_options,
                    value=DashboardState.production_sku,
                    on_change=DashboardState.change_production_sku,
                    width="100%",
                ),
            ),
            columns=rx.breakpoints(initial="1", sm="3"),
            gap="3", width="100%",
        ),
        rx.badge(
            "Formulation: " + DashboardState.production_recipe,
            color_scheme="teal", size="3",
        ),
        rx.heading("2. Compatible Source Lots", size="4"),
        rx.text("Quick material filters", size="1", weight="bold", color=MUTED),
        rx.flex(
            rx.button(
                "All Eligible",
                on_click=DashboardState.change_production_material_filter(
                    "All Eligible Materials"
                ),
                variant="outline",
            ),
            rx.button(
                "Bulk Flower",
                on_click=DashboardState.change_production_material_filter("Bulk Flower"),
                variant="outline",
            ),
            rx.button(
                "Trim",
                on_click=DashboardState.change_production_material_filter("Trim"),
                variant="outline",
            ),
            rx.button(
                "Shake",
                on_click=DashboardState.change_production_material_filter("Shake"),
                variant="outline",
            ),
            rx.button(
                "Mids / Smalls",
                on_click=DashboardState.change_production_material_filter("Mids / Smalls"),
                variant="outline",
            ),
            rx.badge(
                "Showing: " + DashboardState.production_material_filter,
                color_scheme="teal", size="2",
            ),
            gap="2", wrap="wrap", align="center", width="100%",
        ),
        rx.grid(
            rx.cond(
                DashboardState.production_multi_strain_enabled,
                rx.box(
                    rx.text(
                        "Source strains (multiple allowed)",
                        size="1", weight="bold", color=MUTED,
                    ),
                    rx.flex(
                        rx.button(
                            "Select All",
                            on_click=DashboardState.select_all_production_source_strains,
                            variant="outline", size="1",
                        ),
                        rx.button(
                            "Clear",
                            on_click=DashboardState.clear_production_source_strains,
                            variant="outline", size="1",
                        ),
                        gap="2", padding_y="0.35rem",
                    ),
                    rx.box(
                        rx.flex(
                            rx.foreach(
                                DashboardState.production_source_strain_values,
                                production_source_strain_choice,
                            ),
                            gap="3", wrap="wrap",
                        ),
                        max_height="145px", overflow_y="auto",
                        border="1px solid #d8e0e8", border_radius="8px",
                        padding="0.65rem", width="100%",
                    ),
                    width="100%",
                ),
                rx.box(
                    rx.text("Source strain", size="1", weight="bold", color=MUTED),
                    rx.select(
                        DashboardState.production_source_strain_options,
                        value=DashboardState.production_source_strain_filter,
                        on_change=DashboardState.change_production_source_strain_filter,
                        width="100%",
                    ),
                ),
            ),
            rx.box(
                rx.text("Source location", size="1", weight="bold", color=MUTED),
                rx.select(
                    DashboardState.production_source_location_options,
                    value=DashboardState.production_source_location_filter,
                    on_change=DashboardState.change_production_source_location_filter,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("Sort source lots", size="1", weight="bold", color=MUTED),
                rx.select(
                    ["Oldest First", "Newest First", "Largest Lot First", "Smallest Lot First"],
                    value=DashboardState.production_source_sort,
                    on_change=DashboardState.change_production_source_sort,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("Minimum available weight (g)", size="1", weight="bold", color=MUTED),
                rx.input(
                    type="number", min="0",
                    value=DashboardState.production_source_min_weight,
                    on_change=DashboardState.change_production_source_min_weight,
                    width="100%",
                ),
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="4"),
            gap="3", width="100%",
        ),
        rx.input(
            value=DashboardState.production_source_search,
            on_change=DashboardState.change_production_source_search,
            placeholder="Search source strain, item, Metrc tag, category, or location",
            width="100%",
        ),
        rx.grid(
            metric_card(
                "Matching WIP Lots", DashboardState.production_source_count,
                "Approved product-specific compatibility",
            ),
            metric_card(
                "Potential Matching WIP", DashboardState.production_source_weight,
                "Currently uncommitted weight",
            ),
            metric_card(
                "Selected Weight", DashboardState.production_selected_weight_label,
                "Selected source lots",
            ),
            columns=rx.breakpoints(initial="1", sm="3"),
            gap="3", width="100%",
        ),
        rx.flex(
            rx.button(
                "Select All Filtered Lots",
                on_click=DashboardState.select_all_production_sources,
                variant="outline",
            ),
            rx.button(
                "Clear Source Selection",
                on_click=DashboardState.clear_production_sources,
                variant="outline",
            ),
            gap="3", width="100%",
        ),
        rx.cond(
            DashboardState.production_source_records.length() > 0,
            rx.box(
                rx.vstack(
                    rx.foreach(
                        DashboardState.production_source_records,
                        production_source_choice,
                    ),
                    spacing="2", width="100%",
                ),
                max_height="430px", overflow_y="auto", width="100%",
                border="1px solid #d8e0e8", border_radius="10px",
                padding="0.5rem",
            ),
            rx.callout(
                "No uncommitted WIP currently matches this exact product target.",
                icon="triangle_alert", color_scheme="orange", width="100%",
            ),
        ),
        production_number_field(
            "Batch weight to commit (grams)",
            DashboardState.production_batch_weight,
            DashboardState.change_production_batch_weight, "10",
        ),
        rx.text(
            "The batch starts at the combined selected-lot maximum and may be reduced before saving.",
            size="1", color=MUTED,
        ),
        rx.cond(
            (
                (DashboardState.production_recipe == "Flower Mix")
                | (
                    DashboardState.production_recipe
                    == "Craft Kings / Royal Smalls Flower"
                )
            ),
            flower_formulation_panel(),
            rx.cond(
                DashboardState.production_recipe == "Infused Pre-Rolls",
                rx.callout(
                    "Infused pre-rolls correctly display separate Flower and Infusion WIP pools. Saving remains disabled until the approved flower-to-concentrate formulation ratios are defined.",
                    icon="info", color_scheme="orange", width="100%",
                ),
                rx.cond(
                    DashboardState.production_recipe == "Unsupported",
                    rx.callout(
                        "This SKU has compatible WIP but no approved production formulation yet.",
                        icon="circle_help", color_scheme="orange", width="100%",
                    ),
                    single_output_formulation_panel(),
                ),
            ),
        ),
        rx.heading("4. Review and Commit", size="4"),
        rx.grid(
            rx.box(
                rx.text("Production plan name", size="1", weight="bold", color=MUTED),
                rx.input(
                    value=DashboardState.production_plan_name,
                    on_change=DashboardState.change_production_plan_name,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("Target packaging date", size="1", weight="bold", color=MUTED),
                rx.input(
                    type="date", value=DashboardState.production_target_date,
                    on_change=DashboardState.change_production_target_date,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("Initial commitment level", size="1", weight="bold", color=MUTED),
                rx.select(
                    DashboardState.production_status_options,
                    value=DashboardState.production_plan_status,
                    on_change=DashboardState.change_production_plan_status,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("Assigned department", size="1", weight="bold", color=MUTED),
                rx.input(
                    value=DashboardState.production_assigned_department,
                    on_change=DashboardState.change_production_assigned_department,
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("Production line", size="1", weight="bold", color=MUTED),
                rx.select(
                    PRODUCTION_LINE_OPTIONS,
                    value=DashboardState.production_line,
                    on_change=DashboardState.change_production_line,
                    width="100%",
                ),
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="5"),
            gap="3", width="100%",
        ),
        rx.text_area(
            value=DashboardState.production_plan_notes,
            on_change=DashboardState.change_production_plan_notes,
            placeholder="Production notes", width="100%",
        ),
        rx.cond(
            DashboardState.production_saving,
            rx.callout(
                rx.hstack(
                    rx.spinner(size="2"),
                    rx.vstack(
                        rx.text("Saving production plan...", weight="bold"),
                        rx.text(
                            "Please keep this page open. Confirmation will appear when the plan is saved.",
                            size="2",
                        ),
                        spacing="1",
                        align="start",
                    ),
                    gap="3",
                    align="center",
                ),
                color_scheme="orange",
                width="100%",
            ),
        ),
        rx.button(
            rx.cond(
                DashboardState.production_saving,
                rx.hstack(rx.spinner(size="2"), rx.text("Saving Plan..."), gap="2"),
                rx.cond(
                    DashboardState.production_edit_plan_id != "",
                    "Save Plan Changes",
                    "Save Production Plan",
                ),
            ),
            on_click=DashboardState.save_production_plan,
            disabled=(DashboardState.production_save_enabled == False)
            | DashboardState.production_saving,
            background=ACCENT, color="white", size="3",
            class_name="qcc-primary-action",
        ),
        rx.cond(
            DashboardState.production_save_message != "",
            rx.callout(
                rx.vstack(
                    rx.heading("Production Plan Saved", size="4"),
                    rx.text(DashboardState.production_save_message, weight="bold"),
                    rx.text(
                        "Saved Plans, the production calendar, committed WIP, and SKU coverage have been refreshed.",
                        size="2",
                    ),
                    rx.flex(
                        rx.button(
                            "View Saved Plans",
                            on_click=DashboardState.view_saved_production_plan,
                            background="#111111",
                            color="white",
                        ),
                        rx.button(
                            "Build Another Plan",
                            on_click=DashboardState.build_another_production_plan,
                            variant="outline",
                        ),
                        gap="2", wrap="wrap",
                    ),
                    spacing="2", width="100%",
                ),
                icon="circle_check", color_scheme="green", width="100%",
                class_name="qcc-save-success",
            ),
        ),
        rx.cond(
            DashboardState.production_save_error != "",
            rx.callout(
                DashboardState.production_save_error,
                icon="triangle_alert", color_scheme="red", width="100%",
            ),
        ),
        width="100%", spacing="4",
    )


def production_planning_panel() -> rx.Component:
    return rx.vstack(
        rx.heading("Production Planning", size="5"),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Build & Compare", value="build"),
                rx.tabs.trigger("Saved Plans", value="saved"),
                rx.tabs.trigger("Production Calendar", value="calendar"),
                class_name="qcc-tabs",
            ),
            rx.tabs.content(
                production_builder_panel(), value="build", padding_top="1rem"
            ),
            rx.tabs.content(saved_plans_panel(), value="saved", padding_top="1rem"),
            rx.tabs.content(calendar_panel(), value="calendar", padding_top="1rem"),
            value=DashboardState.production_view,
            on_change=DashboardState.change_production_view,
            width="100%",
        ),
        width="100%",
        spacing="4",
    )


def customers_panel() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.heading("Customer Shipment History", size="4"),
            rx.spacer(),
            rx.button("Download Customer History CSV", on_click=DashboardState.download_customers, variant="outline"),
            width="100%",
        ),
        limited_data_grid(
            DashboardState.customer_rows,
            [
                "Destination\nLicense", "Customer", "Units\nShipped",
                "Shipment\nValue", "Manifests", "SKUs\nPurchased",
                "First\nShipment", "Last\nShipment", "Median\nReceipt\nHours",
                "Average\nManifest\nValue",
            ],
            DashboardState.customer_rows_per_page,
            DashboardState.change_customer_rows_per_page,
            DashboardState.customer_page_size,
            height="600px",
            class_name="qcc-14px-data-grid",
        ),
        width="100%",
        spacing="4",
    )


def retail_location_card(store: rx.Var) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.heading(store["Retailer"], size="3"),
            rx.text(store["Address"], size="2", color=MUTED),
            rx.text(
                store["Date Label"] + ": " + store["Latest Metrc Date"],
                size="1", color=MUTED,
            ),
            rx.text(
                store["Coordinate Status"] + " · " + store["Match Method"],
                size="1", color=MUTED,
            ),
            rx.hstack(
                rx.link(
                    rx.button("Open Map", size="2", variant="outline"),
                    href=store["Map URL"], target="_blank",
                ),
                rx.cond(
                    store["Website"] != "",
                    rx.link(
                        rx.button("Website", size="2", variant="ghost"),
                        href=store["Website"], target="_blank",
                    ),
                ),
                gap="2", wrap="wrap",
            ),
            width="100%", spacing="2", align="start",
        ),
        width="100%",
    )


def retail_availability_panel() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.heading("Where to Find QCC Products", size="5"),
            rx.text(
                "Map accepted retail deliveries or switch to outbound transfers "
                "that shops have not yet accepted in Metrc.",
                color=MUTED,
            ),
            width="100%",
        ),
        rx.callout(
            DashboardState.retail_activity_description,
            icon="info", color_scheme="blue", width="100%",
        ),
        rx.card(
            rx.flex(
                rx.box(
                    rx.text(
                        "Show outgoing transfers awaiting acceptance",
                        weight="bold",
                    ),
                    rx.text(
                        "Off shows completed Metrc receipts. On shows transfers "
                        "still marked Shipped and not yet Accepted.",
                        size="1", color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.switch(
                    checked=DashboardState.retail_show_pending,
                    on_change=DashboardState.change_retail_show_pending,
                    size="3",
                ),
                align="center", width="100%", gap="3",
            ),
            width="100%",
        ),
        rx.flex(
            rx.box(
                rx.text(
                    DashboardState.retail_window_label,
                    size="1", color=MUTED, weight="bold",
                ),
                rx.select(
                    DashboardState.retail_timeframe_options,
                    value=DashboardState.retail_timeframe,
                    on_change=DashboardState.change_retail_timeframe,
                    width="170px",
                ),
            ),
            rx.box(
                rx.text("Brand", size="1", color=MUTED, weight="bold"),
                native_filter_select(
                    DashboardState.retail_brand_options,
                    DashboardState.retail_brand_filter,
                    DashboardState.change_retail_brand_filter,
                    "210px",
                ),
            ),
            rx.box(
                rx.text("Strain", size="1", color=MUTED, weight="bold"),
                native_filter_select(
                    DashboardState.retail_strain_options,
                    DashboardState.retail_strain_filter,
                    DashboardState.change_retail_strain_filter,
                    "220px",
                ),
            ),
            rx.box(
                rx.text("SKU Type", size="1", color=MUTED, weight="bold"),
                rx.select(
                    DashboardState.retail_sku_options,
                    value=DashboardState.retail_sku_filter,
                    on_change=DashboardState.change_retail_sku_filter,
                    width="220px",
                ),
            ),
            rx.box(
                rx.text("Retailer / Map", size="1", color=MUTED, weight="bold"),
                rx.select(
                    DashboardState.retail_customer_options,
                    value=DashboardState.retail_customer_filter,
                    on_change=DashboardState.change_retail_customer_filter,
                    width="280px",
                ),
            ),
            align="end", gap="3", wrap="wrap", width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.heading("Matching Shops Near an Address", size="4"),
                    rx.spacer(),
                    rx.button(
                        "Download Location Review CSV",
                        on_click=DashboardState.download_retail_location_review,
                        variant="outline",
                    ),
                    align="center", gap="3", wrap="wrap", width="100%",
                ),
                rx.text(
                    "Every shop matching the date-window and product filters is "
                    "shown as its own marker when saved coordinates are available. "
                    "Enter an address to sort the shops and show the approximate "
                    "distance to each. The review download identifies any records "
                    "that still need an address or coordinates.",
                    color=MUTED,
                ),
                rx.flex(
                    rx.box(
                        rx.text(
                            "Starting Address or ZIP (optional)",
                            size="1", color=MUTED, weight="bold",
                        ),
                        rx.input(
                            placeholder="Example: 123 Main St, Princeton, NJ 08540",
                            value=DashboardState.retail_start_address_input,
                            on_change=DashboardState.change_retail_start_address,
                            width="min(100%, 520px)",
                        ),
                        width="min(100%, 540px)",
                    ),
                    rx.button(
                        "Find Nearby Matching Shops",
                        on_click=DashboardState.apply_retail_start_address,
                        size="3",
                    ),
                    rx.cond(
                        DashboardState.retail_start_address_input != "",
                        rx.button(
                            "Clear Address",
                            on_click=DashboardState.clear_retail_start_address,
                            variant="outline",
                            size="3",
                        ),
                    ),
                    align="end", gap="3", wrap="wrap", width="100%",
                ),
                rx.text(DashboardState.retail_all_map_note, size="1", color=MUTED),
                rx.el.iframe(
                    src_doc=DashboardState.retail_map_src_doc,
                    title="Matching retail availability locations",
                    width="100%", height="560px",
                    border="0", border_radius="10px",
                    loading="eager",
                    sandbox=(
                        "allow-scripts allow-same-origin allow-popups "
                        "allow-popups-to-escape-sandbox"
                    ),
                ),
                rx.text(
                    "Distances are straight-line estimates. Address lookup and map "
                    "tiles are provided by OpenStreetMap services. Directions open "
                    "only for the individual shop selected.",
                    size="1", color=MUTED,
                ),
                width="100%", spacing="3",
            ),
            width="100%",
            border_top=f"4px solid {ACCENT}",
        ),
        rx.grid(
            metric_card(
                "Retailers", DashboardState.retail_retailers_metric,
                "Matching recent Metrc activity",
            ),
            metric_card(
                "Units Shipped", DashboardState.retail_units_metric,
                "Across the selected window",
            ),
            metric_card(
                "Retailer / SKU Matches", DashboardState.retail_skus_metric,
                "Filtered product combinations",
            ),
            metric_card(
                DashboardState.retail_latest_date_label,
                DashboardState.retail_latest_delivery,
                DashboardState.retail_latest_date_caption,
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="4"),
            gap="4", width="100%",
        ),
        rx.heading("Matching Shop Directory", size="4"),
        rx.cond(
            DashboardState.retail_map_location_cards.length() > 0,
            rx.grid(
                rx.foreach(
                    DashboardState.retail_map_location_cards,
                    retail_location_card,
                ),
                columns=rx.breakpoints(initial="1", md="2", xl="3"),
                gap="3", width="100%",
            ),
            rx.callout(
                "No retailer locations match the current filters.",
                icon="map_pin", width="100%",
            ),
        ),
        rx.heading(DashboardState.retail_activity_heading, size="4"),
        data_grid(
            DashboardState.retail_availability_rows,
            [
                "Retailer", "Destination\nLicense", "Brand", "Strain", "SKU\nType",
                "Units\nShipped", "Packages", "Manifests", "First Metrc\nDate",
                "Latest Metrc\nDate",
            ],
            "560px",
            class_name="qcc-retail-availability-grid",
        ),
        width="100%", spacing="4",
    )


def exceptions_panel() -> rx.Component:
    return rx.vstack(
        rx.cond(
            DashboardState.distribution_error != "",
            rx.callout(
                DashboardState.distribution_error,
                icon="triangle_alert",
                color_scheme="red",
                width="100%",
            ),
        ),
        rx.flex(
            rx.box(
                rx.text("Exception view", size="1", color=MUTED, weight="bold"),
                rx.select(
                    DashboardState.shipment_exception_view_options,
                    value=DashboardState.shipment_exception_view,
                    on_change=DashboardState.change_shipment_exception_view,
                    width="230px",
                ),
            ),
            rx.spacer(),
            rx.hstack(
                rx.switch(
                    checked=DashboardState.shipment_exception_show_manifest_summary,
                    on_change=DashboardState.change_shipment_exception_summary_view,
                ),
                rx.text("Manifest summary", weight="bold"),
            ),
            rx.cond(
                DashboardState.shipment_exception_show_manifest_summary,
                rx.button(
                    "Download Selected Manifest Summary",
                    on_click=DashboardState.download_exceptions,
                    variant="outline",
                ),
                rx.button(
                    "Download Selected Package Detail",
                    on_click=DashboardState.download_exception_packages,
                    variant="outline",
                ),
            ),
            align="end", gap="3", wrap="wrap", width="100%",
        ),
        rx.callout(
            DashboardState.shipment_exception_description,
            icon="info", color_scheme="blue", width="100%",
        ),
        rx.grid(
            metric_card("Selected Manifests", DashboardState.selected_exception_manifests_metric, DashboardState.shipment_exception_view),
            metric_card("Selected Package Rows", DashboardState.selected_exception_packages_metric, "Package-level outcomes"),
            metric_card("Selected Shipper Value", DashboardState.selected_exception_value_metric, "Value recorded in Metrc"),
            columns=rx.breakpoints(initial="1", sm="3"),
            gap="4",
            width="100%",
        ),
        rx.cond(
            DashboardState.shipment_exception_show_manifest_summary,
            rx.vstack(
                rx.heading(
                    DashboardState.shipment_exception_view + " — Manifest Summary",
                    size="4",
                ),
                limited_data_grid(
                    DashboardState.exception_rows,
                    [
                        "Manifest", "State", "Destination License", "Customer",
                        "Created", "Received", "Packages", "Items", "Shipper Value",
                    ],
                    DashboardState.exception_rows_per_page,
                    DashboardState.change_exception_rows_per_page,
                    DashboardState.exception_page_size,
                    height="560px",
                    class_name="qcc-exception-data-grid",
                    column_width=190,
                    minimum_width=1710,
                ),
                width="100%",
                spacing="3",
            ),
            rx.vstack(
                rx.heading(
                    DashboardState.shipment_exception_view + " — Package Detail",
                    size="4",
                ),
                rx.flex(
                    rx.button(
                        "Previous",
                        on_click=DashboardState.previous_exception_page,
                        disabled=DashboardState.exception_page <= 1,
                        loading=DashboardState.distribution_loading,
                        variant="outline",
                    ),
                    rx.badge(
                        DashboardState.exception_page_label,
                        color_scheme="teal",
                        size="3",
                    ),
                    rx.button(
                        "Next",
                        on_click=DashboardState.next_exception_page,
                        disabled=(
                            DashboardState.exception_page
                            >= DashboardState.exception_total_pages
                        ),
                        loading=DashboardState.distribution_loading,
                        variant="outline",
                    ),
                    gap="3", align="center", wrap="wrap", width="100%",
                ),
                limited_data_grid(
                    DashboardState.exception_package_rows,
                    [
                        "Manifest", "State", "Destination License", "Customer",
                        "Package Tag", "Metrc Item", "Brand", "Strain", "SKU Type",
                        "Shipped Units", "Shipper Value", "Created", "Received",
                    ],
                    DashboardState.exception_rows_per_page,
                    DashboardState.change_exception_rows_per_page,
                    DashboardState.exception_page_size,
                    height="620px",
                    class_name="qcc-exception-data-grid",
                    column_width=190,
                    minimum_width=2470,
                ),
                width="100%",
                spacing="3",
            ),
        ),
        width="100%",
        spacing="4",
    )


def transfer_data_panel() -> rx.Component:
    return rx.vstack(
        rx.cond(
            DashboardState.distribution_error != "",
            rx.callout(
                DashboardState.distribution_error,
                icon="triangle_alert",
                color_scheme="red",
                width="100%",
            ),
        ),
        rx.hstack(
            rx.heading("Stored Transfer Data", size="4"),
            rx.badge(DashboardState.transfer_rows_metric + " stored rows", size="3"),
            rx.spacer(),
            rx.button("Download Displayed Transfer CSV", on_click=DashboardState.download_transfers, variant="outline"),
            width="100%",
        ),
        rx.text(
            "Transfer records are filtered and paged on the server, so the "
            "browser receives only the rows currently displayed. Supabase "
            "remains the complete system of record.",
            color=MUTED,
        ),
        rx.heading("Import History", size="3"),
        limited_data_grid(
            DashboardState.import_log_rows,
            [
                "Filename", "Source Rows", "Stored Rows", "Inserted Rows",
                "Updated Rows", "Created Min", "Created Max", "Imported At",
            ],
            DashboardState.transfer_import_rows_per_page,
            DashboardState.change_transfer_import_rows_per_page,
            DashboardState.transfer_import_page_size,
            height="280px",
            class_name="qcc-transfer-data-grid",
        ),
        rx.heading("Recent Transfer Records", size="3"),
        rx.flex(
            rx.button(
                "Previous",
                on_click=DashboardState.previous_transfer_page,
                disabled=DashboardState.transfer_page <= 1,
                loading=DashboardState.distribution_loading,
                variant="outline",
            ),
            rx.badge(
                DashboardState.transfer_page_label,
                color_scheme="teal",
                size="3",
            ),
            rx.button(
                "Next",
                on_click=DashboardState.next_transfer_page,
                disabled=(
                    DashboardState.transfer_page
                    >= DashboardState.transfer_total_pages
                ),
                loading=DashboardState.distribution_loading,
                variant="outline",
            ),
            gap="3", align="center", wrap="wrap", width="100%",
        ),
        limited_data_grid(
            DashboardState.transfer_rows,
            [
                "Manifest", "Invoice Number", "Created", "Received", "State",
                "Destination License", "Customer", "Package Tag",
                "Metrc Item", "Brand", "Strain", "SKU Type",
                "Shipped Units", "Shipper Value", "Demand Record",
            ],
            DashboardState.transfer_rows_per_page,
            DashboardState.change_transfer_rows_per_page,
            DashboardState.transfer_page_size,
            height="640px",
            class_name="qcc-transfer-data-grid",
            column_width=190,
            minimum_width=2850,
        ),
        width="100%",
        spacing="4",
    )


def table_row_limit_control(
    value: rx.Var,
    on_change: Any,
) -> rx.Component:
    """Compact 10/25/50 row selector shared by operational tables."""
    return rx.flex(
        rx.text("Rows", size="1", weight="bold", color=MUTED),
        rx.select(
            ["10", "25", "50"],
            value=value,
            on_change=on_change,
            width="76px",
            size="1",
        ),
        align="center",
        gap="2",
        width="100%",
    )


def limited_data_grid(
    data: rx.Var,
    columns: list[str],
    rows_value: rx.Var,
    rows_change: Any,
    page_size: rx.Var,
    height: str = "480px",
    show_search: bool = True,
    class_name: str = "",
    column_width: int = 165,
    minimum_width: int = 900,
) -> rx.Component:
    """Grid.js table with a consistent row-limit control below it."""
    fixed_class_name = " ".join(
        value for value in [class_name, "qcc-fixed-column-grid"] if value
    )
    return rx.vstack(
        data_grid(
            data,
            columns,
            height=height,
            show_search=show_search,
            class_name=fixed_class_name,
            column_width=column_width,
            minimum_width=minimum_width,
            page_size=page_size,
            resizable=False,
        ),
        table_row_limit_control(rows_value, rows_change),
        width="100%",
        spacing="2",
    )


def package_lineage_panel() -> rx.Component:
    """Customer-service lookup across preserved package and transfer history."""
    return rx.vstack(
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.heading("Historical Package Lineage", size="5"),
                        rx.text(
                            "Trace a package from stored inventory evidence through its source packages, harvest information, manifests, customers, and outcomes.",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.badge(
                        "CUSTOMER SERVICE",
                        color_scheme="purple",
                        variant="soft",
                        size="2",
                    ),
                    align="start", gap="3", wrap="wrap", width="100%",
                ),
                rx.flex(
                    rx.input(
                        placeholder="Metrc package tag or manifest number",
                        value=DashboardState.package_lineage_draft,
                        on_change=DashboardState.change_package_lineage_draft,
                        width="min(100%, 560px)",
                        size="3",
                    ),
                    rx.button(
                        "Trace History",
                        on_click=DashboardState.find_package_lineage,
                        loading=DashboardState.package_lineage_loading,
                        size="3",
                    ),
                    rx.button(
                        "Clear",
                        on_click=DashboardState.clear_package_lineage,
                        variant="outline",
                        size="3",
                    ),
                    align="center", gap="3", wrap="wrap", width="100%",
                ),
                rx.callout(
                    DashboardState.package_lineage_message,
                    icon="route",
                    color_scheme="blue",
                    width="100%",
                ),
                rx.cond(
                    DashboardState.package_lineage_error != "",
                    rx.callout(
                        DashboardState.package_lineage_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                ),
                width="100%", spacing="4",
            ),
            width="100%",
            border_top="4px solid #7c3aed",
        ),
        rx.grid(
            metric_card(
                "Packages", DashboardState.package_lineage_packages,
                "Package tags included by this search",
            ),
            metric_card(
                "Source Packages", DashboardState.package_lineage_sources,
                "Parent package tags preserved in snapshots",
            ),
            metric_card(
                "Snapshot Observations", DashboardState.package_lineage_snapshots,
                "Historical appearances of searched packages",
            ),
            metric_card(
                "Transfer Records", DashboardState.package_lineage_transfers,
                "Outbound, rejected, or returned package records",
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="4"),
            gap="4", width="100%",
        ),
        rx.cond(
            DashboardState.package_lineage_rows.length() > 0,
            rx.vstack(
                rx.heading("Package and Source Lineage", size="4"),
                data_grid(
                    DashboardState.package_lineage_rows,
                    [
                        "Relationship", "Package\nTag", "Metrc\nItem", "Strain",
                        "Source\nHarvest", "Production\nBatch", "First\nSeen",
                        "Last\nSeen", "Snapshots",
                    ],
                    "380px",
                    show_search=False,
                    class_name="qcc-package-lineage-grid",
                    column_width=190,
                    minimum_width=1500,
                    page_size=25,
                ),
                width="100%", spacing="3",
            ),
        ),
        rx.cond(
            DashboardState.package_lineage_timeline_rows.length() > 0,
            rx.vstack(
                rx.heading("Package History Timeline", size="4"),
                rx.text(
                    "Inventory observations and transfer outcomes are combined newest-first. Repeated snapshot rows show that the package remained present on that business date.",
                    color=MUTED,
                ),
                data_grid(
                    DashboardState.package_lineage_timeline_rows,
                    [
                        "Date", "Event", "Package\nTag", "Manifest",
                        "Customer /\nLocation", "Status", "Quantity", "Unit",
                        "Metrc\nItem",
                    ],
                    "600px",
                    class_name="qcc-package-lineage-grid",
                    column_width=180,
                    minimum_width=1550,
                    page_size=25,
                ),
                width="100%", spacing="3",
            ),
        ),
        rx.callout(
            "This first version uses preserved QCC snapshots and transfer records. When the Metrc API is connected, missing package, source-harvest, adjustment, inactive, and transferred-package events can be appended to the same history.",
            icon="database",
            color_scheme="purple",
            width="100%",
        ),
        width="100%", spacing="4",
    )


def inventory_filters() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.heading("Inventory Filters", size="3"),
                rx.spacer(),
                rx.button(
                    "Reset Inventory Filters",
                    on_click=DashboardState.reset_inventory_filters,
                    variant="outline",
                ),
                width="100%",
            ),
            rx.flex(
                rx.select(
                    DashboardState.inventory_stage_options,
                    value=DashboardState.inventory_stage_filter,
                    on_change=DashboardState.change_inventory_stage_filter,
                    placeholder="Production Stage",
                    width="230px",
                ),
                rx.select(
                    DashboardState.inventory_license_options,
                    value=DashboardState.inventory_license_filter,
                    on_change=DashboardState.change_inventory_license_filter,
                    placeholder="License",
                    width="190px",
                ),
                rx.select(
                    DashboardState.inventory_qa_options,
                    value=DashboardState.inventory_qa_filter,
                    on_change=DashboardState.change_inventory_qa_filter,
                    placeholder="QA Status",
                    width="210px",
                ),
                rx.select(
                    DashboardState.inventory_category_options,
                    value=DashboardState.inventory_category_filter,
                    on_change=DashboardState.change_inventory_category_filter,
                    placeholder="Category",
                    width="250px",
                ),
                rx.select(
                    DashboardState.inventory_location_options,
                    value=DashboardState.inventory_location_filter,
                    on_change=DashboardState.change_inventory_location_filter,
                    placeholder="Location",
                    width="260px",
                ),
                rx.select(
                    DashboardState.inventory_ownership_options,
                    value=DashboardState.inventory_ownership_filter,
                    on_change=DashboardState.change_inventory_ownership_filter,
                    placeholder="Ownership Status",
                    width="340px",
                ),
                gap="3", wrap="wrap", width="100%",
            ),
            width="100%", spacing="3",
        ),
        width="100%",
        border_top=f"4px solid {ACCENT}",
    )


def package_lookup() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.heading("Package Lookup", size="3"),
            rx.flex(
                rx.input(
                    placeholder="Enter complete Metrc tag",
                    value=DashboardState.inventory_lookup_text,
                    on_change=DashboardState.change_inventory_lookup_text,
                    width="420px",
                ),
                rx.button(
                    "Find Package",
                    on_click=DashboardState.find_inventory_package,
                    background=ACCENT,
                    color="white",
                ),
                rx.button(
                    "Clear Package Lookup",
                    on_click=DashboardState.clear_inventory_lookup,
                    variant="outline",
                ),
                gap="3", align="center", wrap="wrap",
            ),
            rx.text(DashboardState.inventory_lookup_message, color=MUTED),
            rx.cond(
                DashboardState.selected_inventory_details.length() > 0,
                package_lookup_detail_table(),
            ),
            spacing="3", width="100%",
        ),
        width="100%",
    )


def inventory_view(
    rows: rx.Var,
    count: rx.Var,
    units: rx.Var,
    weight: rx.Var,
    download_event: Any,
    summarize_value: rx.Var,
    summarize_event: Any,
) -> rx.Component:
    return rx.vstack(
        rx.cond(
            DashboardState.active_inventory_shows_samples,
            rx.grid(
                metric_card("Filtered Records", count, "Visible package records"),
                metric_card("Filtered Units", units, "Each-based packaged units"),
                metric_card(
                    "Filtered Samples",
                    DashboardState.active_inventory_samples,
                    "Visible legitimate sample packages",
                ),
                metric_card(
                    DashboardState.inventory_weight_metric_label,
                    weight,
                    DashboardState.inventory_weight_caption,
                ),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                gap="4", width="100%",
            ),
            rx.grid(
                metric_card("Filtered Records", count, "Visible package records"),
                metric_card("Filtered Units", units, "Each-based packaged units"),
                metric_card(
                    DashboardState.inventory_weight_metric_label,
                    weight,
                    DashboardState.inventory_weight_caption,
                ),
                columns=rx.breakpoints(initial="1", sm="3"),
                gap="4", width="100%",
            ),
        ),
        rx.hstack(
            rx.badge("Click any column heading to sort", color_scheme="teal", size="3"),
            rx.spacer(),
            rx.flex(
                rx.text("Summarize matching SKUs", weight="bold", size="2"),
                rx.switch(
                    checked=summarize_value,
                    on_change=summarize_event,
                    size="3",
                ),
                align="center",
                gap="2",
            ),
            rx.button(
                "Download Filtered CSV",
                on_click=download_event,
                variant="outline",
            ),
            width="100%",
        ),
        rx.text(
            DashboardState.inventory_grouping_caption,
            size="1", color=MUTED,
        ),
        rx.hstack(
            rx.box(
                rx.text("Table Weight Display", weight="bold", size="2"),
                rx.text(
                    "Changes the weight column below only; the summary remains in pounds.",
                    size="1",
                    color=MUTED,
                ),
            ),
            rx.spacer(),
            rx.select(
                ["Pounds", "Grams"],
                value=DashboardState.inventory_weight_unit,
                on_change=DashboardState.change_inventory_weight_unit,
                width="140px",
            ),
            width="100%",
            align="center",
        ),
        inventory_data_grid(rows),
        rx.flex(
            rx.box(
                rx.text("Rows per page", size="1", weight="bold", color=MUTED),
                rx.select(
                    ["10", "25", "50", "100"],
                    value=DashboardState.inventory_page_size_value,
                    on_change=DashboardState.change_inventory_page_size,
                    width="110px",
                ),
            ),
            rx.text(
                "The table updates immediately. Use the table's page controls "
                "to move through additional results.",
                size="1", color=MUTED,
            ),
            gap="3", align="end", wrap="wrap", width="100%",
        ),
        width="100%", spacing="3",
    )


def active_inventory_context() -> rx.Component:
    """Render only the active inventory tab's specialized summary."""
    return rx.cond(
        DashboardState.inventory_view_name == "cpg",
        rx.card(
            rx.flex(
                rx.box(
                    rx.text("Include Retention/Stability Samples", weight="bold"),
                    rx.text(
                        "Off by default to match Streamlit active CPG totals. Excluded retention packages: "
                        + DashboardState.excluded_retention_count,
                        size="1", color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.switch(
                    checked=DashboardState.inventory_include_retention,
                    on_change=DashboardState.change_inventory_include_retention,
                    size="3",
                ),
                align="center", width="100%",
            ),
            width="100%",
        ),
        rx.cond(
            DashboardState.inventory_view_name == "wip",
            rx.grid(
                metric_card(
                    "Cultivation WIP", DashboardState.cultivation_wip_summary,
                    "Passed Building 33 flower used by Clone Allocation",
                ),
                metric_card(
                    "Cultivation Pre-WIP",
                    DashboardState.cultivation_pre_wip_summary,
                    "Pending Building 33 flower; optional in Clone Allocation",
                ),
                metric_card(
                    "Manufacturing WIP", DashboardState.manufacturing_wip_summary,
                    "Passed manufacturing input; excluded from Clone Allocation",
                ),
                metric_card(
                    "Manufacturing Pre-WIP",
                    DashboardState.manufacturing_pre_wip_summary,
                    "Pending manufacturing input; excluded from Clone Allocation",
                ),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                gap="4", width="100%",
            ),
            rx.cond(
                DashboardState.inventory_view_name == "all",
                rx.grid(
                    metric_card(
                        "Cultivation Pre-WIP",
                        DashboardState.all_inventory_cultivation_pre_wip_summary,
                        "Pending Building 33 flower",
                    ),
                    metric_card(
                        "Cultivation WIP",
                        DashboardState.all_inventory_cultivation_wip_summary,
                        "Passed Building 33 flower",
                    ),
                    metric_card(
                        "Manufacturing Pre-WIP",
                        DashboardState.all_inventory_manufacturing_pre_wip_summary,
                        "Pending manufacturing inputs",
                    ),
                    metric_card(
                        "Manufacturing WIP",
                        DashboardState.all_inventory_manufacturing_wip_summary,
                        "Passed manufacturing inputs",
                    ),
                    metric_card(
                        "CPG Units",
                        DashboardState.all_inventory_cpg_units_summary,
                        "Finished packaged units",
                    ),
                    metric_card(
                        "CPG Flower Weight",
                        DashboardState.all_inventory_cpg_flower_weight_summary,
                        "Packaged goods on the cultivation license",
                    ),
                    metric_card(
                        "CPG Manufacturing Weight",
                        DashboardState.all_inventory_cpg_manufacturing_weight_summary,
                        "Packaged goods on the manufacturing license",
                    ),
                    columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                    gap="4", width="100%",
                ),
                rx.cond(
                    DashboardState.inventory_view_name == "aging_cpg",
                    aging_distribution_card(
                        "CPG Risk by Time Remaining",
                        "Click a bar to filter packages by the product-specific expiration window.",
                        DashboardState.aging_cpg_distribution,
                        DashboardState.aging_cpg_band_filter,
                        "All Risk Bands",
                        DashboardState.change_aging_cpg_band,
                    ),
                    rx.cond(
                        DashboardState.inventory_view_name == "aging_bulk",
                        aging_distribution_card(
                            "Bulk Inventory by Age",
                            "Click a bar to filter the table by absolute package age.",
                            DashboardState.aging_bulk_distribution,
                            DashboardState.aging_bulk_band_filter,
                            "All Age Bands",
                            DashboardState.change_aging_bulk_band,
                        ),
                        rx.box(),
                    ),
                ),
            ),
        ),
    )


def aging_cpg_inventory_view() -> rx.Component:
    return rx.vstack(
        aging_distribution_card(
            "CPG Risk by Time Remaining",
            "Click a bar to filter packages by the product-specific expiration window.",
            DashboardState.aging_cpg_distribution,
            DashboardState.aging_cpg_band_filter,
            "All Risk Bands",
            DashboardState.change_aging_cpg_band,
        ),
        inventory_view(
            DashboardState.aging_cpg_rows,
            DashboardState.aging_cpg_count,
            DashboardState.aging_cpg_units,
            DashboardState.aging_cpg_weight,
            DashboardState.download_aging_cpg,
            DashboardState.summarize_aging_cpg,
            DashboardState.change_summarize_aging_cpg,
        ),
        width="100%", spacing="4",
    )


def aging_bulk_inventory_view() -> rx.Component:
    return rx.vstack(
        aging_distribution_card(
            "Bulk Inventory by Age",
            "Click a bar to filter the table by absolute package age.",
            DashboardState.aging_bulk_distribution,
            DashboardState.aging_bulk_band_filter,
            "All Age Bands",
            DashboardState.change_aging_bulk_band,
        ),
        inventory_view(
            DashboardState.aging_bulk_rows,
            DashboardState.aging_bulk_count,
            DashboardState.aging_bulk_units,
            DashboardState.aging_bulk_weight,
            DashboardState.download_aging_bulk,
            DashboardState.summarize_aging_bulk,
            DashboardState.change_summarize_aging_bulk,
        ),
        width="100%", spacing="4",
    )


def cpg_inventory_view() -> rx.Component:
    return rx.vstack(
        rx.card(
            rx.flex(
                rx.box(
                    rx.text("Include Retention/Stability Samples", weight="bold"),
                    rx.text(
                        "Off by default to match Streamlit active CPG totals. Excluded retention packages: "
                        + DashboardState.excluded_retention_count,
                        size="1", color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.switch(
                    checked=DashboardState.inventory_include_retention,
                    on_change=DashboardState.change_inventory_include_retention,
                    size="3",
                ),
                align="center", width="100%",
            ),
            width="100%",
        ),
        inventory_view(
            DashboardState.cpg_inventory_rows,
            DashboardState.cpg_inventory_count,
            DashboardState.cpg_inventory_units,
            DashboardState.cpg_inventory_weight,
            DashboardState.download_cpg_inventory,
            DashboardState.summarize_cpg_inventory,
            DashboardState.change_summarize_cpg,
        ),
        width="100%", spacing="4",
    )


def wip_inventory_view() -> rx.Component:
    return rx.vstack(
        rx.grid(
            metric_card(
                "Pre-WIP Packages", DashboardState.pre_wip_inventory_count,
                "All source-specific pending WIP stages",
            ),
            metric_card(
                "Pre-WIP Weight", DashboardState.pre_wip_inventory_weight,
                "Filtered testing or pending material",
            ),
            columns=rx.breakpoints(initial="1", sm="2"),
            gap="4", width="100%",
        ),
        inventory_view(
            DashboardState.wip_inventory_rows,
            DashboardState.wip_inventory_count,
            DashboardState.wip_inventory_units,
            DashboardState.wip_inventory_weight,
            DashboardState.download_wip_inventory,
            DashboardState.summarize_wip_inventory,
            DashboardState.change_summarize_wip,
        ),
        width="100%", spacing="4",
    )


def inventory_panel() -> rx.Component:
    """Package-level inventory published to shared Supabase."""
    return rx.vstack(
        rx.heading("Cannabis Inventory", size="5"),
        rx.cond(
            DashboardState.authoritative_cpg_ready,
            rx.callout(
                "Authoritative CPG eligibility is active from the latest Streamlit 81.4 snapshot. Eligible QCC-owned CPG packages: "
                + DashboardState.snapshot_cpg_eligible,
                icon="database", color_scheme="green", width="100%",
            ),
            rx.cond(
                DashboardState.inventory_ready,
                rx.callout(
                    "This snapshot predates Version 81.4. Publish a new snapshot in Streamlit 81.4 before comparing CPG totals.",
                    icon="triangle_alert", color_scheme="orange", width="100%",
                ),
                rx.callout(
                    "Package detail is not published yet. Open Streamlit 81.4, load both Metrc inventory files, and publish the daily snapshot.",
                    icon="triangle_alert", color_scheme="orange", width="100%",
                ),
            ),
        ),
        inventory_filters(),
        package_lookup(),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("CPG Inventory", value="cpg"),
                rx.tabs.trigger("Bulk Inventory", value="bulk"),
                rx.tabs.trigger("WIP & Pre-WIP", value="wip"),
                rx.tabs.trigger("Aging Risk CPG", value="aging_cpg"),
                rx.tabs.trigger("Aging Risk Bulk", value="aging_bulk"),
                rx.tabs.trigger("All Inventory", value="all"),
                rx.tabs.trigger("Needs Review", value="review"),
                class_name="qcc-tabs",
                width="100%",
            ),
            value=DashboardState.inventory_view_name,
            on_change=DashboardState.change_inventory_view,
            width="100%",
        ),
        active_inventory_context(),
        inventory_view(
            DashboardState.active_inventory_rows,
            DashboardState.active_inventory_count,
            DashboardState.active_inventory_units,
            DashboardState.active_inventory_weight,
            DashboardState.download_active_inventory,
            DashboardState.active_inventory_summarize,
            DashboardState.change_active_inventory_summarize,
        ),
        width="100%", spacing="4",
    )


def sales_demand_workspace() -> rx.Component:
    """Mirror the familiar Streamlit Sales & Demand Planning workspace."""
    return rx.vstack(
        rx.box(
            rx.heading("Sales & Demand Planning", size="6"),
            rx.text(
                "Historical demand, stockouts, SKU coverage, production plans, and the buyer menu.",
                color=MUTED,
            ),
            width="100%",
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Overview", value="overview"),
                rx.tabs.trigger("Stockouts", value="stockouts"),
                rx.tabs.trigger("SKU Planning & Coverage", value="planning"),
                rx.tabs.trigger("Production Planning", value="production"),
                rx.tabs.trigger("Buyer Menu Admin", value="buyer_menu"),
                class_name="qcc-tabs",
                width="100%",
            ),
            value=DashboardState.sales_demand_view,
            on_change=DashboardState.change_sales_demand_view,
            width="100%",
        ),
        rx.box(
            rx.match(
                DashboardState.sales_demand_view,
                ("overview", overview_panel()),
                ("stockouts", stockouts_panel()),
                ("planning", sku_planning_panel()),
                ("production", production_planning_panel()),
                ("buyer_menu", sales_menu_admin_panel()),
                overview_panel(),
            ),
            width="100%", padding_top="1.25rem",
        ),
        width="100%",
        spacing="4",
    )


def qa_metric_tile(item: rx.Var) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(item["Label"], size="1", color=MUTED, weight="bold"),
            rx.heading(item["Value"], size="6", color=DARK),
            spacing="1", align="start",
        ),
        border_top=f"4px solid {ACCENT}",
        min_width="180px",
    )


QA_PASS_COLUMNS = [
    "Test Type", "Strain", "Completed Batches", "Passed", "Failed",
    "Pass Success Rate",
]
QA_POTENCY_COLUMNS = [
    "Test Type", "Strain", "Tested Batches", "Avg Total THC",
    "Min Total THC", "Max Total THC", "Avg Total Terpenes",
    "Min Total Terpenes", "Max Total Terpenes",
]
QA_DETAIL_COLUMNS = [
    "Package Tag", "Test Date", "Brand", "Strain", "SKU Type", "Test Type",
    "Status", "Total THC", "Total Terpenes",
]


def qa_operation_panel(
    operation: str,
    test_type: rx.Var,
    test_type_options: rx.Var,
    test_type_handler: Any,
    metrics: rx.Var,
    pass_rows: rx.Var,
    potency_rows: rx.Var,
    consistency_strain: rx.Var,
    consistency_options: rx.Var,
    consistency_handler: Any,
    chart_rows: rx.Var,
    detail_rows: rx.Var,
    pass_rows_value: rx.Var,
    pass_rows_handler: Any,
    pass_page_size: rx.Var,
    potency_rows_value: rx.Var,
    potency_rows_handler: Any,
    potency_page_size: rx.Var,
    detail_rows_value: rx.Var,
    detail_rows_handler: Any,
    detail_page_size: rx.Var,
) -> rx.Component:
    return rx.vstack(
        rx.card(
            rx.flex(
                rx.box(
                    rx.heading("Compliance Filter", size="4", color=DARK),
                    rx.text(
                        "Separate historical results by the actual product type tested.",
                        size="1", color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.box(
                    rx.text("Compliance Test Type", size="1", weight="bold", color=MUTED),
                    rx.select(
                        test_type_options,
                        value=test_type,
                        on_change=test_type_handler,
                        width="290px",
                    ),
                ),
                align="end", gap="4", wrap="wrap", width="100%",
            ),
            width="100%", border_top=f"5px solid {ACCENT}",
        ),
        rx.cond(
            test_type == "Flower",
            rx.callout(
                "Flower includes raw/bulk and packaged flower because the passed flower test remains valid after packaging.",
                icon="info", color_scheme="blue", width="100%",
            ),
        ),
        rx.cond(
            test_type == "Other / Needs Review",
            rx.callout(
                "These packages could not be confidently assigned to a primary compliance product type.",
                icon="triangle_alert", color_scheme="orange", width="100%",
            ),
        ),
        rx.grid(
            rx.foreach(metrics, qa_metric_tile),
            columns=rx.breakpoints(initial="1", sm="2", lg="5"),
            gap="3", width="100%",
        ),
        rx.text(
            "Each package counts once. A package that passes after retesting counts as one passed batch; pending and R&D tests are excluded from the completed denominator.",
            size="1", color=MUTED,
        ),
        rx.heading("Pass Success by Strain", size="4", color=DARK),
        rx.cond(
            pass_rows.length() > 0,
            limited_data_grid(
                pass_rows,
                QA_PASS_COLUMNS,
                pass_rows_value,
                pass_rows_handler,
                pass_page_size,
                height="430px",
                class_name="qcc-14px-data-grid",
            ),
            rx.callout(
                "No completed QA batches match the active global and compliance filters.",
                icon="circle_help", width="100%",
            ),
        ),
        rx.heading("Total THC and Total Terpene Consistency", size="4", color=DARK),
        rx.cond(
            consistency_options.length() > 0,
            rx.vstack(
                rx.box(
                    rx.text("Consistency Strain", size="1", weight="bold", color=MUTED),
                    rx.select(
                        consistency_options,
                        value=consistency_strain,
                        on_change=consistency_handler,
                        width="310px",
                    ),
                ),
                rx.grid(
                    rx.card(
                        rx.text("Total THC by Test Date", weight="bold", color=DARK),
                        rx.recharts.line_chart(
                            rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
                            rx.recharts.x_axis(data_key="Test Date"),
                            rx.recharts.y_axis(),
                            rx.recharts.graphing_tooltip(),
                            rx.recharts.line(
                                data_key="Total THC", stroke="#14969b", stroke_width=3,
                                connect_nulls=True,
                            ),
                            data=chart_rows, height=300, width="100%",
                        ),
                    ),
                    rx.card(
                        rx.text("Total Terpenes by Test Date", weight="bold", color=DARK),
                        rx.recharts.line_chart(
                            rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
                            rx.recharts.x_axis(data_key="Test Date"),
                            rx.recharts.y_axis(),
                            rx.recharts.graphing_tooltip(),
                            rx.recharts.line(
                                data_key="Total Terpenes", stroke="#7c3aed", stroke_width=3,
                                connect_nulls=True,
                            ),
                            data=chart_rows, height=300, width="100%",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", lg="2"),
                    gap="4", width="100%",
                ),
                width="100%", spacing="3",
            ),
            rx.callout(
                "No Total THC or Total Terpenes results match these filters.",
                icon="circle_help", width="100%",
            ),
        ),
        rx.heading("Average and Range by Strain", size="4", color=DARK),
        rx.cond(
            potency_rows.length() > 0,
            limited_data_grid(
                potency_rows,
                QA_POTENCY_COLUMNS,
                potency_rows_value,
                potency_rows_handler,
                potency_page_size,
                height="500px",
                class_name="qcc-14px-data-grid",
                column_width=180,
                minimum_width=1620,
            ),
            rx.callout("No potency ranges are available.", icon="circle_help"),
        ),
        rx.heading("Matching Package Records", size="4", color=DARK),
        rx.cond(
            detail_rows.length() > 0,
            limited_data_grid(
                detail_rows,
                QA_DETAIL_COLUMNS,
                detail_rows_value,
                detail_rows_handler,
                detail_page_size,
                height="560px",
                class_name="qcc-14px-data-grid",
                column_width=190,
                minimum_width=1710,
            ),
            rx.callout(
                "No package records match the active Brand, Strain, SKU Type, and compliance filters.",
                icon="circle_help",
                width="100%",
            ),
        ),
        width="100%", spacing="4",
    )


def qa_import_panel() -> rx.Component:
    return rx.accordion.root(
        rx.accordion.item(
            value="qa-import",
            header=rx.flex(
                rx.box(
                    rx.text("Import Preliminary Lab Results", weight="bold", color=DARK),
                    rx.text(
                        "Upload the laboratory's passed Preliminary Results Summary workbook.",
                        size="1", color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.badge(
                    DashboardState.qa_analyte_count.to_string() + " analyte rows",
                    color_scheme="teal",
                ),
                align="center", width="100%",
            ),
            content=rx.vstack(
                rx.heading("Lab Direct Preliminary Summary", size="3", color=DARK),
                rx.callout(
                    "Use the laboratory's passed Results Summary while Metrc is delayed. "
                    "Only rows explicitly marked PASSED can print; an exact Metrc sample "
                    "tag automatically becomes the active source when imported.",
                    icon="shield-check", color_scheme="purple", width="100%",
                ),
                rx.upload(
                    rx.vstack(
                        rx.icon("file-spreadsheet", size=28, color="#7c3aed"),
                        rx.text("Drag Preliminary Results Summary Excel files here"),
                        rx.button("Choose Excel Files", variant="outline"),
                        spacing="2", align="center",
                    ),
                    id="qa_lab_summary_upload",
                    accept={
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"]
                    },
                    multiple=True,
                    max_files=20,
                    border="2px dashed #8b5cf6",
                    border_radius="12px",
                    padding="2rem",
                    width="100%",
                ),
                rx.flex(
                    rx.foreach(rx.selected_files("qa_lab_summary_upload"), rx.badge),
                    gap="2", wrap="wrap", width="100%",
                ),
                rx.flex(
                    rx.button(
                        "Import Lab Direct Summary",
                        on_click=DashboardState.import_qa_lab_summary_files(
                            rx.upload_files(upload_id="qa_lab_summary_upload")
                        ),
                        loading=DashboardState.qa_importing,
                        background="#7c3aed", color="white",
                    ),
                    rx.button(
                        "Clear Selection",
                        on_click=rx.clear_selected_files("qa_lab_summary_upload"),
                        variant="outline",
                    ),
                    gap="3",
                ),
                rx.cond(
                    DashboardState.qa_import_results.length() > 0,
                    readable_grid(
                        DashboardState.qa_import_results,
                        ["File", "Status", "Source Rows", "Stored Rows", "Inserted", "Updated", "Details"],
                        "300px",
                    ),
                ),
                rx.heading("Recent Lab Import History", size="3"),
                readable_grid(
                    DashboardState.qa_import_log,
                    ["File", "Source Rows", "Stored Rows", "Inserted", "Updated", "Test Min", "Test Max", "Imported At"],
                    "300px",
                ),
                width="100%", spacing="3",
            ),
        ),
        type="single", collapsible=True, width="100%", variant="soft",
    )


def qa_lab_direct_summary_panel() -> rx.Component:
    columns = [
        "Imported At", "File", "Sample Tag", "Parent Package", "Product",
        "Result Status", "Active Source", "Total THC %", "Total Terpenes %",
    ]
    return rx.cond(
        DashboardState.qa_lab_direct_summary.length() > 0,
        rx.box(
            rx.heading("Recent Lab Direct Uploads", size="4", color=DARK),
            rx.text(
                "This audit remains visible after refresh. Active Source changes to Metrc "
                "when matching Metrc lab results arrive.",
                size="2", color=MUTED,
            ),
            readable_grid(
                DashboardState.qa_lab_direct_summary,
                columns,
                "190px",
            ),
            width="100%",
            padding="1rem",
            border="1px solid #d8dee8",
            border_radius="12px",
        ),
    )


def qa_lookup_result_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["package_tag"], min_width="230px"),
        rx.table.cell(row["brand"], min_width="130px"),
        rx.table.cell(row["strain"], min_width="170px"),
        rx.table.cell(row["sku_type"], min_width="190px"),
        rx.table.cell(row["lab_testing_status"], min_width="130px"),
        rx.table.cell(
            rx.badge(
                row["record_origin"],
                color_scheme="purple",
                variant="soft",
            ),
            min_width="210px",
        ),
        rx.table.cell(
            rx.button(
                "Select",
                on_click=DashboardState.select_qa_package(
                    row["package_tag"], row["packaged_license"]
                ),
                background=ACCENT, color="white", size="1",
            )
        ),
    )


def qa_catalog_result_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["Template Name"], min_width="270px", font_weight="600"),
        rx.table.cell(row["Brand"], min_width="130px"),
        rx.table.cell(row["Strain"], min_width="170px"),
        rx.table.cell(row["SKU Type"], min_width="210px"),
        rx.table.cell(row["Operation"], min_width="130px"),
        rx.table.cell(rx.badge(row["Confidence"], color_scheme="teal"), min_width="120px"),
        rx.table.cell(
            rx.button(
                "Use Template",
                on_click=DashboardState.select_native_template(row["Template Name"]),
                variant="outline", size="1",
            ),
            min_width="125px",
        ),
    )


def qa_compliance_summary_item(row: rx.Var[list[str]]) -> rx.Component:
    return rx.box(
        rx.text(row[0], size="1", weight="bold", color=MUTED),
        rx.text(row[1], size="2", weight="medium", color=DARK, white_space="normal"),
        padding="0.45rem 0.55rem",
        border="1px solid #d8e0e8",
        border_radius="8px",
        background="#f8fafc",
        min_width="0",
    )


def qa_analyte_category_badge(row: rx.Var) -> rx.Component:
    return rx.badge(
        row["Category"].to_string() + ": " + row["Count"].to_string(),
        color_scheme="teal",
        variant="soft",
        size="2",
    )


def qa_adjusted_coa_review_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["Field"]),
        rx.table.cell(row["Source"]),
        rx.table.cell(row["Entered"]),
        rx.table.cell(
            rx.badge(row["Status"], color_scheme=row["Color"], variant="soft")
        ),
    )


def qa_adjusted_terpene_input(index: int, on_change: Any) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.flex(
                rx.text(
                    DashboardState.qa_adjusted_terpene_names[index],
                    weight="bold", color=DARK,
                ),
                rx.spacer(),
                rx.badge(
                    DashboardState.qa_adjusted_source_label + " "
                    + DashboardState.qa_adjusted_metrc_terpene_values[index] + "%",
                    color_scheme="gray", variant="soft",
                ),
                width="100%", align="center",
            ),
            rx.input(
                type="number", min="0", max="20", step="0.001",
                value=DashboardState.qa_adjusted_terpene_values[index],
                on_change=on_change,
                placeholder="Enter the three-decimal COA percentage",
                width="100%",
            ),
            width="100%", spacing="2",
        ),
        width="100%",
    )


def qa_adjusted_coa_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Adjusted COA Values"),
            rx.dialog.description(
                "Review the higher-precision laboratory values. Lab Direct results "
                "are prefilled, but they are not stored as an Adjusted COA until you save."
            ),
            rx.callout(
                "Enter percentages as displayed—for example, 1.567 rather than 0.01567.",
                icon="info", color_scheme="blue", width="100%",
            ),
            rx.grid(
                rx.box(
                    rx.flex(
                        rx.text("Total Terpenes %", weight="bold"), rx.spacer(),
                        rx.badge(
                            DashboardState.qa_adjusted_source_label + " "
                            + DashboardState.qa_adjusted_metrc_total_terpenes + "%",
                            color_scheme="gray", variant="soft",
                        ),
                        width="100%",
                    ),
                    rx.input(
                        type="number", min="0", max="20", step="0.001",
                        value=DashboardState.qa_adjusted_total_terpenes,
                        on_change=DashboardState.change_qa_adjusted_total_terpenes,
                        placeholder="Example: 1.567", width="100%", margin_top="0.4rem",
                    ),
                ),
                rx.box(
                    rx.flex(
                        rx.text("Total CBG %", weight="bold"), rx.spacer(),
                        rx.badge(
                            DashboardState.qa_adjusted_source_label + " "
                            + DashboardState.qa_adjusted_metrc_total_cbg + "%",
                            color_scheme="gray", variant="soft",
                        ),
                        width="100%",
                    ),
                    rx.input(
                        type="number", min="0", max="20", step="0.01",
                        value=DashboardState.qa_adjusted_total_cbg,
                        on_change=DashboardState.change_qa_adjusted_total_cbg,
                        placeholder="Enter exactly as printed", width="100%", margin_top="0.4rem",
                    ),
                ),
                columns=rx.breakpoints(initial="1", md="2"), gap="3", width="100%",
            ),
            rx.heading("Top three terpenes", size="3"),
            rx.grid(
                qa_adjusted_terpene_input(0, DashboardState.change_qa_adjusted_terpene_1),
                qa_adjusted_terpene_input(1, DashboardState.change_qa_adjusted_terpene_2),
                qa_adjusted_terpene_input(2, DashboardState.change_qa_adjusted_terpene_3),
                columns=rx.breakpoints(initial="1", md="3"), gap="3", width="100%",
            ),
            rx.card(
                rx.flex(
                    rx.box(
                        rx.text("Calculated Other", size="1", color=MUTED, weight="bold"),
                        rx.text(
                            DashboardState.qa_adjusted_other_preview,
                            size="5", weight="bold", color=DARK,
                        ),
                    ),
                    rx.spacer(),
                    rx.text(
                        "Total and each top terpene are chopped to two decimals first; the three chopped values are then subtracted from the chopped total.",
                        size="1", color=MUTED, max_width="390px",
                    ),
                    width="100%", align="center", gap="3", wrap="wrap",
                ),
                width="100%", border_top="4px solid #8b5cf6",
            ),
            rx.table.root(
                rx.table.header(
                    rx.table.row(*[
                        rx.table.column_header_cell(column)
                        for column in ["Field", "Source", "Entered COA", "Check"]
                    ])
                ),
                rx.table.body(
                    rx.foreach(
                        DashboardState.qa_adjusted_coa_review_rows,
                        qa_adjusted_coa_review_row,
                    )
                ),
                size="1", variant="surface", width="100%",
            ),
            rx.cond(
                DashboardState.qa_adjusted_coa_has_suspect_values,
                rx.callout(
                    "One or more entries differ materially from Metrc. Recheck the lab report before saving.",
                    icon="triangle-alert", color_scheme="orange", width="100%",
                ),
            ),
            rx.cond(
                DashboardState.qa_adjusted_coa_message != "",
                rx.callout(
                    DashboardState.qa_adjusted_coa_message,
                    icon="circle-check", color_scheme="teal", width="100%",
                ),
            ),
            rx.cond(
                DashboardState.qa_adjusted_coa_error != "",
                rx.callout(
                    DashboardState.qa_adjusted_coa_error,
                    icon="triangle-alert", color_scheme="red", width="100%",
                ),
            ),
            rx.flex(
                rx.button(
                    "Save Adjusted COA",
                    on_click=DashboardState.save_qa_adjusted_coa,
                    loading=DashboardState.qa_adjusted_coa_saving,
                    background=ACCENT, color="white",
                ),
                rx.dialog.close(rx.button("Close", variant="outline")),
                justify="end", gap="3", width="100%",
            ),
            max_width="980px", width="calc(100vw - 32px)",
            max_height="calc(100vh - 32px)", overflow_y="auto",
        ),
        open=DashboardState.qa_adjusted_coa_open,
        on_open_change=DashboardState.change_qa_adjusted_coa_open,
    )


def qa_zebra_label_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.flex(
                rx.box(
                    rx.heading("Zebra Cultivation Label Pilot", size="4", color=DARK),
                    rx.text(
                        "Generates validated ZPL for the ZD620/ZD621. The laboratory "
                        "sample supplies the results; the associated bulk source tag is printed.",
                        color=MUTED, size="1",
                    ),
                ),
                rx.spacer(),
                rx.badge("STAGING TEST", color_scheme="orange", size="2"),
                align="center", gap="3", width="100%", wrap="wrap",
            ),
            rx.grid(
                rx.box(
                    rx.text("Package Format", size="1", weight="bold", color=MUTED),
                    rx.select(
                        PACKAGE_FORMAT_OPTIONS,
                        value=DashboardState.qa_zebra_package_format,
                        on_change=DashboardState.change_qa_zebra_package_format,
                        width="100%",
                    ),
                ),
                rx.box(
                    rx.text("Target Printer", size="1", weight="bold", color=MUTED),
                    rx.select(
                        ZEBRA_PRINTER_OPTIONS,
                        value=DashboardState.qa_zebra_printer,
                        on_change=DashboardState.change_qa_zebra_printer,
                        width="100%",
                    ),
                ),
                rx.box(
                    rx.text("Print Quantity", size="1", weight="bold", color=MUTED),
                    rx.input(
                        type="number", min="1", max="9999", step="1",
                        value=DashboardState.qa_zebra_quantity.to_string(),
                        on_change=DashboardState.change_qa_zebra_quantity,
                        width="100%",
                    ),
                ),
                rx.box(
                    rx.text("Harvest Date", size="1", weight="bold", color=MUTED),
                    rx.input(
                        type="date",
                        value=DashboardState.qa_zebra_harvest_date,
                        on_change=DashboardState.change_qa_zebra_harvest_date,
                        width="100%",
                    ),
                ),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                gap="3", width="100%",
            ),
            rx.grid(
                rx.box(
                    rx.text("Associated Bulk METRC Tag", size="1", weight="bold", color=MUTED),
                    rx.input(
                        value=DashboardState.qa_zebra_bulk_uid,
                        on_change=DashboardState.change_qa_zebra_bulk_uid,
                        placeholder="Bulk source tag printed on the label",
                        width="100%",
                    ),
                ),
                rx.box(
                    rx.text("Lot Number", size="1", weight="bold", color=MUTED),
                    rx.input(
                        value=DashboardState.qa_zebra_lot_number,
                        on_change=DashboardState.change_qa_zebra_lot_number,
                        width="100%",
                    ),
                ),
                columns=rx.breakpoints(initial="1", md="2"),
                gap="3", width="100%",
            ),
            rx.grid(
                rx.foreach(DashboardState.qa_zebra_preview, qa_compliance_summary_item),
                columns=rx.breakpoints(initial="1", sm="2", lg="3"),
                gap="2", width="100%",
            ),
            rx.flex(
                rx.cond(
                    DashboardState.qa_adjusted_coa.length() > 0,
                    rx.badge(
                        DashboardState.qa_adjusted_coa_status,
                        color_scheme="teal", size="3",
                    ),
                    rx.badge(
                        DashboardState.qa_adjusted_coa_status,
                        color_scheme="orange", size="3",
                    ),
                ),
                rx.button(
                    "Enter / Edit Adjusted COA",
                    on_click=DashboardState.open_qa_adjusted_coa,
                    variant="outline",
                ),
                rx.text(
                    "Use the laboratory report to verify high-precision terpene and Total CBG values.",
                    size="1", color=MUTED,
                ),
                align="center", gap="3", wrap="wrap", width="100%",
            ),
            rx.cond(
                DashboardState.qa_zebra_ready,
                rx.callout(
                    DashboardState.qa_zebra_validation_message,
                    icon="circle-check", color_scheme="teal", width="100%",
                ),
                rx.callout(
                    DashboardState.qa_zebra_validation_message,
                    icon="triangle-alert", color_scheme="orange", width="100%",
                ),
            ),
            rx.cond(
                DashboardState.qa_zebra_message != "",
                rx.callout(
                    DashboardState.qa_zebra_message,
                    icon="circle-check", color_scheme="teal", width="100%",
                ),
            ),
            rx.cond(
                DashboardState.qa_zebra_error != "",
                rx.callout(
                    DashboardState.qa_zebra_error,
                    icon="triangle-alert", color_scheme="red", width="100%",
                ),
            ),
            rx.flex(
                rx.button(
                    "Download One-Test Zebra ZPL",
                    on_click=DashboardState.download_zebra_zpl,
                    disabled=~DashboardState.qa_zebra_ready,
                    background=ACCENT, color="white", size="3",
                ),
                rx.text(
                    "The downloaded test file does not print automatically. Direct USB "
                    "printing will be enabled after Zebra Browser Print is verified locally.",
                    size="1", color=MUTED, max_width="560px",
                ),
                align="center", gap="3", wrap="wrap", width="100%",
            ),
            width="100%", spacing="3",
        ),
        width="100%", border_top="5px solid #f59e0b",
    )


def qa_compliance_summary_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("General Compliance Label Summary"),
            rx.dialog.description(
                "A quick printable summary of the selected Metrc package. "
                "The COA status below shows whether laboratory results were found."
            ),
            rx.callout(
                "Selected compliance label: "
                + DashboardState.qa_selected_native_template,
                icon="tag", color_scheme="teal", width="100%",
            ),
            rx.grid(
                rx.foreach(
                    DashboardState.qa_general_compliance_summary,
                    qa_compliance_summary_item,
                ),
                columns=rx.breakpoints(initial="1", sm="2"),
                gap="2",
                width="100%",
            ),
            rx.text(
                "The downloaded HTML summary can be opened in any browser and "
                "printed with the browser's Print command.",
                size="1", color=MUTED,
            ),
            rx.flex(
                rx.button(
                    "Download / Print Summary",
                    on_click=DashboardState.download_qa_label,
                    background=ACCENT, color="white",
                ),
                rx.dialog.close(
                    rx.button("Close", variant="outline")
                ),
                gap="3", justify="end", width="100%",
            ),
            max_width="760px", width="calc(100vw - 32px)",
        ),
        open=DashboardState.qa_preview_open,
        on_open_change=DashboardState.change_qa_preview_open,
    )


def qa_label_panel() -> rx.Component:
    return rx.vstack(
        qa_compliance_summary_dialog(),
        qa_adjusted_coa_dialog(),
        rx.heading("Compliance Label Search and Printing", size="5", color=DARK),
        rx.text(
            "Search a package tag or harvest, verify its compliance result, and download the approved printable summary.",
            color=MUTED,
        ),
        rx.card(
            rx.vstack(
                rx.heading("1. Direct Package or Harvest Search", size="4", color=DARK),
                rx.flex(
                    rx.input(
                        value=DashboardState.qa_lookup_draft,
                        on_change=DashboardState.change_qa_lookup_search,
                        placeholder="Enter a Package tag or Harvest",
                        flex="1", min_width="280px", size="3",
                    ),
                    rx.button(
                        "Find and Preview",
                        on_click=DashboardState.find_qa_lookup_record,
                        loading=DashboardState.qa_lookup_loading,
                        background=ACCENT, color="white", size="3",
                    ),
                    gap="3", wrap="wrap", width="100%",
                ),
                rx.text(
                    "Use this when the Metrc tag or harvest is already known. If "
                    "no COA exists, the current Inventory record will generate a "
                    "general printable compliance summary.",
                    color=MUTED, size="1",
                ),
                rx.cond(
                    DashboardState.qa_message != "",
                    rx.callout(
                        DashboardState.qa_message, icon="circle-check",
                        color_scheme="teal", width="100%",
                    ),
                ),
                rx.cond(
                    DashboardState.qa_error != "",
                    rx.callout(
                        DashboardState.qa_error, icon="triangle-alert",
                        color_scheme="red", width="100%",
                    ),
                ),
                width="100%", spacing="2",
            ),
            width="100%", border_top=f"5px solid {ACCENT}",
        ),
        rx.card(
            rx.vstack(
                rx.heading("2. Browse Compliance Records", size="4", color=DARK),
                rx.text(
                    "Narrow the current Metrc and lab records instead of scrolling through one long dropdown.",
                    color=MUTED,
                ),
                rx.grid(
                    rx.box(
                        rx.text("Operation", size="1", weight="bold", color=MUTED),
                        rx.select(
                            DashboardState.qa_label_operation_options,
                            value=DashboardState.qa_label_operation_filter,
                            on_change=DashboardState.change_qa_label_operation_filter,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Brand", size="1", weight="bold", color=MUTED),
                        rx.select(
                            DashboardState.qa_label_brand_options,
                            value=DashboardState.qa_label_brand_filter,
                            on_change=DashboardState.change_qa_label_brand_filter,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Strain", size="1", weight="bold", color=MUTED),
                        rx.select(
                            DashboardState.qa_label_strain_options,
                            value=DashboardState.qa_label_strain_filter,
                            on_change=DashboardState.change_qa_label_strain_filter,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("SKU Type", size="1", weight="bold", color=MUTED),
                        rx.select(
                            DashboardState.qa_label_sku_options,
                            value=DashboardState.qa_label_sku_filter,
                            on_change=DashboardState.change_qa_label_sku_filter,
                            width="100%",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                    gap="3", width="100%",
                ),
                rx.cond(
                    DashboardState.qa_lookup_matches.length() > 0,
                    rx.box(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(*[
                                    rx.table.column_header_cell(column)
                                    for column in ["Package Tag", "Brand", "Strain", "SKU Type", "QA Status", "Source", "Action"]
                                ])
                            ),
                            rx.table.body(rx.foreach(DashboardState.qa_lookup_matches, qa_lookup_result_row)),
                            size="1", variant="surface", width="100%",
                        ),
                        width="100%", overflow_x="auto", max_height="420px", overflow_y="auto",
                    ),
                    rx.callout(
                        "Enter a direct search or choose at least one browse filter.",
                        icon="search", width="100%",
                    ),
                ),
                width="100%", spacing="3",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.heading("3. Approved COA Label Template Catalog", size="4", color=DARK),
                        rx.text(
                            "Thirty finite NiceLabel designs classified from the supplied filenames.",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.input(
                        value=DashboardState.qa_label_catalog_search,
                        on_change=DashboardState.change_qa_label_catalog_search,
                        placeholder="Search label templates",
                        width="290px",
                    ),
                    align="end", gap="3", wrap="wrap", width="100%",
                ),
                rx.box(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(*[
                                rx.table.column_header_cell(column)
                                for column in ["Template", "Brand", "Strain", "SKU Type", "Operation", "Confidence", "Action"]
                            ])
                        ),
                        rx.table.body(rx.foreach(DashboardState.qa_filtered_label_catalog, qa_catalog_result_row)),
                        size="1", variant="surface", width="100%",
                    ),
                    width="100%", overflow_x="auto", max_height="460px", overflow_y="auto",
                ),
                rx.cond(
                    DashboardState.qa_selected_native_template != "",
                    rx.callout(
                        "Selected native template: " + DashboardState.qa_selected_native_template,
                        icon="tag", color_scheme="teal", width="100%",
                    ),
                ),
                rx.callout(
                    "The catalog can be selected now. Direct printing into encrypted .nlbl files will be connected after the installed ZebraDesigner/NiceLabel edition is confirmed.",
                    icon="info", color_scheme="blue", width="100%",
                ),
                width="100%", spacing="3",
            ),
            width="100%",
        ),
        rx.cond(
            DashboardState.qa_selected_package.length() > 0,
            rx.vstack(
                rx.grid(
                    metric_card(
                        "Package", DashboardState.qa_selected_package["package_tag"].to_string(),
                        "Selected Metrc compliance record",
                    ),
                    metric_card(
                        "Test Type", DashboardState.qa_selected_package["qa_test_type"].to_string(),
                        "Product family tested",
                    ),
                    metric_card(
                        "QA Status", DashboardState.qa_selected_package["lab_testing_status"].to_string(),
                        "Current compliance result",
                    ),
                    columns=rx.breakpoints(initial="1", sm="3"),
                    gap="3", width="100%",
                ),
                qa_zebra_label_card(),
                rx.box(
                    rx.text("Printable Label Template", size="1", weight="bold", color=MUTED),
                    rx.select(
                        DashboardState.qa_template_options,
                        value=DashboardState.qa_selected_template,
                        on_change=DashboardState.change_qa_selected_template,
                        width="360px",
                    ),
                ),
                rx.flex(
                    rx.switch(
                        checked=DashboardState.qa_override_expiration,
                        on_change=DashboardState.change_qa_override_expiration,
                    ),
                    rx.text("Manually Override Expiration Date", weight="bold"),
                    align="center", gap="2",
                ),
                rx.cond(
                    DashboardState.qa_override_expiration,
                    rx.box(
                        rx.text("Expiration Date", size="1", weight="bold", color=MUTED),
                        rx.input(
                            type="date",
                            value=DashboardState.qa_manual_expiration,
                            on_change=DashboardState.change_qa_manual_expiration,
                            width="250px",
                        ),
                    ),
                    rx.text(
                        "Calculated expiration: "
                        + DashboardState.qa_selected_package["expiration_date"].to_string(),
                        size="1", color=MUTED,
                    ),
                ),
                rx.button(
                    "Download / Print Compliance Summary",
                    on_click=DashboardState.download_qa_label,
                    background=ACCENT, color="white", size="3",
                ),
                rx.heading("All Compliance Analytes", size="4"),
                rx.cond(
                    DashboardState.qa_analyte_message != "",
                    rx.callout(
                        DashboardState.qa_analyte_message,
                        icon="info", color_scheme="blue", width="100%",
                    ),
                ),
                rx.flex(
                    rx.box(
                        rx.text("Analyte Category", size="1", weight="bold", color=MUTED),
                        rx.select(
                            QA_ANALYTE_CATEGORIES,
                            value=DashboardState.qa_analyte_category_filter,
                            on_change=DashboardState.change_qa_analyte_category_filter,
                            width="260px",
                        ),
                    ),
                    rx.text(
                        "Showing "
                        + DashboardState.qa_filtered_analyte_count.to_string()
                        + " of "
                        + DashboardState.qa_selected_analytes.length().to_string()
                        + " analytes",
                        size="2", color=MUTED,
                    ),
                    align="end", gap="4", wrap="wrap", width="100%",
                ),
                rx.flex(
                    rx.foreach(
                        DashboardState.qa_analyte_category_counts,
                        qa_analyte_category_badge,
                    ),
                    gap="2", wrap="wrap", width="100%",
                ),
                readable_grid(
                    DashboardState.qa_selected_analyte_rows,
                    ["Category", "Test Date", "Test", "Result", "Passed"],
                    "520px",
                ),
                width="100%", spacing="4",
            ),
        ),
        width="100%", spacing="4",
    )


def quality_compliance_panel() -> rx.Component:
    return rx.vstack(
        rx.flex(
            rx.box(
                rx.heading("Quality & Compliance", size="6", color=DARK),
                rx.text(
                    "Cultivation and manufacturing lab data, COAs, label printing, testing, and compliance.",
                    color=MUTED,
                ),
            ),
            rx.spacer(),
            rx.badge(
                DashboardState.qa_record_count.to_string() + " current package records",
                color_scheme="teal", size="3",
            ),
            rx.button(
                "Reconnect & Reload Lab Data", on_click=DashboardState.refresh_qa,
                variant="outline", loading=DashboardState.qa_loading,
            ),
            align="center", gap="3", wrap="wrap", width="100%",
        ),
        rx.cond(
            DashboardState.qa_message != "",
            rx.callout(
                DashboardState.qa_message,
                icon="circle_check", color_scheme="green", width="100%",
            ),
        ),
        rx.cond(
            DashboardState.qa_error != "",
            rx.callout(
                DashboardState.qa_error,
                icon="triangle_alert", color_scheme="red", width="100%",
            ),
        ),
        qa_import_panel(),
        qa_lab_direct_summary_panel(),
        rx.text(
            "The global Brand and Strain filters above apply to lab and label views where relevant.",
            size="1", color=MUTED,
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Cultivation Lab Data", value="cultivation"),
                rx.tabs.trigger("Manufacturing Lab Data", value="manufacturing"),
                rx.tabs.trigger(
                    "Compliance Label Search & Printing", value="labels"
                ),
                class_name="qcc-tabs",
                width="100%",
            ),
            value=DashboardState.quality_view,
            on_change=DashboardState.change_quality_view,
            width="100%",
        ),
        rx.box(
            rx.match(
                DashboardState.quality_view,
                ("cultivation", qa_operation_panel(
                    "Cultivation",
                    DashboardState.qa_cultivation_test_type,
                    DashboardState.qa_cultivation_test_type_options,
                    DashboardState.change_qa_cultivation_test_type,
                    DashboardState.qa_cultivation_metrics,
                    DashboardState.qa_cultivation_pass_summary,
                    DashboardState.qa_cultivation_potency,
                    DashboardState.qa_cultivation_consistency_strain,
                    DashboardState.qa_cultivation_consistency_options,
                    DashboardState.change_qa_cultivation_consistency_strain,
                    DashboardState.qa_cultivation_chart,
                    DashboardState.qa_cultivation_detail,
                    DashboardState.qa_cultivation_pass_rows_per_page,
                    DashboardState.change_qa_cultivation_pass_rows_per_page,
                    DashboardState.qa_cultivation_pass_page_size,
                    DashboardState.qa_cultivation_potency_rows_per_page,
                    DashboardState.change_qa_cultivation_potency_rows_per_page,
                    DashboardState.qa_cultivation_potency_page_size,
                    DashboardState.qa_cultivation_detail_rows_per_page,
                    DashboardState.change_qa_cultivation_detail_rows_per_page,
                    DashboardState.qa_cultivation_detail_page_size,
                )),
                ("manufacturing", qa_operation_panel(
                    "Manufacturing",
                    DashboardState.qa_manufacturing_test_type,
                    DashboardState.qa_manufacturing_test_type_options,
                    DashboardState.change_qa_manufacturing_test_type,
                    DashboardState.qa_manufacturing_metrics,
                    DashboardState.qa_manufacturing_pass_summary,
                    DashboardState.qa_manufacturing_potency,
                    DashboardState.qa_manufacturing_consistency_strain,
                    DashboardState.qa_manufacturing_consistency_options,
                    DashboardState.change_qa_manufacturing_consistency_strain,
                    DashboardState.qa_manufacturing_chart,
                    DashboardState.qa_manufacturing_detail,
                    DashboardState.qa_manufacturing_pass_rows_per_page,
                    DashboardState.change_qa_manufacturing_pass_rows_per_page,
                    DashboardState.qa_manufacturing_pass_page_size,
                    DashboardState.qa_manufacturing_potency_rows_per_page,
                    DashboardState.change_qa_manufacturing_potency_rows_per_page,
                    DashboardState.qa_manufacturing_potency_page_size,
                    DashboardState.qa_manufacturing_detail_rows_per_page,
                    DashboardState.change_qa_manufacturing_detail_rows_per_page,
                    DashboardState.qa_manufacturing_detail_page_size,
                )),
                ("labels", qa_label_panel()),
                qa_operation_panel(
                    "Cultivation",
                    DashboardState.qa_cultivation_test_type,
                    DashboardState.qa_cultivation_test_type_options,
                    DashboardState.change_qa_cultivation_test_type,
                    DashboardState.qa_cultivation_metrics,
                    DashboardState.qa_cultivation_pass_summary,
                    DashboardState.qa_cultivation_potency,
                    DashboardState.qa_cultivation_consistency_strain,
                    DashboardState.qa_cultivation_consistency_options,
                    DashboardState.change_qa_cultivation_consistency_strain,
                    DashboardState.qa_cultivation_chart,
                    DashboardState.qa_cultivation_detail,
                    DashboardState.qa_cultivation_pass_rows_per_page,
                    DashboardState.change_qa_cultivation_pass_rows_per_page,
                    DashboardState.qa_cultivation_pass_page_size,
                    DashboardState.qa_cultivation_potency_rows_per_page,
                    DashboardState.change_qa_cultivation_potency_rows_per_page,
                    DashboardState.qa_cultivation_potency_page_size,
                    DashboardState.qa_cultivation_detail_rows_per_page,
                    DashboardState.change_qa_cultivation_detail_rows_per_page,
                    DashboardState.qa_cultivation_detail_page_size,
                ),
            ),
            width="100%", padding_top="1rem", padding_bottom="6rem",
        ),
        width="100%", spacing="4",
    )


def distribution_customer_service_panel() -> rx.Component:
    """Customer, shipment, transfer, and package-history operations."""
    return rx.vstack(
        rx.box(
            rx.heading("Distribution & Customer Service", size="6", color=DARK),
            rx.text(
                "Customers, retail availability, shipment exceptions, package lineage, and transfer operations.",
                color=MUTED,
            ),
            width="100%",
        ),
        rx.text(
            "The global Brand, Strain, SKU, and search filters above apply where relevant.",
            size="1",
            color=MUTED,
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Customers", value="customers"),
                rx.tabs.trigger("Retail Availability", value="retail"),
                rx.tabs.trigger("Shipment Exceptions", value="exceptions"),
                rx.tabs.trigger("Package Lineage", value="lineage"),
                rx.tabs.trigger("Transfer Data", value="transfers"),
                class_name="qcc-tabs",
                width="100%",
            ),
            value=DashboardState.distribution_view,
            on_change=DashboardState.change_distribution_view,
            width="100%",
        ),
        rx.box(
            rx.match(
                DashboardState.distribution_view,
                ("customers", customers_panel()),
                ("retail", retail_availability_panel()),
                ("exceptions", exceptions_panel()),
                ("lineage", package_lineage_panel()),
                ("transfers", transfer_data_panel()),
                customers_panel(),
            ),
            width="100%",
            padding_top="1rem",
            padding_bottom="6rem",
        ),
        width="100%",
        spacing="4",
    )


def erp_foundation_panel(
    title: str,
    description: str,
    first_workspace: str,
    workspace_description: str,
) -> rx.Component:
    """A stable navigation destination for the next ERP build phase."""
    return rx.vstack(
        rx.box(
            rx.heading(title, size="6", color=DARK),
            rx.text(description, color=MUTED),
            width="100%",
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger(first_workspace, value="foundation"),
                class_name="qcc-tabs",
                width="100%",
            ),
            default_value="foundation",
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.badge("ERP FOUNDATION", color_scheme="teal", size="2"),
                rx.heading(first_workspace, size="5", color=DARK),
                rx.text(workspace_description, color=MUTED, max_width="900px"),
                rx.callout(
                    "This staging step establishes the module boundary without changing current production data or workflows.",
                    icon="info",
                    color_scheme="blue",
                    width="100%",
                ),
                align="start",
                spacing="3",
                width="100%",
            ),
            width="100%",
            border_left=f"4px solid {ACCENT}",
        ),
        width="100%",
        spacing="4",
    )


def manufacturing_panel() -> rx.Component:
    return erp_foundation_panel(
        "Manufacturing",
        "BOM recipes, production orders, yields, work in process, and material consumption.",
        "BOM Registry",
        "The BOM Registry will connect finished-product recipes to a shared Item Master, including packaging and supplies owned by Materials & Procurement and cannabis inputs owned by Cannabis Inventory.",
    )


def materials_procurement_panel() -> rx.Component:
    return erp_foundation_panel(
        "Materials & Procurement",
        "Packaging, operating supplies, suppliers, purchasing, and receiving.",
        "Packaging Inventory",
        "Packaging Inventory will be the first materials workspace, followed by supplier records, purchase orders, receiving, and reorder controls.",
    )


def employee_directory_card(employee: rx.Var) -> rx.Component:
    """Readable employee identity card that avoids compact-grid rendering issues."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.box(
                    rx.heading(employee["Name"], size="4"),
                    rx.text(employee["Title"], color=MUTED, size="2"),
                ),
                rx.spacer(),
                rx.badge(employee["Role"], color_scheme="teal", size="2"),
                rx.cond(
                    employee["Active"],
                    rx.badge("Active", color_scheme="green", size="2"),
                    rx.badge("Archived", color_scheme="gray", size="2"),
                ),
                width="100%",
                align="center",
            ),
            rx.separator(width="100%"),
            rx.grid(
                rx.box(
                    rx.text("Employee ID", size="1", weight="bold", color=MUTED),
                    rx.text(employee["Employee ID"], size="2"),
                ),
                rx.box(
                    rx.text("Primary Email", size="1", weight="bold", color=MUTED),
                    rx.text(employee["Primary Email"], size="2"),
                ),
                columns=rx.breakpoints(initial="1", md="2"),
                gap="3",
                width="100%",
            ),
            rx.box(
                rx.text("Authorized Login Emails", size="1", weight="bold", color=MUTED),
                rx.text(employee["Login Emails"], size="2", word_break="break-word"),
                width="100%",
            ),
            width="100%",
            spacing="3",
        ),
        width="100%",
        border_left=f"4px solid {ACCENT}",
    )


def administration_panel() -> rx.Component:
    """Reflex-owned employee administration with durable multi-provider identities."""
    return rx.vstack(
        rx.box(
            rx.heading("Administration", size="6"),
            rx.text(
                "Create separate employee accounts and authorize Google Workspace and Microsoft 365 login emails without overwriting another person.",
                color=MUTED,
            ),
            width="100%",
        ),
        rx.cond(
            DashboardState.admin_message != "",
            rx.callout(
                DashboardState.admin_message,
                icon="circle_check",
                color_scheme="green",
                width="100%",
            ),
        ),
        rx.cond(
            DashboardState.admin_error != "",
            rx.callout(
                DashboardState.admin_error,
                icon="triangle_alert",
                color_scheme="red",
                width="100%",
            ),
        ),
        rx.card(
            rx.vstack(
                rx.heading("Create Employee Account", size="4"),
                rx.text(
                    "Each click creates a new permanent employee ID. The optional second email can authorize the same employee through the other provider.",
                    size="2",
                    color=MUTED,
                ),
                rx.grid(
                    rx.box(
                        rx.text("Full Name", size="1", weight="bold"),
                        rx.input(
                            value=DashboardState.admin_new_name,
                            on_change=DashboardState.change_admin_new_name,
                            placeholder="Employee name",
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Title", size="1", weight="bold"),
                        rx.input(
                            value=DashboardState.admin_new_title,
                            on_change=DashboardState.change_admin_new_title,
                            placeholder="Job title",
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Role", size="1", weight="bold"),
                        rx.select(
                            list(EMPLOYEE_ROLES),
                            value=DashboardState.admin_new_role,
                            on_change=DashboardState.change_admin_new_role,
                            width="100%",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", md="3"),
                    gap="3",
                    width="100%",
                ),
                rx.grid(
                    rx.box(
                        rx.text("Primary Login Email", size="1", weight="bold"),
                        rx.input(
                            value=DashboardState.admin_new_primary_email,
                            on_change=DashboardState.change_admin_new_primary_email,
                            placeholder="Google or Microsoft email",
                            type="email",
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Second Login Email (optional)", size="1", weight="bold"),
                        rx.input(
                            value=DashboardState.admin_new_alternate_email,
                            on_change=DashboardState.change_admin_new_alternate_email,
                            placeholder="Other Google or Microsoft email",
                            type="email",
                            width="100%",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", md="2"),
                    gap="3",
                    width="100%",
                ),
                rx.button(
                    "Create Separate Employee",
                    on_click=DashboardState.create_team_member,
                    background=ACCENT,
                    color="white",
                    size="3",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
            border_top=f"4px solid {ACCENT}",
        ),
        rx.card(
            rx.vstack(
                rx.hstack(
                    rx.heading("Team Directory", size="4"),
                    rx.spacer(),
                    rx.badge(
                        DashboardState.team_members.length().to_string() + " employee accounts",
                        color_scheme="teal",
                        size="2",
                    ),
                    rx.button(
                        "Refresh Directory",
                        on_click=DashboardState.load_team_access,
                        variant="outline",
                    ),
                    width="100%",
                    align="center",
                ),
                rx.cond(
                    DashboardState.team_members.length() > 0,
                    rx.vstack(
                        rx.foreach(
                            DashboardState.team_members,
                            employee_directory_card,
                        ),
                        width="100%",
                        spacing="3",
                    ),
                    rx.callout(
                        "No employee accounts were found.",
                        icon="info",
                        color_scheme="gray",
                        width="100%",
                    ),
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.heading("Manage Employee Access", size="4"),
                rx.cond(
                    DashboardState.admin_selected_employee_id != "",
                    rx.callout(
                        rx.vstack(
                            rx.text(
                                DashboardState.admin_selected_name
                                + " · "
                                + DashboardState.admin_selected_title,
                                weight="bold",
                            ),
                            rx.text(
                                "Primary email: "
                                + DashboardState.admin_selected_primary_email,
                                size="2",
                            ),
                            rx.text(
                                "Authorized login emails: "
                                + DashboardState.admin_selected_login_emails,
                                size="2",
                            ),
                            width="100%",
                            spacing="1",
                        ),
                        icon="user_round_check",
                        color_scheme="blue",
                        width="100%",
                    ),
                ),
                rx.grid(
                    rx.box(
                        rx.text("Employee ID", size="1", weight="bold"),
                        rx.select(
                            DashboardState.admin_employee_options,
                            value=DashboardState.admin_selected_employee_id,
                            on_change=DashboardState.change_admin_selected_employee,
                            placeholder="Select an employee",
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Role", size="1", weight="bold"),
                        rx.select(
                            list(EMPLOYEE_ROLES),
                            value=DashboardState.admin_selected_role,
                            on_change=DashboardState.change_admin_selected_role,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Active Access", size="1", weight="bold"),
                        rx.hstack(
                            rx.switch(
                                checked=DashboardState.admin_selected_active,
                                on_change=DashboardState.change_admin_selected_active,
                                size="3",
                            ),
                            rx.text(
                                rx.cond(
                                    DashboardState.admin_selected_active,
                                    "Active",
                                    "Archived",
                                )
                            ),
                            min_height="38px",
                            align="center",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", md="3"),
                    gap="3",
                    width="100%",
                ),
                rx.box(
                    rx.text("Add Another Login Email (optional)", size="1", weight="bold"),
                    rx.input(
                        value=DashboardState.admin_additional_email,
                        on_change=DashboardState.change_admin_additional_email,
                        placeholder="Add Google or Microsoft email without replacing existing emails",
                        type="email",
                        width="100%",
                    ),
                    width="100%",
                ),
                rx.button(
                    "Save Employee Access",
                    on_click=DashboardState.save_team_member_access,
                    background=ACCENT,
                    color="white",
                    size="3",
                ),
                rx.text(
                    "Archiving removes sign-in access but preserves orders, reservations, production plans, and audit history.",
                    size="1",
                    color=MUTED,
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        width="100%",
        spacing="4",
        on_mount=DashboardState.load_team_access,
    )


def cultivation_strain_slot(
    bench: rx.Var, slot_number: int
) -> rx.Component:
    strain_key = f"strain_{slot_number}"
    percent_key = f"percent_{slot_number}"
    return rx.grid(
        rx.box(
            rx.text(f"Strain {slot_number}", size="1", weight="bold", color=MUTED),
            rx.select(
                DashboardState.cultivation_strain_options,
                value=bench[strain_key],
                on_change=lambda value: DashboardState.change_cultivation_bench_strain(
                    bench["bench"], strain_key, value
                ),
                placeholder="Choose strain",
                width="100%",
            ),
        ),
        rx.box(
            rx.text("Bench %", size="1", weight="bold", color=MUTED),
            rx.input(
                type="number",
                min="0",
                max="100",
                step="1",
                value=bench[percent_key],
                on_change=lambda value: DashboardState.change_cultivation_bench_percent(
                    bench["bench"], percent_key, value
                ),
                width="100%",
            ),
        ),
        columns="2",
        gap="2",
        width="100%",
    )


def cultivation_bench_card(bench: rx.Var) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.flex(
                rx.box(
                    rx.heading(bench["bench"], size="3", color=DARK),
                    rx.text(
                        bench["square_feet"].to_string()
                        + " sq ft · "
                        + bench["target_plants"].to_string()
                        + " plants",
                        size="1",
                        color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.badge(
                    bench["length"].to_string()
                    + " × "
                    + bench["width"].to_string()
                    + " ft",
                    color_scheme="teal",
                ),
                width="100%",
                align="center",
            ),
            rx.cond(
                DashboardState.cultivation_layout_editing,
                rx.grid(
                    rx.box(
                        rx.text("Length (ft)", size="1", weight="bold", color=MUTED),
                        rx.input(
                            type="number",
                            min="0",
                            step="0.5",
                            value=bench["length"],
                            on_change=lambda value: DashboardState.change_cultivation_bench_dimension(
                                bench["bench"], "length", value
                            ),
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Width (ft)", size="1", weight="bold", color=MUTED),
                        rx.input(
                            type="number",
                            min="0",
                            step="0.5",
                            value=bench["width"],
                            on_change=lambda value: DashboardState.change_cultivation_bench_dimension(
                                bench["bench"], "width", value
                            ),
                            width="100%",
                        ),
                    ),
                    columns="2",
                    gap="2",
                    width="100%",
                ),
            ),
            rx.box(
                rx.text("How many strains on this bench?", size="1", weight="bold", color=MUTED),
                rx.select(
                    ["1", "2", "3"],
                    value=bench["strain_count"].to_string(),
                    on_change=lambda value: DashboardState.change_cultivation_bench_strain_count(
                        bench["bench"], value
                    ),
                    width="100%",
                ),
                width="100%",
            ),
            cultivation_strain_slot(bench, 1),
            rx.cond(bench["strain_count"] >= 2, cultivation_strain_slot(bench, 2)),
            rx.cond(bench["strain_count"] >= 3, cultivation_strain_slot(bench, 3)),
            width="100%",
            spacing="3",
        ),
        width="100%",
        background=bench["tint"],
        border_left_width="6px",
        border_left_style="solid",
        border_left_color=bench["accent"],
        box_shadow="0 8px 22px rgba(15, 23, 42, 0.08)",
        transition="transform 140ms ease, box-shadow 140ms ease",
        _hover={
            "transform": "translateY(-2px)",
            "box_shadow": "0 12px 28px rgba(15, 23, 42, 0.13)",
        },
    )


def cultivation_print_bench_strain(bench: rx.Var, slot_number: int) -> rx.Component:
    strain_key = f"strain_{slot_number}"
    percent_key = f"percent_{slot_number}"
    return rx.cond(
        bench[strain_key] != "",
        rx.hstack(
            rx.text(bench[strain_key], font_weight="800", color=DARK),
            rx.spacer(),
            rx.text(bench[percent_key].to_string() + "%", font_weight="700"),
            width="100%",
            align="center",
        ),
    )


def cultivation_print_bench_card(bench: rx.Var) -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.flex(
                rx.heading(bench["bench"], size="4", color=DARK),
                rx.spacer(),
                rx.text(
                    bench["length"].to_string()
                    + " × "
                    + bench["width"].to_string()
                    + " ft",
                    font_weight="700",
                ),
                width="100%",
                align="center",
            ),
            rx.text(
                bench["square_feet"].to_string()
                + " sq ft · "
                + bench["target_plants"].to_string()
                + " target plants",
                size="2",
                color=MUTED,
            ),
            rx.separator(size="4"),
            cultivation_print_bench_strain(bench, 1),
            cultivation_print_bench_strain(bench, 2),
            cultivation_print_bench_strain(bench, 3),
            width="100%",
            spacing="2",
        ),
        min_height="150px",
        padding="14px",
        border="2px solid #334155",
        border_left=f"8px solid {ACCENT}",
        border_radius="8px",
        break_inside="avoid",
        background="white",
    )


def cultivation_summary_table_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["strain"], font_weight="600", min_width="160px"),
        rx.table.cell(row["benches"], min_width="140px"),
        rx.table.cell(row["square_feet"].to_string(), text_align="right"),
        rx.table.cell(row["plant_density"], text_align="right"),
        rx.table.cell(row["target_plants"].to_string(), text_align="right"),
        rx.table.cell(row["recommended_clones"].to_string(), text_align="right"),
        rx.table.cell(row["domes"].to_string(), text_align="right"),
        rx.table.cell(
            row["actual_overage_percent"].to_string() + "%",
            text_align="right",
        ),
    )


def cultivation_room_layout_print_document() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.flex(
                rx.box(
                    rx.heading(
                        DashboardState.cultivation_cycle_name + " Room Layout",
                        size="7",
                        color=DARK,
                    ),
                    rx.text(
                        DashboardState.cultivation_flower_room,
                        size="4",
                        weight="bold",
                    ),
                ),
                rx.spacer(),
                rx.box(
                    rx.text(
                        "Flower Entry: " + DashboardState.cultivation_flower_entry_date,
                        weight="bold",
                    ),
                    rx.text(
                        "Projected Harvest: " + DashboardState.cultivation_harvest_date,
                        weight="bold",
                    ),
                    text_align="right",
                ),
                width="100%",
                align="center",
            ),
            rx.hstack(
                rx.badge(
                    "Density: "
                    + DashboardState.cultivation_plant_density.to_string()
                    + " plants / sq ft",
                    color_scheme="teal",
                    size="2",
                ),
                rx.badge(
                    "Target Plants: " + DashboardState.cultivation_total_target_plants,
                    color_scheme="green",
                    size="2",
                ),
                rx.badge(
                    "Clone Cuts: " + DashboardState.cultivation_total_clone_cuts,
                    color_scheme="purple",
                    size="2",
                ),
                gap="3",
                wrap="wrap",
            ),
            rx.grid(
                rx.foreach(
                    DashboardState.cultivation_bench_plans,
                    cultivation_print_bench_card,
                ),
                columns="2",
                gap="3",
                width="100%",
            ),
            rx.text(
                "Generated by QCC Control Tower · Room assignments reflect the current on-screen layout.",
                size="1",
                color=MUTED,
                margin_top="8px",
            ),
            width="100%",
            spacing="4",
        ),
        class_name="qcc-field-print-document qcc-room-layout-print-document",
        width="100%",
    )


def cultivation_clone_dome_print_document() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.flex(
                rx.box(
                    rx.heading(
                        DashboardState.cultivation_cycle_name
                        + " Clone & Dome Cutting Plan",
                        size="7",
                        color=DARK,
                    ),
                    rx.text(
                        DashboardState.cultivation_flower_room,
                        size="4",
                        weight="bold",
                    ),
                ),
                rx.spacer(),
                rx.box(
                    rx.text(
                        "Clone Cut: " + DashboardState.cultivation_cut_date,
                        weight="bold",
                    ),
                    rx.text(
                        "Move to Veg: " + DashboardState.cultivation_veg_transfer_date,
                        weight="bold",
                    ),
                    rx.text(
                        "Move to Flower: " + DashboardState.cultivation_flower_entry_date,
                        weight="bold",
                    ),
                    text_align="right",
                ),
                width="100%",
                align="center",
            ),
            rx.hstack(
                rx.badge(
                    "Density: "
                    + DashboardState.cultivation_plant_density.to_string()
                    + " plants / sq ft",
                    color_scheme="teal",
                    size="2",
                ),
                rx.badge(
                    "Safety Overage: "
                    + DashboardState.cultivation_overage_percent.to_string()
                    + "%",
                    color_scheme="purple",
                    size="2",
                ),
                rx.badge(
                    "Total Domes: " + DashboardState.cultivation_total_trays,
                    color_scheme="orange",
                    size="2",
                ),
                gap="3",
                wrap="wrap",
            ),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Strain"),
                        rx.table.column_header_cell("Bench(es)"),
                        rx.table.column_header_cell("Canopy sq ft", text_align="right"),
                        rx.table.column_header_cell("Plants / sq ft", text_align="right"),
                        rx.table.column_header_cell("Target Plants", text_align="right"),
                        rx.table.column_header_cell("Clone Cuts", text_align="right"),
                        rx.table.column_header_cell("32-Clone Trays / Domes", text_align="right"),
                        rx.table.column_header_cell("Actual Overage", text_align="right"),
                    )
                ),
                rx.table.body(
                    rx.foreach(
                        DashboardState.cultivation_strain_summary_rows,
                        cultivation_summary_table_row,
                    )
                ),
                class_name="qcc-clone-dome-print-table",
                width="100%",
                variant="surface",
            ),
            rx.grid(
                rx.box(
                    rx.text("Target Flower Plants", size="1", color=MUTED),
                    rx.heading(DashboardState.cultivation_total_target_plants, size="5"),
                ),
                rx.box(
                    rx.text("Recommended Clone Cuts", size="1", color=MUTED),
                    rx.heading(DashboardState.cultivation_total_clone_cuts, size="5"),
                ),
                rx.box(
                    rx.text("32-Clone Trays / Domes", size="1", color=MUTED),
                    rx.heading(DashboardState.cultivation_total_trays, size="5"),
                ),
                columns="3",
                gap="4",
                width="100%",
            ),
            rx.text(
                "Generated by QCC Control Tower · Verify mother and Veg availability before cutting.",
                size="1",
                color=MUTED,
            ),
            width="100%",
            spacing="4",
        ),
        class_name="qcc-field-print-document qcc-clone-dome-print-document",
        width="100%",
    )


def cultivation_risk_badge(risk: rx.Var) -> rx.Component:
    return rx.match(
        risk,
        (
            "Balanced",
            rx.badge(
                "BALANCED", background="#16a34a", color="#ffffff", size="2",
                font_weight="900", min_width="94px", justify_content="center",
            ),
        ),
        (
            "Warning",
            rx.badge(
                "WARNING", background="#facc15", color="#422006", size="2",
                font_weight="900", min_width="94px", justify_content="center",
            ),
        ),
        (
            "Excess",
            rx.badge(
                "EXCESS", background="#dc2626", color="#ffffff", size="2",
                font_weight="900", min_width="94px", justify_content="center",
            ),
        ),
        rx.badge(
            "REVIEW", color_scheme="gray", variant="solid", size="2",
            font_weight="900", min_width="94px", justify_content="center",
        ),
    )


def cultivation_future_yield_table_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["strain"], font_weight="600", min_width="150px"),
        rx.table.cell(row["current_lbs"].to_string(), text_align="right"),
        rx.table.cell(row["scheduled_lbs"].to_string(), text_align="right"),
        rx.table.cell(row["scheduled_window"], min_width="125px"),
        rx.table.cell(row["proposed_lbs"].to_string(), text_align="right"),
        rx.table.cell(row["weekly_demand_lbs"].to_string(), text_align="right"),
        rx.table.cell(row["expiring_lbs"].to_string(), text_align="right"),
        rx.table.cell(row["projected_balance_lbs"].to_string(), text_align="right"),
        rx.table.cell(row["weeks_supply"], text_align="right"),
        rx.table.cell(cultivation_risk_badge(row["risk"])),
    )


def cultivation_info_header(title: str, definition: str) -> rx.Component:
    return rx.table.column_header_cell(
        rx.tooltip(
            rx.hstack(
                rx.text(title, white_space="pre-line", line_height="1.1"),
                rx.icon("info", size=14),
                gap="1",
                align="center",
            ),
            content=definition,
        ),
        min_width="105px",
        vertical_align="middle",
    )


def cultivation_upcoming_crop_table_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["crop"], font_weight="600"),
        rx.table.cell(row["room"]),
        rx.table.cell(row["harvest_date"]),
        rx.table.cell(row["estimated_yield_lbs"].to_string() + " lb", text_align="right"),
    )


def cultivation_saved_allocation_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["cycle_name"], font_weight="700", min_width="120px"),
        rx.table.cell(row["flower_room"], min_width="130px"),
        rx.table.cell(row["flower_entry_date"], min_width="120px"),
        rx.table.cell(row["strains"], min_width="300px", white_space="normal"),
        rx.table.cell(row["target_plants"].to_string(), text_align="right"),
        rx.table.cell(row["clone_cuts"].to_string(), text_align="right"),
        rx.table.cell(row["created_by"], min_width="150px"),
        rx.table.cell(
            rx.button(
                "Load",
                on_click=DashboardState.load_cultivation_allocation(
                    row["allocation_id"]
                ),
                variant="outline",
                size="1",
            )
        ),
    )


def cultivation_scheduled_supply_detail(detail: rx.Var) -> rx.Component:
    """One auditable crop/strain projection inside a Scheduled popover."""
    return rx.box(
        rx.hstack(
            rx.text(detail["crop"], weight="bold", color=DARK),
            rx.badge(
                detail["status"],
                color_scheme=rx.cond(
                    detail["forecast_counted_lbs"] > 0, "blue", "orange"
                ),
            ),
            justify="between",
            width="100%",
        ),
        rx.text(
            "Harvest " + detail["harvest_date"]
            + " · Expected " + detail["available_date"],
            size="1",
            color=MUTED,
        ),
        rx.grid(
            rx.text("Gross projected", size="2"),
            rx.text(
                detail["gross_projected_lbs"].to_string() + " lb",
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Fresh Frozen reduction", size="2"),
            rx.text(
                detail["fresh_frozen_reduction_lbs"].to_string() + " lb",
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Fresh Frozen basis", size="2"),
            rx.text(
                detail["fresh_frozen_source"],
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Fresh Frozen plants used", size="2"),
            rx.text(
                detail["fresh_frozen_plants"].to_string()
                + " of " + detail["planted_plants"].to_string(),
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Creative Use reduction", size="2"),
            rx.text(
                detail["creative_use_reduction_lbs"].to_string() + " lb",
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Net projected", size="2"),
            rx.text(
                detail["net_projected_lbs"].to_string() + " lb",
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Actual processed", size="2"),
            rx.text(
                detail["actual_processed_lbs"].to_string() + " lb",
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Unconfirmed remainder", size="2"),
            rx.text(
                detail["unconfirmed_remainder_lbs"].to_string() + " lb",
                size="2", weight="bold", text_align="right",
            ),
            rx.text("Forecast counted", size="2", weight="bold"),
            rx.text(
                detail["forecast_counted_lbs"].to_string() + " lb",
                size="2", weight="bold", color="#0f766e", text_align="right",
            ),
            columns="1fr auto",
            gap="2",
            width="100%",
            margin_top="8px",
        ),
        rx.cond(
            detail["can_edit_fresh_frozen"],
            rx.box(
                rx.text(
                    "Planned Fresh Frozen plants",
                    size="1", weight="bold", color=MUTED,
                ),
                rx.hstack(
                    rx.input(
                        type="number",
                        min="0",
                        max=detail["planted_plants"].to_string(),
                        step="1",
                        default_value=detail[
                            "planned_fresh_frozen_plants"
                        ].to_string(),
                        on_blur=lambda value: DashboardState.save_cultivation_fresh_frozen_plants(
                            detail["crop"], detail["strain"],
                            detail["harvest_date"], detail["planted_plants"], value,
                        ),
                        width="110px",
                        size="1",
                    ),
                    rx.text(
                        "of " + detail["planted_plants"].to_string()
                        + " plants (" + detail["fresh_frozen_percent"].to_string()
                        + "%)",
                        size="1", color=MUTED,
                    ),
                    align="center",
                    gap="2",
                ),
                margin_top="10px",
            ),
            rx.cond(
                detail["actual_fresh_frozen_detected"],
                rx.box(
                    rx.text(
                        "Actual Metrc Fresh Frozen overrides the manual plan: "
                        + detail["actual_fresh_frozen_plants"].to_string()
                        + " plants · "
                        + detail["actual_fresh_frozen_wet_lbs"].to_string()
                        + " wet lb.",
                        size="1", weight="bold", color="#7c3aed",
                    ),
                    rx.text(
                        detail["actual_fresh_frozen_batches"],
                        size="1", color=MUTED,
                    ),
                    margin_top="8px",
                ),
                rx.text(
                    "Fresh Frozen planning locked after harvest day.",
                    size="1", color=MUTED, margin_top="8px",
                ),
            ),
        ),
        rx.cond(
            detail["can_edit_creative_use"],
            rx.box(
                rx.text(
                    "Creative Use reduction (lb)",
                    size="1", weight="bold", color=MUTED,
                ),
                rx.hstack(
                    rx.input(
                        type="number",
                        min="0",
                        max=(
                            detail["gross_projected_lbs"]
                            - detail["fresh_frozen_reduction_lbs"]
                        ).to_string(),
                        step="0.1",
                        default_value=detail["creative_use_reduction_lbs"].to_string(),
                        on_blur=lambda value: DashboardState.save_cultivation_creative_use_lbs(
                            detail["crop"], detail["strain"],
                            detail["gross_projected_lbs"]
                            - detail["fresh_frozen_reduction_lbs"], value,
                        ),
                        width="110px",
                        size="1",
                    ),
                    rx.text(
                        "Redirected to blends, co-packing, or another planned use",
                        size="1", color=MUTED,
                    ),
                    align="center",
                    gap="2",
                ),
                margin_top="10px",
            ),
            rx.text(
                "Creative Use is locked after actual crop inventory appears or the projection expires.",
                size="1", color=MUTED, margin_top="8px",
            ),
        ),
        padding_bottom="10px",
        border_bottom="1px solid #e5e7eb",
        width="100%",
    )


def cultivation_clone_plan_value_cell(
    cell: rx.Var, metric: rx.Var, weekly_demand: rx.Var, strain: rx.Var
) -> rx.Component:
    value = cell["value"]
    is_current_pounds = metric == "Current Pounds"
    weeks_supply = value / rx.cond(weekly_demand > 0, weekly_demand, 1)
    current_background = rx.cond(
        value < 0,
        "#b91c1c",
        rx.cond(
            weekly_demand <= 0,
            "#f3f4f6",
            rx.cond(
                weeks_supply <= 4,
                "#dcfce7",
                rx.cond(weeks_supply <= 8, "#fef3c7", "#fee2e2"),
            ),
        ),
    )
    current_color = rx.cond(
        value < 0,
        "#ffffff",
        rx.cond(
            weekly_demand <= 0,
            "#4b5563",
            rx.cond(
                weeks_supply <= 4,
                "#166534",
                rx.cond(weeks_supply <= 8, "#92400e", "#b91c1c"),
            ),
        ),
    )
    current_breakdown_content = rx.cond(
        cell["show_breakdown"],
        rx.popover.root(
            rx.popover.trigger(
                rx.button(
                    rx.text(value.to_string(), font_variant_numeric="tabular-nums"),
                    rx.icon("info", size=12),
                    variant="ghost",
                    size="1",
                    color=current_color,
                    font_weight="900",
                    padding="2px 4px",
                    min_height="24px",
                    cursor="pointer",
                    text_decoration="underline dotted",
                    text_underline_offset="3px",
                )
            ),
            rx.popover.content(
                rx.vstack(
                    rx.box(
                        rx.text("Current Pounds Breakdown", weight="bold", color=DARK),
                        rx.text(
                            "Usable net-flower supply by production stage",
                            size="1",
                            color=MUTED,
                        ),
                    ),
                    rx.hstack(
                        rx.text("CPG · packaged flower/pre-rolls", size="2"),
                        rx.spacer(),
                        rx.text(
                            cell["cpg_lbs"].to_string() + " lb",
                            weight="bold",
                            size="2",
                        ),
                        width="100%",
                    ),
                    rx.hstack(
                        rx.text("WIP-Cultivation · tested flower bulk", size="2"),
                        rx.spacer(),
                        rx.text(
                            cell["wip_lbs"].to_string() + " lb",
                            weight="bold",
                            size="2",
                        ),
                        width="100%",
                    ),
                    rx.hstack(
                        rx.vstack(
                            rx.text("Pre-WIP-Cultivation · pending testing", size="2"),
                            rx.text(
                                rx.cond(
                                    DashboardState.cultivation_clone_plan_include_pre_wip,
                                    "Included in Current Pounds",
                                    "Excluded from Current Pounds",
                                ),
                                size="1",
                                color=rx.cond(
                                    DashboardState.cultivation_clone_plan_include_pre_wip,
                                    "#7c3aed",
                                    MUTED,
                                ),
                            ),
                            spacing="0",
                            align="start",
                        ),
                        rx.spacer(),
                        rx.text(
                            cell["pre_wip_lbs"].to_string() + " lb",
                            weight="bold",
                            size="2",
                        ),
                        width="100%",
                        align="center",
                    ),
                    rx.separator(size="4"),
                    rx.hstack(
                        rx.text("Current pounds", weight="bold"),
                        rx.spacer(),
                        rx.text(
                            cell["current_total_lbs"].to_string() + " lb",
                            weight="bold",
                            color="#0f766e",
                        ),
                        width="100%",
                    ),
                    spacing="2",
                    width="330px",
                ),
                side="top",
                align="center",
            ),
        ),
        rx.text(value.to_string(), font_variant_numeric="tabular-nums"),
    )
    scheduled_content = rx.popover.root(
        rx.popover.trigger(
            rx.button(
                rx.text(value.to_string(), font_variant_numeric="tabular-nums"),
                rx.icon("info", size=12),
                variant="ghost",
                size="1",
                color="#1d4ed8",
                font_weight="800",
                cursor="pointer",
                text_decoration="underline dotted",
                text_underline_offset="3px",
            )
        ),
        rx.popover.content(
            rx.vstack(
                rx.heading("Scheduled Supply Detail", size="3", color=DARK),
                rx.text(
                    "Projected pounds are excluded once actual crop-matched inventory appears and expire 45 days after harvest.",
                    size="1",
                    color=MUTED,
                ),
                rx.foreach(
                    cell["scheduled_details"],
                    cultivation_scheduled_supply_detail,
                ),
                spacing="3",
                width="390px",
            ),
            side="top",
            align="center",
        ),
    )
    allocation_content = rx.cond(
        cell["editable_allocation"],
        rx.input(
            type="number",
            min="0",
            max="7",
            step="0.1",
            default_value=value.to_string(),
            key=(
                DashboardState.cultivation_clone_plan_entry_version.to_string()
                + "-" + strain.to_string()
            ),
            on_blur=lambda input_value: DashboardState.change_cultivation_clone_plan_allocation(
                strain, input_value
            ),
            disabled=~DashboardState.cultivation_clone_plan_editable,
            width="100%",
            size="1",
            text_align="center",
        ),
        rx.cond(
            cell["historical_editable"],
            rx.input(
                type="number",
                min="0",
                max="7",
                step="0.1",
                default_value=value.to_string(),
                key=(
                    DashboardState.cultivation_historical_plan_entry_version.to_string()
                    + "-" + cell["crop"] + "-" + strain.to_string()
                ),
                on_blur=lambda input_value: DashboardState.change_cultivation_historical_plan_allocation(
                    strain, input_value
                ),
                width="100%",
                size="1",
                text_align="center",
            ),
            rx.text(value.to_string(), font_variant_numeric="tabular-nums"),
        ),
    )
    value_content = rx.cond(
        ~cell["available"],
        rx.text("—", color="#9ca3af"),
        rx.cond(
            metric == "Clone Allocation",
            allocation_content,
            rx.cond(
                (metric == "Scheduled") & (cell["scheduled_details"].length() > 0),
                scheduled_content,
                current_breakdown_content,
            ),
        ),
    )
    return rx.table.cell(
        value_content,
        text_align="right",
        color=rx.cond(is_current_pounds, current_color, DARK),
        font_weight=rx.cond(is_current_pounds, "800", "500"),
        box_shadow=rx.cond(
            cell["highlight"],
            "none",
            rx.cond(
                is_current_pounds & (value < 0),
                "inset 0 0 0 2px #7f1d1d",
                "none",
            ),
        ),
        background_color=rx.cond(
            cell["editable_allocation"],
            "#f5f3ff",
            rx.cond(
                cell["historical_allocation"],
                "#faf5ff",
                rx.cond(
                    cell["highlight"],
                    "#f5f3ff",
                    rx.cond(is_current_pounds, current_background, "transparent"),
                ),
            ),
        ),
        outline=rx.cond(
            cell["editable_allocation"] | cell["historical_editable"] | cell["highlight"],
            "3px solid #8b5cf6",
            "none",
        ),
        outline_offset=rx.cond(
            cell["editable_allocation"] | cell["historical_editable"] | cell["highlight"], "-3px", "0"
        ),
        min_width="102px",
        white_space="nowrap",
    )


def cultivation_clone_plan_matrix_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            row["strain"],
            font_weight="800",
            width="200px",
            min_width="200px",
            max_width="200px",
            background="#ffffff",
            position="sticky",
            left="0",
            z_index="2",
        ),
        rx.table.cell(
            row["metric"],
            font_weight="700",
            width="170px",
            min_width="170px",
            max_width="170px",
            background=rx.match(
                row["metric"],
                ("Clone Allocation", "#ede9fe"),
                ("Current Pounds", "#ecfdf5"),
                ("Scheduled", "#eff6ff"),
                ("Two-Week Demand", "#fff7ed"),
                "#ffffff",
            ),
            position="sticky",
            left="200px",
            z_index="2",
        ),
        rx.foreach(
            row["values"],
            lambda cell: cultivation_clone_plan_value_cell(
                cell, row["metric"], row["weekly_demand"], row["strain"]
            ),
        ),
        class_name="qcc-clone-plan-row",
    )


def cultivation_clone_plan_period_header(period: rx.Var) -> rx.Component:
    return rx.table.column_header_cell(
        rx.vstack(
            rx.text(period["crop"], weight="bold", color="#ffffff"),
            rx.text(period["date_label"], size="1", color="#cbd5e1"),
            spacing="0",
            align="center",
        ),
        text_align="center",
        min_width="102px",
        vertical_align="middle",
        class_name=rx.cond(period["is_current"], "qcc-current-clone-period", ""),
    )


def cultivation_clone_plan_page_period_header(period: rx.Var) -> rx.Component:
    return rx.table.column_header_cell(
        rx.tooltip(
            rx.vstack(
                rx.text(period["crop"], weight="bold", color="#ffffff"),
                rx.text(period["date_label"], size="1", color="#cbd5e1"),
                rx.cond(
                    period["is_historical"],
                    rx.cond(
                        DashboardState.cultivation_historical_plan_editing
                        & (DashboardState.cultivation_historical_plan_crop == period["crop"]),
                        rx.hstack(
                            rx.button(
                                "Save",
                                on_click=DashboardState.save_cultivation_historical_plan,
                                loading=DashboardState.cultivation_historical_plan_saving,
                                size="1",
                                color_scheme="green",
                                height="20px",
                            ),
                            rx.icon_button(
                                "x",
                                on_click=DashboardState.cancel_cultivation_clone_plan_history_edit,
                                size="1",
                                variant="soft",
                                color_scheme="gray",
                                height="20px",
                                width="20px",
                            ),
                            gap="1",
                        ),
                        rx.button(
                            rx.icon("pencil", size=11),
                            "Edit",
                            on_click=DashboardState.edit_cultivation_historical_lookback(
                                period["crop"]
                            ),
                            size="1",
                            variant="solid",
                            background="#e9d5ff",
                            color="#4c1d95",
                            border="1px solid #c4b5fd",
                            _hover={"background": "#ddd6fe"},
                            height="20px",
                        ),
                    ),
                    rx.box(height="20px"),
                ),
                spacing="0",
                align="center",
            ),
            content=period["harvest_date_full_label"],
        ),
        text_align="center",
        width="102px",
        min_width="102px",
        max_width="102px",
        height="82px",
        padding="0",
        cursor="help",
        class_name=rx.cond(
            period["is_current"],
            "qcc-clone-plan-page-period qcc-current-clone-period",
            "qcc-clone-plan-page-period",
        ),
    )


def cultivation_clone_plan_history_row(plan: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(plan["crop"], weight="bold"),
        rx.table.cell(plan["clone_cut_date"]),
        rx.table.cell(plan["allocation_benches"]),
        rx.table.cell(
            rx.badge(
                plan["status"],
                color_scheme=rx.cond(plan["status"] == "Approved", "green", "purple"),
                variant="solid",
            )
        ),
        rx.table.cell(
            rx.button(
                rx.icon("pencil", size=14),
                "Edit",
                on_click=DashboardState.edit_cultivation_clone_plan_history(
                    plan["plan_id"]
                ),
                variant="outline",
                color_scheme="purple",
                size="1",
            )
        ),
    )


def cultivation_current_plan_history_row(plan: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(plan["crop"], weight="bold"),
        rx.table.cell(plan["flower_room"]),
        rx.table.cell(plan["clone_cut_date"]),
        rx.table.cell(plan["allocation_benches"]),
        rx.table.cell(
            rx.badge(
                plan["status"],
                color_scheme=rx.cond(plan["status"] == "Approved", "green", "purple"),
                variant="solid",
            )
        ),
        rx.table.cell(
            rx.button(
                rx.icon("upload", size=14),
                "Load",
                on_click=DashboardState.load_current_clone_plan(plan["plan_id"]),
                variant="outline",
                color_scheme="purple",
                size="1",
            )
        ),
    )


def cultivation_historical_plan_entry_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["strain"], font_weight="700"),
        rx.table.cell(
            rx.input(
                type="number",
                min="0",
                max="7",
                step="0.1",
                default_value=row["allocation"].to_string(),
                key=(
                    DashboardState.cultivation_historical_plan_entry_version.to_string()
                    + "-"
                    + row["strain"].to_string()
                ),
                on_blur=lambda value: DashboardState.change_cultivation_historical_plan_allocation(
                    row["strain"], value
                ),
                width="110px",
                size="1",
                text_align="center",
            ),
            text_align="right",
        ),
    )


def cultivation_approved_plan_allocation_row(row: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["crop"], font_weight="800"),
        rx.table.cell(row["flower_room"]),
        rx.table.cell(row["flower_entry_date"]),
        rx.table.cell(row["allocation_benches"], min_width="420px", white_space="normal"),
        rx.table.cell(
            rx.badge("Approved", color_scheme="green", variant="solid")
        ),
        rx.table.cell(
            rx.button(
                rx.icon("download", size=14),
                "Load Plan",
                on_click=DashboardState.load_approved_clone_plan_to_allocation(
                    row["plan_id"]
                ),
                color_scheme="purple",
                size="1",
            )
        ),
    )


def cultivation_new_strain_control() -> rx.Component:
    return rx.box(
        rx.flex(
            rx.box(
                rx.hstack(
                    rx.text(
                        "New / Provisional Strain",
                        size="2",
                        weight="bold",
                        color=DARK,
                    ),
                    rx.badge("NO HISTORY REQUIRED", color_scheme="purple"),
                    gap="2",
                    align="center",
                ),
                rx.text(
                    "Add a cultivar that has not harvested or shipped yet. It becomes available in both planning and exact bench assignment.",
                    size="1",
                    color=MUTED,
                ),
                min_width="330px",
            ),
            rx.input(
                value=DashboardState.cultivation_new_strain_name,
                on_change=DashboardState.change_cultivation_new_strain_name,
                placeholder="Example: Hood Candy",
                width=rx.breakpoints(initial="100%", md="290px"),
            ),
            rx.button(
                "Add Provisional Strain",
                on_click=DashboardState.add_cultivation_provisional_strain,
                color_scheme="purple",
                white_space="nowrap",
            ),
            align="center",
            wrap="wrap",
            gap="3",
            width="100%",
        ),
        rx.cond(
            DashboardState.cultivation_new_strain_error != "",
            rx.text(
                DashboardState.cultivation_new_strain_error,
                size="1",
                color="#b91c1c",
                margin_top="6px",
            ),
        ),
        rx.cond(
            DashboardState.cultivation_new_strain_message != "",
            rx.text(
                DashboardState.cultivation_new_strain_message,
                size="1",
                color="#166534",
                margin_top="6px",
            ),
        ),
        padding="12px",
        border="1px solid #ddd6fe",
        border_radius="10px",
        background="#faf8ff",
        width="100%",
    )


def cultivation_clone_planning_panel() -> rx.Component:
    return rx.vstack(
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.hstack(
                            rx.heading("Clone Planning", size="5", color=DARK),
                            rx.badge(
                                DashboardState.cultivation_clone_plan_status,
                                color_scheme=rx.cond(
                                    DashboardState.cultivation_clone_plan_status == "Approved",
                                    "green",
                                    "purple",
                                ),
                                variant="solid",
                            ),
                            gap="2",
                            align="center",
                        ),
                        rx.text(
                            "Plan bench equivalents against usable inventory, crops already in flight, and two-week demand.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.vstack(
                        rx.badge(
                            DashboardState.cultivation_current_crop_badge,
                            color_scheme="teal",
                            size="2",
                        ),
                        rx.text(
                            DashboardState.cultivation_clone_plan_edit_window_label,
                            size="1",
                            color=MUTED,
                        ),
                        spacing="1",
                        align="end",
                    ),
                    width="100%",
                    align="center",
                    wrap="wrap",
                    gap="3",
                ),
                rx.flex(
                    rx.box(
                        rx.text("Current crop name", size="1", weight="bold", color=MUTED),
                        rx.input(
                            value=DashboardState.cultivation_current_crop_draft,
                            on_change=DashboardState.set_cultivation_current_crop_draft,
                            width="220px",
                        ),
                    ),
                    rx.button(
                        "Save Crop Name",
                        on_click=DashboardState.save_current_crop_name,
                        variant="outline",
                    ),
                    align="end",
                    gap="3",
                    wrap="wrap",
                    width="100%",
                ),
                rx.grid(
                    rx.box(
                        rx.text("Demand model", size="1", weight="bold", color=MUTED),
                        rx.select(
                            [
                                "AI-Adjusted",
                                "Availability-Adjusted",
                                "30-Day Availability-Adjusted",
                                "60-Day Availability-Adjusted",
                                "Current SKU Velocity",
                            ],
                            value=DashboardState.cultivation_clone_plan_demand_model,
                            on_change=DashboardState.change_cultivation_clone_plan_demand_model,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Strain list", size="1", weight="bold", color=MUTED),
                        rx.select(
                            ["Clade9 Strains", "Clade9 + Craft Kings Strains"],
                            value=DashboardState.cultivation_clone_strain_scope,
                            on_change=DashboardState.change_cultivation_clone_strain_scope,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Demand products", size="1", weight="bold", color=MUTED),
                        rx.select(
                            [
                                "Flower + Pre-Rolls",
                                "Pre-Rolls Only",
                                "Flower Only",
                            ],
                            value=DashboardState.cultivation_clone_plan_product_scope,
                            on_change=DashboardState.change_cultivation_clone_plan_product_scope,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Current Pounds", size="1", weight="bold", color=MUTED),
                        rx.hstack(
                            rx.switch(
                                checked=DashboardState.cultivation_clone_plan_include_pre_wip,
                                on_change=DashboardState.change_cultivation_clone_plan_include_pre_wip,
                                color_scheme="purple",
                            ),
                            rx.text("Include Pre-WIP", size="2", weight="bold"),
                            align="center",
                            spacing="2",
                            min_height="40px",
                        ),
                    ),
                    snapshot_stat_card(
                        "Planned Bench Equivalents",
                        DashboardState.cultivation_clone_plan_total_benches,
                        "#7c3aed",
                    ),
                    snapshot_stat_card(
                        "Recommended Cuts",
                        DashboardState.cultivation_clone_plan_total_clones,
                        "#0f766e",
                    ),
                    snapshot_stat_card(
                        DashboardState.cultivation_current_room_capacity_title,
                        DashboardState.cultivation_clone_plan_room_capacity,
                        "#2563eb",
                    ),
                    columns=rx.breakpoints(initial="1", md="2", xl="7"),
                    gap="3",
                    width="100%",
                ),
                rx.cond(
                    DashboardState.cultivation_clone_plan_demand_model
                    == "AI-Adjusted",
                    rx.callout(
                        "AI-Adjusted is a first-pass shadow forecast: 45% 30-day, 35% 60-day, and 20% long-term availability-adjusted demand, plus a bounded recent trend that fades across future periods and conservative seasonality when enough history exists.",
                        icon="sparkles",
                        color_scheme="purple",
                        width="100%",
                    ),
                ),
                cultivation_new_strain_control(),
                rx.callout(
                    rx.cond(
                        DashboardState.cultivation_clone_plan_include_pre_wip,
                        "Current Pounds combines CPG, WIP-Cultivation, and—because the toggle is on—Pre-WIP-Cultivation. Purchased or partner-owned 1A bulk, trim, shake, retention, samples, and manufacturing bulk remain excluded. Scheduled pounds arrive 30 days after harvest and expire 45 days after harvest.",
                        "Current Pounds combines CPG and WIP-Cultivation. Pre-WIP-Cultivation is excluded unless the user turns on Include Pre-WIP. Purchased or partner-owned 1A bulk, trim, shake, retention, samples, and manufacturing bulk remain excluded. Scheduled pounds arrive 30 days after harvest and expire 45 days after harvest.",
                    ),
                    icon="info",
                    color_scheme="blue",
                    width="100%",
                ),
                width="100%",
                spacing="4",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.heading("Rolling Clone Planner", size="4", color=DARK),
                        rx.text(
                            "Enter 0.1–7.0 bench equivalents in the first allocation column. Projected physical inventory stops at zero.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.hstack(
                        rx.badge("14-DAY PERIODS", color_scheme="purple", size="2"),
                        rx.select(
                            [
                                "No Historical Crops",
                                "Last 4 Crops",
                                "Last 8 Crops",
                            ],
                            value=DashboardState.cultivation_clone_plan_lookback,
                            on_change=DashboardState.change_cultivation_clone_plan_lookback,
                            width="180px",
                            size="2",
                        ),
                        gap="2",
                        align="center",
                    ),
                    width="100%",
                    align="center",
                    wrap="wrap",
                    gap="2",
                ),
                rx.box(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell(
                                    "Strain",
                                    width="200px",
                                    min_width="200px",
                                    max_width="200px",
                                ),
                                rx.table.column_header_cell(
                                    "Planning Row",
                                    width="170px",
                                    min_width="170px",
                                    max_width="170px",
                                ),
                                rx.foreach(
                                    DashboardState.cultivation_clone_plan_periods,
                                    cultivation_clone_plan_page_period_header,
                                ),
                            ),
                            class_name="qcc-clone-plan-page-header",
                        ),
                        rx.table.body(
                            rx.foreach(
                                DashboardState.cultivation_clone_plan_matrix_rows,
                                cultivation_clone_plan_matrix_row,
                            )
                        ),
                        variant="surface",
                        size="2",
                        width="max-content",
                        min_width="100%",
                        class_name="qcc-clone-plan-table",
                    ),
                    width="100%",
                    max_height="70vh",
                    overflow="auto",
                    border="1px solid #64748b",
                    border_radius="10px",
                    class_name="qcc-clone-plan-viewport",
                ),
                rx.cond(
                    DashboardState.cultivation_clone_plan_error != "",
                    rx.callout(
                        DashboardState.cultivation_clone_plan_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                ),
                rx.cond(
                    DashboardState.cultivation_clone_plan_message != "",
                    rx.callout(
                        DashboardState.cultivation_clone_plan_message,
                        icon="circle_check",
                        color_scheme="green",
                        width="100%",
                    ),
                ),
                rx.flex(
                    rx.button(
                        "Approve Plan",
                        on_click=DashboardState.approve_cultivation_clone_plan,
                        loading=DashboardState.cultivation_clone_plan_saving,
                        color_scheme="green",
                    ),
                    rx.spacer(),
                    rx.cond(
                        DashboardState.is_administrator,
                        rx.hstack(
                            rx.switch(
                                checked=DashboardState.cultivation_clone_plan_override,
                                on_change=DashboardState.toggle_cultivation_clone_plan_override,
                            ),
                            rx.text("Admin override", size="2", weight="bold"),
                            rx.input(
                                value=DashboardState.cultivation_clone_plan_override_reason,
                                on_change=DashboardState.change_cultivation_clone_plan_override_reason,
                                placeholder="Required reason",
                                width="240px",
                                size="2",
                            ),
                            gap="2",
                            align="center",
                        ),
                    ),
                    width="100%",
                    gap="2",
                    align="center",
                    wrap="wrap",
                ),
                width="100%",
                spacing="4",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.heading("Planning History", size="4", color=DARK),
                        rx.text(
                            "Current plans reload into the Rolling Clone Planner. Historical allocations are edited directly in their lookback columns above.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.hstack(
                        rx.button(
                            rx.icon("refresh_cw", size=16),
                            "Refresh",
                            on_click=DashboardState.load_cultivation_clone_plan_history,
                            variant="outline",
                            color_scheme="teal",
                            size="2",
                        ),
                        gap="2",
                    ),
                    width="100%",
                    align="center",
                    wrap="wrap",
                    gap="2",
                ),
                cultivation_new_strain_control(),
                rx.heading("Current Plans", size="3", color=DARK),
                rx.cond(
                    DashboardState.cultivation_current_plan_history_rows.length() > 0,
                    rx.box(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("Crop"),
                                    rx.table.column_header_cell("Flower Room"),
                                    rx.table.column_header_cell("Clone Cut"),
                                    rx.table.column_header_cell("Bench Allocations"),
                                    rx.table.column_header_cell("Status"),
                                    rx.table.column_header_cell("Load"),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(
                                    DashboardState.cultivation_current_plan_history_rows,
                                    cultivation_current_plan_history_row,
                                )
                            ),
                            variant="surface",
                            size="2",
                            width="100%",
                        ),
                        width="100%",
                        overflow_x="auto",
                    ),
                    rx.callout(
                        "No current plan has been approved yet. Approve the Rolling Clone Planner to create it.",
                        icon="info",
                        color_scheme="gray",
                        width="100%",
                    ),
                ),
                rx.accordion.root(
                    rx.accordion.item(
                        header=rx.hstack(
                            rx.icon("history", size=17),
                            rx.text(
                                rx.cond(
                                    DashboardState.cultivation_historical_plan_edit_id != "",
                                    "Edit Saved Clone Plan",
                                    "Enter an Older Clone Plan",
                                ),
                                weight="bold",
                            ),
                            rx.badge("F2.10–F4.10 INCLUDED", color_scheme="blue"),
                            gap="2",
                            align="center",
                        ),
                        content=rx.vstack(
                            rx.callout(
                                rx.cond(
                                    DashboardState.cultivation_historical_plan_edit_id != "",
                                    "Update the saved bench equivalents below. Saving replaces the existing planning-history record while preserving its crop, date, room, and status.",
                                    "Use this form for an older plan completed before the Control Tower began saving clone plans. Enter bench equivalents by strain, then save it as an approved historical record.",
                                ),
                                icon="info",
                                color_scheme="blue",
                                width="100%",
                            ),
                            rx.flex(
                                rx.box(
                                    rx.text(
                                        "Historical crop",
                                        size="1",
                                        weight="bold",
                                        color=MUTED,
                                    ),
                                    rx.select(
                                        DashboardState.cultivation_historical_plan_crop_options,
                                        value=DashboardState.cultivation_historical_plan_crop,
                                        on_change=DashboardState.change_cultivation_historical_plan_crop,
                                        disabled=DashboardState.cultivation_historical_plan_edit_id != "",
                                        width="180px",
                                    ),
                                ),
                                rx.box(
                                    rx.text(
                                        "Entered total",
                                        size="1",
                                        weight="bold",
                                        color=MUTED,
                                    ),
                                    rx.badge(
                                        DashboardState.cultivation_historical_plan_total,
                                        color_scheme="purple",
                                        size="2",
                                    ),
                                ),
                                rx.spacer(),
                                rx.hstack(
                                    rx.cond(
                                        DashboardState.cultivation_historical_plan_edit_id != "",
                                        rx.button(
                                            "Cancel",
                                            on_click=DashboardState.cancel_cultivation_clone_plan_history_edit,
                                            variant="outline",
                                            color_scheme="gray",
                                        ),
                                    ),
                                    rx.button(
                                        rx.cond(
                                            DashboardState.cultivation_historical_plan_edit_id != "",
                                            "Save Changes",
                                            "Save Historical Plan",
                                        ),
                                        on_click=DashboardState.save_cultivation_historical_plan,
                                        loading=DashboardState.cultivation_historical_plan_saving,
                                        color_scheme="blue",
                                    ),
                                    gap="2",
                                ),
                                width="100%",
                                align="end",
                                gap="3",
                                wrap="wrap",
                            ),
                            rx.box(
                                rx.table.root(
                                    rx.table.header(
                                        rx.table.row(
                                            rx.table.column_header_cell("Strain"),
                                            rx.table.column_header_cell(
                                                "Bench Equivalents", text_align="right"
                                            ),
                                        )
                                    ),
                                    rx.table.body(
                                        rx.foreach(
                                            DashboardState.cultivation_historical_plan_entry_rows,
                                            cultivation_historical_plan_entry_row,
                                        )
                                    ),
                                    variant="surface",
                                    size="2",
                                    width="100%",
                                ),
                                max_height="390px",
                                overflow_y="auto",
                                width="100%",
                            ),
                            width="100%",
                            spacing="3",
                        ),
                        value="historical-entry",
                    ),
                    type="single",
                    collapsible=True,
                    default_value="historical-entry",
                    width="100%",
                    variant="surface",
                    display="none",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        width="100%",
        spacing="4",
    )


def cultivation_clone_allocation_panel() -> rx.Component:
    return rx.vstack(
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.hstack(
                            rx.heading("Room Layout & Clone Plan", size="4", color=DARK),
                            rx.badge(
                                DashboardState.cultivation_cycle_name,
                                color_scheme="purple",
                                variant="solid",
                                size="2",
                            ),
                            rx.badge(
                                rx.hstack(
                                    rx.icon("lock", size=13),
                                    rx.text("FIXED TO CURRENT PLAN"),
                                    gap="1",
                                ),
                                color_scheme="gray",
                            ),
                            gap="2",
                            align="center",
                            wrap="wrap",
                        ),
                        rx.text(
                            "Room, crop, and flower date come directly from the current rolling clone plan.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.badge("40-day clone-to-flower cycle", color_scheme="teal", size="2"),
                    width="100%",
                    align="center",
                    wrap="wrap",
                    gap="3",
                ),
                rx.grid(
                    rx.box(
                        rx.text("Flower Room", size="1", weight="bold", color=MUTED),
                        rx.select(
                            [f"Flower Room {number}" for number in range(1, 6)],
                            value=DashboardState.cultivation_flower_room,
                            on_change=DashboardState.change_cultivation_flower_room,
                            disabled=True,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Cycle / Crop", size="1", weight="bold", color=MUTED),
                        rx.input(
                            value=DashboardState.cultivation_cycle_name,
                            on_change=DashboardState.change_cultivation_cycle_name,
                            disabled=True,
                            placeholder="Example: F4.10",
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Flower Entry Date", size="1", weight="bold", color=MUTED),
                        rx.input(
                            type="date",
                            value=DashboardState.cultivation_flower_entry_date,
                            on_change=DashboardState.change_cultivation_flower_entry_date,
                            disabled=True,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Clone Safety Overage", size="1", weight="bold", color=MUTED),
                        rx.select(
                            ["25%", "26%", "27%", "28%", "29%", "30%"],
                            value=DashboardState.cultivation_overage_percent.to_string() + "%",
                            on_change=DashboardState.change_cultivation_overage,
                            width="100%",
                        ),
                    ),
                    rx.box(
                        rx.text("Post-Harvest to Usable", size="1", weight="bold", color=MUTED),
                        rx.input(
                            type="number",
                            min="0",
                            max="90",
                            value=DashboardState.cultivation_post_harvest_days,
                            on_change=DashboardState.change_cultivation_post_harvest_days,
                            width="100%",
                        ),
                    ),
                    columns=rx.breakpoints(initial="1", sm="2", lg="5"),
                    gap="3",
                    width="100%",
                ),
                rx.grid(
                    snapshot_stat_card("Clone Cut Date", DashboardState.cultivation_cut_date, "#7c3aed"),
                    snapshot_stat_card("Move to Veg", DashboardState.cultivation_veg_transfer_date, "#2563eb"),
                    snapshot_stat_card("Move to Flower", DashboardState.cultivation_flower_entry_date, "#16a34a"),
                    snapshot_stat_card("Projected Harvest", DashboardState.cultivation_harvest_date, "#ea580c"),
                    snapshot_stat_card("Projected Usable", DashboardState.cultivation_available_date, "#0f766e"),
                    columns=rx.breakpoints(initial="1", sm="2", lg="5"),
                    gap="3",
                    width="100%",
                ),
                width="100%",
                spacing="4",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.hstack(
                            rx.heading(
                                DashboardState.cultivation_current_clone_plan_title,
                                size="4",
                                color=DARK,
                            ),
                            rx.badge("CURRENT APPROVED PLAN", color_scheme="green"),
                            gap="2",
                            align="center",
                        ),
                        rx.text(
                            "Load the current plan's saved room map, finalize its exact benches, and print the clone and dome instructions.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.button(
                        rx.icon("arrow_left", size=15),
                        "Back to Rolling Plan",
                        on_click=DashboardState.change_cultivation_view("clone_planning"),
                        variant="outline",
                        color_scheme="purple",
                        size="2",
                    ),
                    width="100%",
                    align="center",
                    wrap="wrap",
                    gap="2",
                ),
                rx.cond(
                    DashboardState.cultivation_approved_current_plan_rows.length() > 0,
                    rx.box(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("Cycle / Crop"),
                                    rx.table.column_header_cell("Flower Room"),
                                    rx.table.column_header_cell("Flower Entry"),
                                    rx.table.column_header_cell("Saved Bench Allocations"),
                                    rx.table.column_header_cell("Status"),
                                    rx.table.column_header_cell("Load"),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(
                                    DashboardState.cultivation_approved_current_plan_rows,
                                    cultivation_approved_plan_allocation_row,
                                )
                            ),
                            width="100%",
                            variant="surface",
                        ),
                        width="100%",
                        overflow_x="auto",
                    ),
                    rx.callout(
                        "No approved current plan is available. Approve the Rolling Clone Planner first.",
                        icon="info",
                        color_scheme="gray",
                        width="100%",
                    ),
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
            border_left="5px solid #8b5cf6",
            background="#faf8ff",
        ),
        rx.card(
            rx.vstack(
                rx.box(
                        rx.heading("Room Bench Map", size="4", color=DARK),
                        rx.text(
                            "Execution view: set plant density, assign the approved strains to exact benches, then use the layout and clone/dome plan with the cultivation team.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                rx.flex(
                    rx.badge(DashboardState.cultivation_room_square_feet, color_scheme="teal", size="2"),
                    rx.badge(DashboardState.cultivation_allocated_bench_count, color_scheme="blue", size="2"),
                    rx.box(
                        rx.text("Plant density", size="1", weight="bold", color=MUTED),
                        rx.hstack(
                            rx.input(
                                type="number",
                                min="0.10",
                                max="2.00",
                                step="0.05",
                                value=DashboardState.cultivation_plant_density,
                                on_change=DashboardState.change_cultivation_plant_density,
                                width="90px",
                                size="1",
                            ),
                            rx.text("plants / sq ft", size="1", color=MUTED),
                            gap="2",
                            align="center",
                        ),
                    ),
                    rx.box(
                        rx.text("Strain list", size="1", weight="bold", color=MUTED),
                        rx.select(
                            ["Clade9 Strains", "Clade9 + Craft Kings Strains"],
                            value=DashboardState.cultivation_clone_strain_scope,
                            on_change=DashboardState.change_cultivation_clone_strain_scope,
                            width="230px",
                            size="1",
                        ),
                    ),
                    rx.spacer(),
                    rx.button(
                        rx.cond(DashboardState.cultivation_layout_editing, "Lock Layout", "Edit Layout"),
                        on_click=DashboardState.toggle_cultivation_layout_editing,
                        variant="outline",
                        size="2",
                    ),
                    rx.button(
                        "Restore Defaults",
                        on_click=DashboardState.reset_cultivation_room_layout,
                        variant="outline",
                        color_scheme="gray",
                        size="2",
                    ),
                    rx.button(
                        "Clear Map",
                        on_click=DashboardState.clear_cultivation_allocations,
                        variant="outline",
                        color_scheme="gray",
                        size="2",
                    ),
                    rx.button(
                        rx.icon("printer", size=15),
                        "Print and Save Clone Plan",
                        on_click=DashboardState.save_and_print_cultivation_clone_plan,
                        loading=DashboardState.cultivation_saving,
                        disabled=DashboardState.cultivation_strain_summary_rows.length() == 0,
                        background="#7c3aed",
                        color="white",
                        size="2",
                    ),
                    width="100%",
                    align="end",
                    wrap="wrap",
                    gap="2",
                ),
                rx.cond(
                    DashboardState.cultivation_error != "",
                    rx.callout(
                        DashboardState.cultivation_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                ),
                rx.cond(
                    DashboardState.cultivation_message != "",
                    rx.callout(
                        DashboardState.cultivation_message,
                        icon="circle_check",
                        color_scheme="green",
                        width="100%",
                    ),
                ),
                rx.cond(
                    DashboardState.cultivation_unbalanced_benches != "",
                    rx.callout(
                        "Bench percentages must total 100%: "
                        + DashboardState.cultivation_unbalanced_benches,
                        icon="triangle_alert",
                        color_scheme="orange",
                        width="100%",
                    ),
                ),
                rx.grid(
                    rx.foreach(DashboardState.cultivation_bench_plans, cultivation_bench_card),
                    columns=rx.breakpoints(initial="1", md="2", xl="3"),
                    gap="3",
                    width="100%",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        rx.grid(
            snapshot_stat_card("Target Flower Plants", DashboardState.cultivation_total_target_plants, "#0f766e"),
            snapshot_stat_card("Recommended Clone Cuts", DashboardState.cultivation_total_clone_cuts, "#7c3aed"),
            snapshot_stat_card("32-Clone Trays / Domes", DashboardState.cultivation_total_trays, "#ea580c"),
            snapshot_stat_card("Projected Dry Yield", DashboardState.cultivation_projected_yield, "#2563eb"),
            columns=rx.breakpoints(initial="1", sm="2", lg="4"),
            gap="3",
            width="100%",
        ),
        rx.cond(
            DashboardState.cultivation_strain_summary_rows.length() > 0,
            rx.card(
                rx.vstack(
                    rx.box(
                        rx.heading("Clone & Dome Cutting Plan", size="4", color=DARK),
                        rx.text(
                            "Live execution totals from the exact bench map. Counts update immediately when density, overage, bench splits, or strains change.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                    rx.box(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("Strain"),
                                    rx.table.column_header_cell("Bench(es)"),
                                    rx.table.column_header_cell("Canopy sq ft", text_align="right"),
                                    rx.table.column_header_cell("Plants / sq ft", text_align="right"),
                                    rx.table.column_header_cell("Target Plants", text_align="right"),
                                    rx.table.column_header_cell("Clone Cuts", text_align="right"),
                                    rx.table.column_header_cell("32-Clone Trays / Domes", text_align="right"),
                                    rx.table.column_header_cell("Actual Overage", text_align="right"),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(
                                    DashboardState.cultivation_strain_summary_rows,
                                    cultivation_summary_table_row,
                                )
                            ),
                            width="100%",
                            min_width="1120px",
                            variant="surface",
                        ),
                        width="100%",
                        overflow_x="auto",
                    ),
                    width="100%",
                    spacing="3",
                ),
                width="100%",
                border_left="5px solid #7c3aed",
            ),
        ),
        rx.cond(
            DashboardState.cultivation_future_yield_rows.length() > 0,
            rx.card(
                rx.vstack(
                    rx.box(
                        rx.heading("Future Yield & Inventory Outlook", size="4", color=DARK),
                        rx.text(
                            "Shows the estimated position when this crop becomes usable: current inventory + active crops + this plan − demand − inventory expected to expire.",
                            size="2",
                            color=MUTED,
                        ),
                    ),
                    rx.box(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    cultivation_info_header(
                                        "Strain",
                                        "The cultivar selected in this proposed room allocation.",
                                    ),
                                    cultivation_info_header(
                                        "Current\nlb",
                                        "Current on-hand inventory weight for this strain across the latest published inventory snapshot.",
                                    ),
                                    cultivation_info_header(
                                        "Scheduled\nlb",
                                        "Estimated dry yield from rooms already flowering that should become usable before this proposed crop.",
                                    ),
                                    cultivation_info_header(
                                        "Scheduled\nArrival",
                                        "The expected date, or date range, when those scheduled pounds should finish post-harvest processing and become usable inventory.",
                                    ),
                                    cultivation_info_header(
                                        "This Plan\nlb",
                                        "Estimated dry yield created by the bench allocation currently being planned.",
                                    ),
                                    cultivation_info_header(
                                        "Weekly Demand\nlb",
                                        "Recent average weekly unit velocity converted to flower-equivalent pounds using each SKU fill weight.",
                                    ),
                                    cultivation_info_header(
                                        "Expiring\nlb",
                                        "Current inventory expected to reach its spoilage date before this proposed crop becomes usable.",
                                    ),
                                    cultivation_info_header(
                                        "Projected Balance\nlb",
                                        "Current inventory plus scheduled and proposed yield, minus forecast demand and expiring inventory.",
                                    ),
                                    cultivation_info_header(
                                        "Weeks\nSupply",
                                        "Projected balance divided by estimated weekly demand when this crop becomes usable.",
                                    ),
                                    cultivation_info_header(
                                        "Signal",
                                        "Balanced is 0–4 weeks, Warning is more than 4 through 8 weeks, Excess is more than 8 weeks, and Review means demand could not be calculated.",
                                    ),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(
                                    DashboardState.cultivation_future_yield_rows,
                                    cultivation_future_yield_table_row,
                                )
                            ),
                            class_name="qcc-cultivation-outlook-table",
                            width="100%",
                            min_width="1420px",
                            variant="surface",
                        ),
                        width="100%",
                        overflow_x="auto",
                    ),
                    rx.callout(
                        "Planning signal: 0–4 weeks = balanced, more than 4 through 8 weeks = warning, and more than 8 weeks = excess. Results are estimates for clone-allocation decisions, not inventory accounting.",
                        icon="info",
                        color_scheme="blue",
                        width="100%",
                    ),
                    width="100%",
                    spacing="3",
                ),
                width="100%",
            ),
        ),
        rx.card(
            rx.vstack(
                rx.heading("Active & Planned Flower Rooms Feeding the Outlook", size="4", color=DARK),
                rx.text(
                    "Includes active rooms plus saved and on-screen clone plans through F5.10. Completed crops are represented by actual inventory instead.",
                    size="2",
                    color=MUTED,
                ),
                rx.box(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Crop"),
                                rx.table.column_header_cell("Room"),
                                rx.table.column_header_cell("Harvest Date"),
                                rx.table.column_header_cell("Estimated Dry Yield"),
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                DashboardState.cultivation_upcoming_crop_rows,
                                cultivation_upcoming_crop_table_row,
                            )
                        ),
                        width="100%",
                        variant="surface",
                    ),
                    width="100%",
                    overflow_x="auto",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        cultivation_room_layout_print_document(),
        cultivation_clone_dome_print_document(),
        width="100%",
        spacing="4",
    )


def cultivation_registry_field(
    label: str,
    value: rx.Var,
    event: Any,
    *,
    name: str = "",
    input_type: str = "text",
    step: str = "any",
    placeholder: str = "",
) -> rx.Component:
    return rx.box(
        rx.text(label, size="1", weight="bold", color=MUTED),
        rx.input(
            value=value,
            on_change=event,
            type=input_type,
            step=step,
            placeholder=placeholder,
            width="100%",
            **({"name": name} if name else {}),
        ),
        width="100%",
    )


def cultivation_schedule_panel() -> rx.Component:
    """Editable multi-program cultivation calendar with automatic crop selection."""
    return rx.vstack(
        rx.card(
            rx.flex(
                rx.box(
                    rx.heading("Cultivation Schedule", size="5", color=DARK),
                    rx.text(
                        "Generate rotations from a cadence, run independent room programs, and select the crop that drives Clone Allocation.",
                        color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.badge(
                    DashboardState.cultivation_schedule_future_count.to_string()
                    + " future crops",
                    color_scheme="teal",
                    size="2",
                ),
                align="center",
                width="100%",
                gap="3",
                wrap="wrap",
            ),
            border_left=f"5px solid {ACCENT}",
            width="100%",
        ),
        rx.cond(
            DashboardState.cultivation_schedule_future_count < 26,
            rx.callout(
                "The active schedule has fewer than 26 future crops. Generate and save an extension to restore the planning horizon.",
                icon="calendar-plus",
                color_scheme="orange",
                width="100%",
            ),
        ),
        rx.cond(
            DashboardState.cultivation_registry_error != "",
            rx.callout(DashboardState.cultivation_registry_error, icon="triangle-alert", color_scheme="red", width="100%"),
        ),
        rx.cond(
            DashboardState.cultivation_registry_message != "",
            rx.callout(DashboardState.cultivation_registry_message, icon="circle-check", color_scheme="green", width="100%"),
        ),
        rx.card(
            rx.form(
                rx.vstack(
                    rx.heading("Generate schedule", size="4", color=DARK),
                    rx.grid(
                        rx.box(
                            rx.text("Cycle program", size="1", weight="bold", color=MUTED),
                            rx.select(
                                DashboardState.cultivation_program_options,
                                value=DashboardState.cultivation_schedule_program,
                                on_change=DashboardState.set_cultivation_schedule_program,
                                name="program_id",
                                width="100%",
                            ),
                        ),
                        cultivation_registry_field(
                            "Starting crop",
                            DashboardState.cultivation_schedule_start_crop,
                            DashboardState.set_cultivation_schedule_start_crop,
                            name="start_crop",
                            placeholder="F5.10",
                        ),
                        cultivation_registry_field(
                            "First clone cut",
                            DashboardState.cultivation_schedule_first_cut,
                            DashboardState.set_cultivation_schedule_first_cut,
                            name="first_cut",
                            input_type="date",
                        ),
                        cultivation_registry_field(
                            "Crops to generate",
                            DashboardState.cultivation_schedule_count,
                            DashboardState.set_cultivation_schedule_count,
                            name="count",
                            input_type="number",
                            step="1",
                        ),
                        columns=rx.breakpoints(initial="1", md="4"), gap="3", width="100%",
                    ),
                    rx.hstack(
                        rx.button("Preview Schedule", type="submit", variant="outline"),
                        rx.button(
                            "Save Preview",
                            type="button",
                            on_click=DashboardState.save_cultivation_schedule_preview,
                            background=ACCENT,
                            color="white",
                            loading=DashboardState.cultivation_schedule_saving,
                        ),
                        gap="3",
                    ),
                    rx.cond(
                        DashboardState.cultivation_schedule_preview.length() > 0,
                        historical_yield_table(
                            DashboardState.cultivation_schedule_preview_rows,
                            ["Crop", "Room", "Clone Cut Date", "Flower Entry Date", "Harvest Date", "Available Date", "Status"],
                            height="360px",
                        ),
                    ),
                    width="100%", spacing="3",
                ),
                on_submit=DashboardState.preview_cultivation_schedule,
                reset_on_submit=False,
                width="100%",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.heading("Saved Schedule", size="4", color=DARK),
                rx.text(
                    "Every saved preview appears here. Clone Allocation automatically follows the most recent crop whose clone-cut date has arrived.",
                    size="2",
                    color=MUTED,
                ),
                historical_yield_table(
                    DashboardState.cultivation_schedule_rows,
                    ["Crop", "Program", "Room", "Clone Cut", "Flower Entry", "Harvest", "Expected Available", "Status", "Source", "Schedule ID"],
                    height="600px",
                ),
                width="100%", spacing="3",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.heading("Cycle Program Editor", size="4", color=DARK),
                rx.text("Create an independent cycle for an expansion room by giving it its own program ID and room rotation.", size="2", color=MUTED),
                rx.grid(
                    cultivation_registry_field("Program ID", DashboardState.cultivation_schedule_program, DashboardState.set_cultivation_schedule_program, placeholder="expansion-room-6"),
                    cultivation_registry_field("Program name", DashboardState.cultivation_program_name, DashboardState.set_cultivation_program_name),
                    cultivation_registry_field("Crop prefix", DashboardState.cultivation_program_code_prefix, DashboardState.set_cultivation_program_code_prefix),
                    cultivation_registry_field("Cadence days", DashboardState.cultivation_program_cadence_days, DashboardState.set_cultivation_program_cadence_days, input_type="number", step="1"),
                    cultivation_registry_field("Rooting days", DashboardState.cultivation_program_rooting_days, DashboardState.set_cultivation_program_rooting_days, input_type="number", step="1"),
                    cultivation_registry_field("Veg days", DashboardState.cultivation_program_veg_days, DashboardState.set_cultivation_program_veg_days, input_type="number", step="1"),
                    cultivation_registry_field("Flowering days", DashboardState.cultivation_program_flowering_days, DashboardState.set_cultivation_program_flowering_days, input_type="number", step="1"),
                    cultivation_registry_field("Processing days", DashboardState.cultivation_program_processing_days, DashboardState.set_cultivation_program_processing_days, input_type="number", step="1"),
                    cultivation_registry_field("Future crop target", DashboardState.cultivation_program_target_crops, DashboardState.set_cultivation_program_target_crops, input_type="number", step="1"),
                    columns=rx.breakpoints(initial="1", md="3"), gap="3", width="100%",
                ),
                cultivation_registry_field(
                    "Room rotation (comma separated, in order)",
                    DashboardState.cultivation_program_room_rotation,
                    DashboardState.set_cultivation_program_room_rotation,
                    placeholder="Flower Room 6",
                ),
                rx.button("Save Cycle Program", on_click=DashboardState.save_cycle_program_editor, background=ACCENT, color="white"),
                width="100%", spacing="3",
            ),
            width="100%",
        ),
        width="100%", spacing="4", on_mount=DashboardState.load_cultivation_registry,
    )


def cultivation_rooms_benches_panel() -> rx.Component:
    return rx.vstack(
        rx.card(
            rx.vstack(
                rx.heading("Room & Bench Registry", size="5", color=DARK),
                rx.text(
                    "Physical dimensions and lighting are effective-dated master data. Bench canopy is calculated and locked; crop records carry temporary canopy overrides.",
                    color=MUTED,
                ),
                width="100%", spacing="2",
            ),
            border_left=f"5px solid {ACCENT}", width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.heading("Room Registry", size="4", color=DARK),
                rx.hstack(
                    rx.text("Edit existing room", size="1", weight="bold", color=MUTED),
                    rx.select(DashboardState.cultivation_registry_room_id_options, placeholder="Select room ID", on_change=DashboardState.load_cultivation_room_editor, width="260px"),
                    gap="3", align="center",
                ),
                rx.grid(
                    cultivation_registry_field("Room ID", DashboardState.cultivation_room_edit_id, DashboardState.set_cultivation_room_edit_id, placeholder="blank creates from name"),
                    cultivation_registry_field("Room code", DashboardState.cultivation_room_code, DashboardState.set_cultivation_room_code, placeholder="F6"),
                    cultivation_registry_field("Room name", DashboardState.cultivation_room_name, DashboardState.set_cultivation_room_name, placeholder="Flower Room 6"),
                    cultivation_registry_field("Building", DashboardState.cultivation_room_building, DashboardState.set_cultivation_room_building),
                    rx.box(rx.text("Cycle program", size="1", weight="bold", color=MUTED), rx.select(DashboardState.cultivation_program_options, value=DashboardState.cultivation_room_program, on_change=DashboardState.set_cultivation_room_program, width="100%"), width="100%"),
                    cultivation_registry_field("Length (ft)", DashboardState.cultivation_room_length, DashboardState.set_cultivation_room_length, input_type="number"),
                    cultivation_registry_field("Width (ft)", DashboardState.cultivation_room_width, DashboardState.set_cultivation_room_width, input_type="number"),
                    cultivation_registry_field("Height (ft)", DashboardState.cultivation_room_height, DashboardState.set_cultivation_room_height, input_type="number"),
                    rx.box(rx.text("Overhead lighting", size="1", weight="bold", color=MUTED), rx.select(list(OVERHEAD_LIGHTING_TYPES), value=DashboardState.cultivation_room_overhead_type, on_change=DashboardState.set_cultivation_room_overhead_type, width="100%"), width="100%"),
                    cultivation_registry_field("Other lighting", DashboardState.cultivation_room_overhead_other, DashboardState.set_cultivation_room_overhead_other),
                    cultivation_registry_field("Fixture count", DashboardState.cultivation_room_fixture_count, DashboardState.set_cultivation_room_fixture_count, input_type="number", step="1"),
                    cultivation_registry_field("Watts / fixture", DashboardState.cultivation_room_watts_fixture, DashboardState.set_cultivation_room_watts_fixture, input_type="number"),
                    cultivation_registry_field("Total watt override", DashboardState.cultivation_room_watts_override, DashboardState.set_cultivation_room_watts_override, input_type="number"),
                    cultivation_registry_field("Effective date", DashboardState.cultivation_room_effective_date, DashboardState.set_cultivation_room_effective_date, input_type="date"),
                    columns=rx.breakpoints(initial="1", md="2", xl="4"), gap="3", width="100%",
                ),
                cultivation_registry_field("Room notes", DashboardState.cultivation_room_notes, DashboardState.set_cultivation_room_notes),
                rx.button("Save Room", on_click=DashboardState.save_cultivation_room_editor, background=ACCENT, color="white"),
                data_grid(DashboardState.cultivation_room_registry_rows, ["Room ID", "Code", "Room", "Building", "Program", "L × W × H (ft)", "Floor Area", "Volume", "Physical Canopy", "Overhead", "Fixtures", "Total Overhead W", "Effective", "Active"], height="470px", minimum_width=2100, page_size=10),
                width="100%", spacing="3",
            ), width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.heading("Bench Registry", size="4", color=DARK),
                rx.text("Supplemental wattage is calculated from rows × watts per row unless a total override is entered.", size="2", color=MUTED),
                rx.hstack(rx.text("Edit existing bench", size="1", weight="bold", color=MUTED), rx.select(DashboardState.cultivation_registry_bench_id_options, placeholder="Select bench ID", on_change=DashboardState.load_cultivation_bench_editor, width="300px"), gap="3", align="center"),
                rx.grid(
                    cultivation_registry_field("Bench ID", DashboardState.cultivation_bench_edit_id, DashboardState.set_cultivation_bench_edit_id, placeholder="blank creates one"),
                    rx.box(rx.text("Room ID", size="1", weight="bold", color=MUTED), rx.select(DashboardState.cultivation_registry_room_id_options, value=DashboardState.cultivation_bench_room_id, on_change=DashboardState.set_cultivation_bench_room_id, width="100%"), width="100%"),
                    cultivation_registry_field("Bench name", DashboardState.cultivation_bench_name, DashboardState.set_cultivation_bench_name),
                    cultivation_registry_field("Length (ft)", DashboardState.cultivation_bench_length, DashboardState.set_cultivation_bench_length, input_type="number"),
                    cultivation_registry_field("Width (ft)", DashboardState.cultivation_bench_width, DashboardState.set_cultivation_bench_width, input_type="number"),
                    cultivation_registry_field("Plants / sqft", DashboardState.cultivation_bench_density, DashboardState.set_cultivation_bench_density, input_type="number", step="0.05"),
                    rx.box(rx.text("Supplemental lighting", size="1", weight="bold", color=MUTED), rx.select(list(SUPPLEMENTAL_LIGHTING_TYPES), value=DashboardState.cultivation_bench_supplemental_type, on_change=DashboardState.set_cultivation_bench_supplemental_type, width="100%"), width="100%"),
                    cultivation_registry_field("Lighting rows", DashboardState.cultivation_bench_supplemental_rows, DashboardState.set_cultivation_bench_supplemental_rows, input_type="number", step="1"),
                    cultivation_registry_field("Watts / row", DashboardState.cultivation_bench_watts_row, DashboardState.set_cultivation_bench_watts_row, input_type="number"),
                    cultivation_registry_field("Total watt override", DashboardState.cultivation_bench_watts_override, DashboardState.set_cultivation_bench_watts_override, input_type="number"),
                    cultivation_registry_field("Effective date", DashboardState.cultivation_bench_effective_date, DashboardState.set_cultivation_bench_effective_date, input_type="date"),
                    columns=rx.breakpoints(initial="1", md="2", xl="4"), gap="3", width="100%",
                ),
                cultivation_registry_field("Bench notes", DashboardState.cultivation_bench_notes, DashboardState.set_cultivation_bench_notes),
                rx.button("Save Bench", on_click=DashboardState.save_cultivation_bench_editor, background=ACCENT, color="white"),
                data_grid(DashboardState.cultivation_bench_registry_rows, ["Bench ID", "Room", "Bench", "Length", "Width", "Canopy sqft", "Plants/sqft", "Target Plants", "Supplemental", "Rows", "Watts/Row", "Total Supplemental W", "Effective", "Active"], height="620px", minimum_width=2000, page_size=25),
                width="100%", spacing="3",
            ), width="100%",
        ),
        width="100%", spacing="4", on_mount=DashboardState.load_cultivation_registry,
    )


def cultivation_historical_yield_entry_panel() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.heading("Historical Yield Entry", size="4", color=DARK),
            rx.text("Enter room totals or strain-level results. Actual Fresh Frozen canopy overrides the planned plant proportion without changing the physical bench registry.", size="2", color=MUTED),
            rx.cond(
                DashboardState.cultivation_historical_yield_id_options.length() > 0,
                rx.hstack(
                    rx.text("Edit existing yield", size="1", weight="bold", color=MUTED),
                    rx.select(
                        DashboardState.cultivation_historical_yield_id_options,
                        placeholder="Select record ID",
                        on_change=DashboardState.load_historical_yield_editor,
                        width="360px",
                    ),
                    gap="3", align="center",
                ),
            ),
            rx.grid(
                cultivation_registry_field("Record ID (blank creates)", DashboardState.cultivation_yield_edit_id, DashboardState.set_cultivation_yield_edit_id),
                cultivation_registry_field("Crop", DashboardState.cultivation_yield_crop, DashboardState.set_cultivation_yield_crop, placeholder="F5.10"),
                rx.box(rx.text("Room", size="1", weight="bold", color=MUTED), rx.select(DashboardState.cultivation_registry_room_options, value=DashboardState.cultivation_yield_room, on_change=DashboardState.set_cultivation_yield_room, width="100%"), width="100%"),
                cultivation_registry_field("Strain (blank for room total)", DashboardState.cultivation_yield_strain, DashboardState.set_cultivation_yield_strain),
                cultivation_registry_field("Harvest date", DashboardState.cultivation_yield_harvest_date, DashboardState.set_cultivation_yield_harvest_date, input_type="date"),
                cultivation_registry_field("Physical canopy sqft", DashboardState.cultivation_yield_physical_canopy, DashboardState.set_cultivation_yield_physical_canopy, input_type="number"),
                cultivation_registry_field("Planted canopy sqft", DashboardState.cultivation_yield_planted_canopy, DashboardState.set_cultivation_yield_planted_canopy, input_type="number"),
                cultivation_registry_field("Planted plants", DashboardState.cultivation_yield_planted_plants, DashboardState.set_cultivation_yield_planted_plants, input_type="number", step="1"),
                cultivation_registry_field("Planned FF plants", DashboardState.cultivation_yield_planned_ff_plants, DashboardState.set_cultivation_yield_planned_ff_plants, input_type="number", step="1"),
                cultivation_registry_field("Actual FF plants", DashboardState.cultivation_yield_actual_ff_plants, DashboardState.set_cultivation_yield_actual_ff_plants, input_type="number", step="1"),
                cultivation_registry_field("Actual FF canopy sqft", DashboardState.cultivation_yield_actual_ff_canopy, DashboardState.set_cultivation_yield_actual_ff_canopy, input_type="number"),
                cultivation_registry_field("Wet yield lb", DashboardState.cultivation_yield_wet_lbs, DashboardState.set_cultivation_yield_wet_lbs, input_type="number"),
                cultivation_registry_field("Dry flower lb", DashboardState.cultivation_yield_dry_lbs, DashboardState.set_cultivation_yield_dry_lbs, input_type="number"),
                cultivation_registry_field("AB flower lb", DashboardState.cultivation_yield_ab_lbs, DashboardState.set_cultivation_yield_ab_lbs, input_type="number"),
                cultivation_registry_field("C flower lb", DashboardState.cultivation_yield_c_lbs, DashboardState.set_cultivation_yield_c_lbs, input_type="number"),
                cultivation_registry_field("Trim lb", DashboardState.cultivation_yield_trim_lbs, DashboardState.set_cultivation_yield_trim_lbs, input_type="number"),
                cultivation_registry_field("Quality score", DashboardState.cultivation_yield_quality, DashboardState.set_cultivation_yield_quality, input_type="number"),
                columns=rx.breakpoints(initial="1", md="3", xl="5"), gap="3", width="100%",
            ),
            cultivation_registry_field("Notes", DashboardState.cultivation_yield_notes, DashboardState.set_cultivation_yield_notes),
            rx.button("Save Historical Yield", on_click=DashboardState.save_historical_yield_editor, background=ACCENT, color="white"),
            rx.cond(
                DashboardState.cultivation_historical_entry_rows.length() > 0,
                data_grid(DashboardState.cultivation_historical_entry_rows, ["Record ID", "Crop", "Room", "Strain", "Harvest Date", "Planted Canopy", "Fresh Frozen Plants", "Fresh Frozen Canopy", "Net Dry Canopy", "Dry Flower (lb)", "Yield (g/sqft)", "Source"], height="440px", minimum_width=1900, page_size=10),
            ),
            width="100%", spacing="3",
        ),
        width="100%",
    )


def cultivation_foundation_panel() -> rx.Component:
    return rx.grid(
        rx.card(
            rx.vstack(
                rx.heading("Flower Room Schedule", size="4", color=DARK),
                rx.text(
                    "The cultivation calendar and five-room cycle will populate upcoming flower entry and harvest dates here.",
                    color=MUTED,
                ),
                rx.badge("Calendar source reviewed", color_scheme="green"),
                align="start",
                spacing="3",
            )
        ),
        rx.card(
            rx.vstack(
                rx.heading("Veg & Mother Inventory", size="4", color=DARK),
                rx.text(
                    "Mapped Veg benches, Mother racks, strain counts, transplant dates, and clone availability will connect to each allocation.",
                    color=MUTED,
                ),
                rx.badge("Veg and Mother sources reviewed", color_scheme="green"),
                align="start",
                spacing="3",
            )
        ),
        rx.card(
            rx.vstack(
                rx.heading("Projected Yield", size="4", color=DARK),
                rx.text(
                    "Historical room, strain, plant-count, wet-weight, and finished dry-trim results will drive the projected harvest output.",
                    color=MUTED,
                ),
                rx.badge("Historical yield sources reviewed", color_scheme="green"),
                align="start",
                spacing="3",
            )
        ),
        columns=rx.breakpoints(initial="1", lg="3"),
        gap="4",
        width="100%",
    )


def cultivation_demand_availability_panel() -> rx.Component:
    """Shadow availability model for validating seasonality and stock gaps."""
    return rx.vstack(
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.heading("Availability-Adjusted Demand", size="5", color=DARK),
                        rx.text(
                            "Compare current SKU Planning velocity with a shipment-based estimate that removes likely constrained weeks after each SKU launches.",
                            color=MUTED,
                            size="2",
                        ),
                    ),
                    rx.spacer(),
                    rx.badge("SHADOW MODEL — SKU PLANNING UNCHANGED", color_scheme="purple", size="2"),
                    width="100%",
                    align="center",
                    gap="3",
                    direction=rx.breakpoints(initial="column", md="row"),
                ),
                rx.callout(
                    "A week with shipments is treated as available. A post-launch zero-shipment week before later shipments is a Likely OOS proxy; a trailing zero week is marked Recent gap — review. These are hypotheses for comparison with crop history, not confirmed inventory facts.",
                    icon="flask-conical",
                    color_scheme="purple",
                    width="100%",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
            border_left="5px solid #7c3aed",
        ),
        rx.grid(
            snapshot_stat_card("Flower Strains", DashboardState.cultivation_demand_strain_count, "#0f766e"),
            snapshot_stat_card("Strain / Size Series", DashboardState.cultivation_demand_sku_count, "#2563eb"),
            snapshot_stat_card("Likely Constrained Weeks", DashboardState.cultivation_demand_constraint_count, "#7c3aed"),
            columns=rx.breakpoints(initial="1", sm="3"),
            gap="3",
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.text("Brand", size="1", weight="bold", color=MUTED),
                        rx.select(
                            DashboardState.cultivation_demand_brand_options,
                            value=DashboardState.cultivation_demand_brand_filter,
                            on_change=DashboardState.change_cultivation_demand_brand_filter,
                            width="200px",
                        ),
                    ),
                    rx.box(
                        rx.text("Strain", size="1", weight="bold", color=MUTED),
                        rx.select(
                            DashboardState.cultivation_demand_strain_options,
                            value=DashboardState.cultivation_demand_strain_filter,
                            on_change=DashboardState.change_cultivation_demand_strain_filter,
                            width="230px",
                        ),
                    ),
                    rx.box(
                        rx.text("SKU type", size="1", weight="bold", color=MUTED),
                        rx.select(
                            [
                                "All Compared SKUs",
                                "1g Flower",
                                "3.5g Flower",
                                "7g Flower",
                                "1g Pre-Roll",
                                "3.5g Pre-Rolls",
                            ],
                            value=DashboardState.cultivation_demand_sku_filter,
                            on_change=DashboardState.change_cultivation_demand_sku_filter,
                            width="210px",
                        ),
                    ),
                    gap="4",
                    align="end",
                    wrap="wrap",
                    width="100%",
                ),
                rx.heading("All-Strain Comparison", size="4", color=DARK),
                rx.vstack(
                    data_grid(
                        DashboardState.cultivation_demand_summary_rows,
                        [
                            "Brand", "Strain", "SKU\nType", "First Ship\nWeek",
                            "Last Ship\nWeek", "Calendar\nWeeks", "Shipping\nWeeks",
                            "Likely\nConstrained\nWeeks", "Recent Gap\nWeeks",
                            "Current\nVelocity", "Availability-\nAdjusted\nVelocity",
                            "Adjustment", "Signal",
                        ],
                        height="520px",
                        show_search=True,
                        class_name="qcc-cultivation-demand-grid",
                        column_width=165,
                        minimum_width=2200,
                        page_size=DashboardState.cultivation_demand_page_size,
                    ),
                    rx.flex(
                        rx.text("Rows", size="1", weight="bold", color=MUTED),
                        rx.select(
                            ["10", "25", "50", "100"],
                            value=DashboardState.cultivation_demand_rows_per_page,
                            on_change=DashboardState.change_cultivation_demand_rows_per_page,
                            width="82px",
                            size="1",
                        ),
                        align="center",
                        gap="2",
                    ),
                    align="start",
                    spacing="2",
                    width="100%",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        rx.card(
            rx.vstack(
                rx.heading("Weekly Evidence", size="4", color=DARK),
                rx.cond(
                    DashboardState.cultivation_demand_strain_filter == "All Strains",
                    rx.callout(
                        "Choose one strain above to inspect every weekly shipment and availability signal for its compared flower and pre-roll SKUs.",
                        icon="mouse-pointer-click",
                        color_scheme="blue",
                        width="100%",
                    ),
                    data_grid(
                        DashboardState.cultivation_demand_weekly_rows,
                        [
                            "Week\nStarting",
                            "SKU\nType",
                            "Units\nShipped",
                            "Availability\nSignal",
                        ],
                        height="440px",
                        show_search=False,
                        class_name="qcc-cultivation-demand-weekly-grid",
                        column_width=210,
                        minimum_width=900,
                        page_size=DashboardState.cultivation_demand_page_size,
                    ),
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        width="100%",
        spacing="4",
    )


def cultivation_historical_yield_panel() -> rx.Component:
    """Historical room, cycle, and strain performance from the yield workbook."""
    return rx.vstack(
        cultivation_historical_yield_entry_panel(),
        rx.card(
            rx.flex(
                rx.box(
                    rx.heading("Historical Yield Performance", size="5", color=DARK),
                    rx.text(
                        "Actual crop-report results are preserved as the cultivation source of truth. Use these benchmarks to compare rooms, strains, and operating cycles before committing a clone allocation.",
                        color=MUTED,
                        size="2",
                    ),
                ),
                rx.spacer(),
                rx.box(
                    rx.text("Room filter", size="1", weight="bold", color=MUTED),
                    rx.select(
                        [
                            "All Flower Rooms",
                            "Flower Room 1",
                            "Flower Room 2",
                            "Flower Room 3",
                            "Flower Room 4",
                            "Flower Room 5",
                        ],
                        value=DashboardState.cultivation_history_room_filter,
                        on_change=DashboardState.change_cultivation_history_room_filter,
                        width="210px",
                    ),
                ),
                width="100%",
                align="end",
                gap="4",
                direction=rx.breakpoints(initial="column", md="row"),
            ),
            width="100%",
            border_left=f"5px solid {ACCENT}",
        ),
        rx.grid(
            snapshot_stat_card(
                "Completed Harvests",
                DashboardState.cultivation_history_harvest_count,
                "#0f766e",
            ),
            snapshot_stat_card(
                "Finished Flower",
                DashboardState.cultivation_history_total_finished,
                "#2563eb",
            ),
            snapshot_stat_card(
                "Average / Harvest",
                DashboardState.cultivation_history_average_finished,
                "#7c3aed",
            ),
            snapshot_stat_card(
                "Weighted Yield",
                DashboardState.cultivation_history_weighted_yield,
                "#ea580c",
            ),
            snapshot_stat_card(
                "Wet Conversion",
                DashboardState.cultivation_history_average_conversion,
                "#0891b2",
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="5"),
            gap="3",
            width="100%",
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Rooms & Harvests", value="rooms"),
                rx.tabs.trigger("Strain Benchmarks", value="strains"),
                class_name="qcc-tabs",
            ),
            rx.tabs.content(
                rx.vstack(
                    rx.card(
                        rx.vstack(
                            rx.heading("Historical Data Coverage", size="4", color=DARK),
                            rx.text(
                                "The workbook contributes room harvests, operating-cycle history, and strain-level crop observations. Values are bundled with this cultivation build for consistent local and Render results.",
                                color=MUTED,
                                size="2",
                            ),
                            rx.callout(
                                f"{HISTORICAL_STRAIN_OBSERVATIONS:,} crop/strain observations support the strain benchmarks.",
                                icon="database",
                                color_scheme="teal",
                                width="100%",
                            ),
                            rx.callout(
                                "The room filter changes the KPIs and applies to Individual Harvests and Combine Rooms. Combine Cycles always shows complete cycles across all rooms.",
                                icon="info",
                                color_scheme="blue",
                                width="100%",
                            ),
                            width="100%",
                            spacing="4",
                        ),
                        width="100%",
                    ),
                    rx.card(
                        rx.vstack(
                            rx.flex(
                                rx.box(
                                    rx.heading(
                                        "Historical Room & Harvest Data",
                                        size="4",
                                        color=DARK,
                                    ),
                                    rx.text(
                                        "Select individual harvests, room totals, or operating-cycle totals. Every table is sortable by column.",
                                        size="2",
                                        color=MUTED,
                                    ),
                                ),
                                rx.spacer(),
                                rx.box(
                                    rx.text(
                                        "Table view",
                                        size="1",
                                        weight="bold",
                                        color=MUTED,
                                    ),
                                    rx.select(
                                        [
                                            "Individual Harvests",
                                            "Combine Rooms",
                                            "Combine Cycles",
                                        ],
                                        value=DashboardState.cultivation_history_table_view,
                                        on_change=DashboardState.change_cultivation_history_table_view,
                                        width="210px",
                                    ),
                                ),
                                width="100%",
                                align="end",
                                gap="4",
                                direction=rx.breakpoints(
                                    initial="column", md="row"
                                ),
                            ),
                            rx.cond(
                                DashboardState.cultivation_history_table_view
                                == "Individual Harvests",
                                rx.callout(
                                    "Fresh Frozen = Yes when the crop report's flower-time field contains (FF). Fresh frozen removal can reduce the canopy represented in the dry-flower result.",
                                    icon="snowflake",
                                    color_scheme="blue",
                                    width="100%",
                                ),
                            ),
                            rx.cond(
                                DashboardState.cultivation_history_table_view
                                == "Combine Rooms",
                                cultivation_history_data_grid(
                                    DashboardState.cultivation_history_room_table_data,
                                    HISTORICAL_ROOM_COLUMNS,
                                    height="470px",
                                    class_name="qcc-historical-room-grid",
                                    column_width=132,
                                    minimum_width=1320,
                                ),
                                rx.cond(
                                    DashboardState.cultivation_history_table_view
                                    == "Combine Cycles",
                                    cultivation_history_data_grid(
                                        DashboardState.cultivation_history_cycle_table_data,
                                        HISTORICAL_CYCLE_COLUMNS,
                                        height="470px",
                                        class_name="qcc-historical-cycle-grid",
                                        column_width=132,
                                        minimum_width=1056,
                                    ),
                                    cultivation_history_data_grid(
                                        DashboardState.cultivation_history_harvest_table_data,
                                        HISTORICAL_HARVEST_COLUMNS,
                                        height="540px",
                                        class_name="qcc-historical-harvest-grid",
                                        column_width=140,
                                        minimum_width=980,
                                    ),
                                ),
                            ),
                            width="100%",
                            spacing="4",
                        ),
                        width="100%",
                    ),
                    width="100%",
                    spacing="4",
                ),
                value="rooms",
                padding_top="1rem",
            ),
            rx.tabs.content(
                rx.vstack(
                    rx.flex(
                        rx.box(
                            rx.text(
                                "Strain filter",
                                size="1",
                                weight="bold",
                                color=MUTED,
                            ),
                            rx.select(
                                DashboardState.cultivation_history_strain_options,
                                value=DashboardState.cultivation_history_strain_filter,
                                on_change=DashboardState.change_cultivation_history_strain_filter,
                                width="240px",
                            ),
                        ),
                        gap="3",
                        align="end",
                        width="100%",
                    ),
                    rx.card(
                        rx.vstack(
                            rx.heading("Top Historical Strain Yields", size="4", color=DARK),
                            rx.text(
                                "Finished AB + C flower yield per square foot. Observation counts are shown in the table below and should guide confidence in each benchmark.",
                                size="2",
                                color=MUTED,
                            ),
                            rx.recharts.bar_chart(
                                rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
                                rx.recharts.x_axis(
                                    data_key="Strain",
                                    angle=-22,
                                    text_anchor="end",
                                    height=92,
                                ),
                                rx.recharts.y_axis(),
                                rx.recharts.graphing_tooltip(),
                                rx.recharts.bar(
                                    data_key="Flower Yield",
                                    fill="#7c3aed",
                                    radius=[5, 5, 0, 0],
                                ),
                                data=DashboardState.cultivation_history_strain_chart_rows,
                                width="100%",
                                height=390,
                            ),
                            width="100%",
                            spacing="3",
                        ),
                        width="100%",
                    ),
                    cultivation_history_data_grid(
                        DashboardState.cultivation_history_strain_table_data,
                        HISTORICAL_STRAIN_COLUMNS,
                        height="590px",
                        class_name="qcc-historical-strain-grid",
                        column_width=135,
                        minimum_width=1485,
                    ),
                    width="100%",
                    spacing="4",
                ),
                value="strains",
                padding_top="1rem",
            ),
            default_value="rooms",
            width="100%",
        ),
        width="100%",
        spacing="4",
    )


PLANT_ACTIVE_COLUMNS = [
    "Tag", "Strain", "Phase", "Facility", "Location", "Plant Batch",
    "Batch Date", "Phase Date", "Harvest Date",
]
PLANT_LOCATION_COLUMNS = ["Facility", "Phase", "Location", "Plants", "Strains"]
PLANTING_COLUMNS = [
    "Plant Batch", "Strain", "Facility", "Location", "Available Plants",
    "Tracked", "Packaged", "Destroyed", "Batch Date",
]
PLANT_HARVEST_COLUMNS = [
    "Harvest Batch", "Strain", "Harvest Date", "Plants", "Wet Weight (lb)",
    "Packaged Weight (lb)", "Remaining Weight (lb)", "Packages", "Fresh Frozen",
]
PLANT_RECONCILIATION_COLUMNS = [
    "Crop", "Room", "Strain", "Harvest Date", "Crop Report Plants",
    "Metrc Harvest Plants", "Variance", "Status",
]


def cultivation_metrc_plant_panel() -> rx.Component:
    """Current Metrc plant operations with crop-report reconciliation."""
    return rx.vstack(
        rx.card(
            rx.vstack(
                rx.flex(
                    rx.box(
                        rx.hstack(
                            rx.heading("Metrc Plant Data", size="5", color=DARK),
                            rx.badge(
                                "CURRENT SNAPSHOT",
                                color_scheme="teal",
                                variant="soft",
                            ),
                            gap="2",
                            align="center",
                        ),
                        rx.text(
                            "Operational plant positions from the four active Metrc exports. Crop reports remain the cultivation source of truth and are reconciled below.",
                            color=MUTED,
                            size="2",
                        ),
                    ),
                    rx.spacer(),
                    rx.vstack(
                        rx.text("Snapshot", size="1", weight="bold", color=MUTED),
                        rx.text(
                            DashboardState.cultivation_plant_snapshot_label,
                            weight="bold",
                            color=DARK,
                        ),
                        spacing="0",
                        align="end",
                    ),
                    width="100%",
                    align="center",
                    wrap="wrap",
                    gap="3",
                ),
                rx.callout(
                    "Upload Flowering, Vegetative, Plantings—Active, and Harvests together. Each upload replaces the operational view while preserving the previous snapshot in history.",
                    icon="info",
                    color_scheme="blue",
                    width="100%",
                ),
                width="100%",
                spacing="3",
            ),
            width="100%",
        ),
        rx.accordion.root(
            rx.accordion.item(
                value="plant-import",
                header=rx.flex(
                    rx.box(
                        rx.text("Import Four Active Metrc Plant Exports", weight="bold"),
                        rx.text(
                            "The workbook type is validated by its headers and filename before publishing.",
                            size="1",
                            color=MUTED,
                        ),
                    ),
                    rx.spacer(),
                    rx.icon("upload", color=ACCENT),
                    width="100%",
                    align="center",
                ),
                content=rx.vstack(
                    rx.upload(
                        rx.vstack(
                            rx.icon("files", size=30, color=ACCENT),
                            rx.text(
                                "Drop the four Metrc .xlsx files here or click to select",
                                weight="bold",
                            ),
                            rx.text(
                                "Harvests · Flowering · Vegetative · Plantings—Active",
                                size="1",
                                color=MUTED,
                            ),
                            rx.button("Choose Four Files", variant="outline"),
                            spacing="2",
                            align="center",
                        ),
                        id="cultivation_plant_upload",
                        accept={
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"]
                        },
                        multiple=True,
                        max_files=4,
                        border=f"2px dashed {ACCENT}",
                        border_radius="12px",
                        padding="2rem",
                        width="100%",
                    ),
                    rx.flex(
                        rx.foreach(
                            rx.selected_files("cultivation_plant_upload"), rx.badge
                        ),
                        gap="2",
                        wrap="wrap",
                        width="100%",
                    ),
                    rx.hstack(
                        rx.button(
                            "Import Plant Snapshot",
                            on_click=DashboardState.import_cultivation_plant_files(
                                rx.upload_files(upload_id="cultivation_plant_upload")
                            ),
                            loading=DashboardState.cultivation_plant_importing,
                            background=ACCENT,
                            color="white",
                        ),
                        rx.button(
                            "Clear Selection",
                            on_click=rx.clear_selected_files("cultivation_plant_upload"),
                            variant="outline",
                        ),
                        gap="3",
                    ),
                    rx.cond(
                        DashboardState.cultivation_plant_source_rows.length() > 0,
                        readable_grid(
                            DashboardState.cultivation_plant_source_rows,
                            ["Export", "Filename"],
                            "250px",
                        ),
                    ),
                    width="100%",
                    spacing="3",
                ),
            ),
            type="single",
            collapsible=True,
            width="100%",
            variant="soft",
        ),
        rx.cond(
            DashboardState.cultivation_plant_error != "",
            rx.callout(
                DashboardState.cultivation_plant_error,
                icon="triangle_alert",
                color_scheme="red",
                width="100%",
            ),
        ),
        rx.cond(
            DashboardState.cultivation_plant_message != "",
            rx.callout(
                DashboardState.cultivation_plant_message,
                icon="circle_check",
                color_scheme="green",
                width="100%",
            ),
        ),
        rx.grid(
            metric_card(
                "Flowering Plants",
                DashboardState.cultivation_plant_kpis["flowering"],
                "Individually tagged in Metrc flowering",
            ),
            metric_card(
                "Veg Plants",
                DashboardState.cultivation_plant_kpis["vegetative"],
                "All individually tagged vegetative plants",
            ),
            metric_card(
                "Clones",
                DashboardState.cultivation_plant_kpis["clones"],
                "Available clones in Propagation",
            ),
            metric_card(
                "Moms",
                DashboardState.cultivation_plant_kpis["mothers"],
                "Tagged plants in Mom locations",
            ),
            metric_card(
                "Strains",
                DashboardState.cultivation_plant_kpis["strains"],
                "Distinct strains matching the filters",
            ),
            metric_card(
                "Unfinished Harvest Batches",
                DashboardState.cultivation_plant_kpis["unfinished_harvests"],
                "Harvest batches with remaining weight",
            ),
            columns=rx.breakpoints(initial="1", sm="2", xl="3"),
            gap="3",
            width="100%",
        ),
        rx.card(
            rx.flex(
                rx.box(
                    rx.text("Facility", size="1", weight="bold", color=MUTED),
                    rx.select(
                        DashboardState.cultivation_plant_facility_options,
                        value=DashboardState.cultivation_plant_facility_filter,
                        on_change=DashboardState.change_cultivation_plant_facility_filter,
                        width="210px",
                    ),
                ),
                rx.box(
                    rx.text("Phase", size="1", weight="bold", color=MUTED),
                    rx.select(
                        ["All Phases", "Flowering", "Vegetative"],
                        value=DashboardState.cultivation_plant_phase_filter,
                        on_change=DashboardState.change_cultivation_plant_phase_filter,
                        width="180px",
                    ),
                ),
                rx.box(
                    rx.text("Location", size="1", weight="bold", color=MUTED),
                    rx.select(
                        DashboardState.cultivation_plant_location_options,
                        value=DashboardState.cultivation_plant_location_filter,
                        on_change=DashboardState.change_cultivation_plant_location_filter,
                        width="280px",
                    ),
                ),
                rx.box(
                    rx.text("Strain", size="1", weight="bold", color=MUTED),
                    rx.select(
                        DashboardState.cultivation_plant_strain_options,
                        value=DashboardState.cultivation_plant_strain_filter,
                        on_change=DashboardState.change_cultivation_plant_strain_filter,
                        width="240px",
                    ),
                ),
                rx.spacer(),
                rx.badge(
                    DashboardState.cultivation_active_plant_count,
                    color_scheme="teal",
                    size="2",
                ),
                width="100%",
                align="end",
                gap="3",
                wrap="wrap",
            ),
            width="100%",
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Active Plants", value="active_plants"),
                rx.tabs.trigger("Location Summary", value="locations"),
                rx.tabs.trigger("Propagation", value="plantings"),
                rx.tabs.trigger("Harvests", value="harvests"),
                rx.tabs.trigger("Crop Reconciliation", value="reconciliation"),
                class_name="qcc-tabs",
            ),
            rx.tabs.content(
                cultivation_plant_data_grid(
                    DashboardState.cultivation_active_plant_table_rows,
                    PLANT_ACTIVE_COLUMNS,
                    "650px",
                    minimum_width=1750,
                ),
                value="active_plants",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_plant_data_grid(
                    DashboardState.cultivation_plant_location_summary_rows,
                    PLANT_LOCATION_COLUMNS,
                    "520px",
                    minimum_width=900,
                ),
                value="locations",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_plant_data_grid(
                    DashboardState.cultivation_active_planting_rows,
                    PLANTING_COLUMNS,
                    "600px",
                    minimum_width=1500,
                ),
                value="plantings",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_plant_data_grid(
                    DashboardState.cultivation_plant_harvest_rows,
                    PLANT_HARVEST_COLUMNS,
                    "650px",
                    minimum_width=1600,
                ),
                value="harvests",
                padding_top="1rem",
            ),
            rx.tabs.content(
                rx.vstack(
                    rx.callout(
                        "Crop-report canopy at 0.75 plants per square foot is compared with the actual plant count recorded when Metrc harvest batches appear. A variance greater than two plants is flagged for review.",
                        icon="scale",
                        color_scheme="purple",
                        width="100%",
                    ),
                    cultivation_plant_data_grid(
                        DashboardState.cultivation_plant_reconciliation_rows,
                        PLANT_RECONCILIATION_COLUMNS,
                        "600px",
                        minimum_width=1400,
                    ),
                    width="100%",
                    spacing="3",
                ),
                value="reconciliation",
                padding_top="1rem",
            ),
            value=DashboardState.cultivation_plant_view,
            on_change=DashboardState.change_cultivation_plant_view,
            width="100%",
        ),
        width="100%",
        spacing="4",
    )


def cultivation_panel() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.heading("Cultivation Planning", size="6", color=DARK),
            rx.text(
                "Translate the flower schedule into bench populations, clone cuts, Veg movements, and projected yield.",
                color=MUTED,
            ),
        ),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Clone Allocation", value="clone_planning"),
                rx.tabs.trigger("Room Layout & Clone Plan", value="clone_allocation"),
                rx.tabs.trigger("Cultivation Schedule", value="schedule"),
                rx.tabs.trigger("Rooms & Benches", value="rooms_benches"),
                rx.tabs.trigger("Historical Yield", value="historical_yield"),
                rx.tabs.trigger("Demand & Availability", value="demand_availability"),
                rx.tabs.trigger("Metrc Plant Data", value="metrc_plants"),
                rx.tabs.trigger("Planning Foundation", value="foundation"),
                class_name="qcc-tabs",
            ),
            rx.tabs.content(
                cultivation_clone_planning_panel(),
                value="clone_planning",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_clone_allocation_panel(),
                value="clone_allocation",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_schedule_panel(),
                value="schedule",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_rooms_benches_panel(),
                value="rooms_benches",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_historical_yield_panel(),
                value="historical_yield",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_demand_availability_panel(),
                value="demand_availability",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_metrc_plant_panel(),
                value="metrc_plants",
                padding_top="1rem",
            ),
            rx.tabs.content(
                cultivation_foundation_panel(),
                value="foundation",
                padding_top="1rem",
            ),
            value=DashboardState.cultivation_view,
            on_change=DashboardState.change_cultivation_view,
            width="100%",
        ),
        width="100%",
        spacing="4",
    )


def protected_dashboard() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.flex(
                rx.box(
                    rx.heading("QCC Control Tower", size="8", color=DARK),
                    rx.text(
                        f"Reflex Inventory, Production & QA · Version {PILOT_VERSION}",
                        color=MUTED,
                    ),
                ),
                rx.spacer(),
                rx.vstack(
                    rx.cond(
                        DashboardState.using_demo_data,
                        rx.badge("DEMO DATA", color_scheme="orange", size="3"),
                        rx.badge("SHARED SUPABASE", color_scheme="green", size="3"),
                    ),
                    rx.text("Loaded " + DashboardState.loaded_at, size="1", color=MUTED),
                    rx.text(
                        DashboardState.auth_name + " · " + DashboardState.auth_role,
                        size="1",
                        color=MUTED,
                    ),
                    rx.cond(
                        DashboardState.is_administrator,
                        rx.button(
                            rx.icon("settings", size=14),
                            "Administration",
                            on_click=DashboardState.change_workspace_view(
                                "administration"
                            ),
                            variant="outline",
                            color_scheme="teal",
                            size="1",
                        ),
                    ),
                    rx.button(
                        "Sign Out",
                        on_click=DashboardState.sign_out,
                        variant="outline",
                        color_scheme="teal",
                        size="1",
                    ),
                    align="end",
                    spacing="1",
                ),
                align="center",
                width="100%",
                class_name="qcc-brand-header",
            ),
            rx.cond(
                DashboardState.error_message != "",
                rx.callout(
                    DashboardState.error_message,
                    icon="triangle_alert",
                    color_scheme="orange",
                    width="100%",
                ),
            ),
            rx.vstack(
                rx.grid(
                    snapshot_stat_card("Inventory Snapshot", DashboardState.snapshot_date, "#0f766e"),
                    snapshot_stat_card("Packages", DashboardState.snapshot_packages, "#2563eb"),
                    snapshot_stat_card("SKUs", DashboardState.snapshot_skus, "#7c3aed"),
                    snapshot_stat_card("Package Detail", DashboardState.snapshot_detail, "#0369a1"),
                    snapshot_stat_card("CPG Eligible", DashboardState.snapshot_cpg_eligible, "#16a34a"),
                    columns=rx.breakpoints(initial="1", sm="2", lg="5"),
                    gap="4", width="100%",
                ),
                rx.text(DashboardState.rule_version, size="1", color=MUTED),
                width="100%", spacing="2",
            ),
            filters(),
            rx.tabs.root(
                rx.tabs.list(
                    rx.tabs.trigger("Executive Dashboard", value="executive"),
                    rx.tabs.trigger("Sales & Demand Planning", value="sales_demand"),
                    rx.tabs.trigger("Cultivation", value="cultivation"),
                    rx.tabs.trigger("Manufacturing", value="manufacturing"),
                    rx.tabs.trigger("Cannabis Inventory", value="inventory"),
                    rx.tabs.trigger("Materials & Procurement", value="materials"),
                    rx.tabs.trigger("Quality & Compliance", value="quality"),
                    rx.tabs.trigger(
                        "Distribution & Customer Service", value="distribution"
                    ),
                    class_name="qcc-tabs qcc-tabs-primary",
                    width="100%",
                ),
                rx.tabs.content(
                    executive_dashboard_panel(),
                    value="executive",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    sales_demand_workspace(),
                    value="sales_demand",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    cultivation_panel(),
                    value="cultivation",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    manufacturing_panel(),
                    value="manufacturing",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    inventory_panel(),
                    value="inventory",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    materials_procurement_panel(),
                    value="materials",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    quality_compliance_panel(),
                    value="quality",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    distribution_customer_service_panel(),
                    value="distribution",
                    padding_top="1.25rem",
                ),
                rx.tabs.content(
                    rx.cond(
                        DashboardState.is_administrator,
                        administration_panel(),
                        rx.callout(
                            "Administrator access is required.",
                            icon="shield_alert",
                            color_scheme="red",
                        ),
                    ),
                    value="administration",
                    padding_top="1.25rem",
                ),
                value=DashboardState.workspace_view,
                on_change=DashboardState.change_workspace_view,
                width="100%",
            ),
            width="100%",
            max_width="1800px",
            margin="0 auto",
            padding="2rem",
            spacing="5",
            class_name="qcc-app-content",
        ),
        min_height="100vh",
        background=BACKGROUND,
        class_name="qcc-clade9-app",
    )


def login_page() -> rx.Component:
    """Private entry point for Google Workspace and Microsoft 365 users."""
    return rx.center(
        rx.vstack(
            rx.box(
                rx.heading("QCC Control Tower", size="8", color=DARK),
                rx.text(
                    "Secure employee access · Inventory and Production Planning",
                    color=MUTED,
                    text_align="center",
                ),
                width="100%",
                text_align="center",
            ),
            rx.cond(
                DashboardState.auth_message != "",
                rx.callout(
                    DashboardState.auth_message,
                    icon="info",
                    color_scheme="orange",
                    width="100%",
                ),
            ),
            rx.cond(
                DashboardState.auth_configured,
                rx.vstack(
                    rx.button(
                        rx.icon("mail", size=20),
                        "Continue with Google Workspace",
                        on_click=DashboardState.begin_google_sign_in,
                        width="100%",
                        size="3",
                        loading=DashboardState.auth_redirecting,
                        disabled=DashboardState.auth_redirecting,
                        class_name="qcc-login-button",
                    ),
                    rx.button(
                        rx.icon("building_2", size=20),
                        "Continue with Microsoft 365",
                        on_click=DashboardState.begin_microsoft_sign_in,
                        width="100%",
                        size="3",
                        variant="outline",
                        loading=DashboardState.auth_redirecting,
                        disabled=DashboardState.auth_redirecting,
                        class_name="qcc-login-button",
                    ),
                    width="100%",
                    spacing="3",
                ),
                rx.callout(
                    "Authentication configuration is required before this app can be used.",
                    icon="shield_alert",
                    color_scheme="red",
                    width="100%",
                ),
            ),
            rx.separator(width="100%"),
            rx.text(
                "Only active employees listed in QCC Team & Access can enter. "
                "Signing into Google or Microsoft does not grant access by itself.",
                size="2",
                color=MUTED,
                text_align="center",
            ),
            rx.text(f"Version {PILOT_VERSION}", size="1", color=MUTED),
            width="100%",
            max_width="520px",
            spacing="5",
            padding="2.5rem",
            class_name="qcc-login-card",
        ),
        min_height="100vh",
        padding="1.5rem",
        class_name="qcc-clade9-app qcc-login-page",
    )


def dashboard() -> rx.Component:
    return rx.cond(
        DashboardState.auth_checked,
        rx.cond(
            DashboardState.authenticated,
            protected_dashboard(),
            login_page(),
        ),
        rx.center(
            rx.vstack(
                rx.spinner(size="3"),
                rx.heading("Connecting securely", size="5"),
                rx.text(
                    rx.cond(
                        DashboardState.auth_message != "",
                        DashboardState.auth_message,
                        "Checking your QCC employee session...",
                    ),
                    color=MUTED,
                    text_align="center",
                ),
                align="center",
                spacing="4",
                padding="2.5rem",
                class_name="qcc-login-card",
            ),
            min_height="100vh",
            padding="1.5rem",
            class_name="qcc-clade9-app qcc-login-page",
        ),
    )


def auth_callback_page() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.spinner(size="3"),
            rx.heading("Completing secure sign-in", size="5"),
            rx.text("Verifying your identity and QCC employee access...", color=MUTED),
            rx.cond(
                DashboardState.auth_message != "",
                rx.cond(
                    DashboardState.auth_failed,
                    rx.callout(
                        DashboardState.auth_message,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                    rx.callout(
                        DashboardState.auth_message,
                        icon="info",
                        color_scheme="blue",
                        width="100%",
                    ),
                ),
            ),
            rx.button("Return to Sign In", on_click=rx.redirect("/"), variant="soft"),
            align="center",
            spacing="4",
            padding="2.5rem",
            class_name="qcc-login-card",
        ),
        min_height="100vh",
        padding="1.5rem",
        class_name="qcc-clade9-app qcc-login-page",
        on_mount=rx.call_script(
            "window.location.hash",
            callback=DashboardState.complete_oauth_callback,
        ),
    )


app = rx.App(
    theme=rx.theme(
        appearance="light",
        accent_color="teal",
        radius="medium",
    ),
    stylesheets=["/qcc.css"],
)
app.add_page(
    dashboard,
    route="/",
    title="QCC Control Tower - Reflex Inventory, Production & QA",
    on_load=DashboardState.load_dashboard,
)
app.add_page(
    auth_callback_page,
    route="/auth/callback",
    title="QCC Control Tower - Secure Sign In",
)
app.add_page(
    buyer_menu_page,
    route="/menu",
    title="QCC New Jersey Wholesale Menu",
    on_load=BuyerMenuState.load_public_menu,
)
