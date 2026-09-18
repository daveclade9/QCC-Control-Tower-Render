"""Reflex views for supply inventory, formal counts, purchasing, and labels."""

from __future__ import annotations

from datetime import date
from typing import Any

import reflex as rx

from . import procurement as service


class ProcurementState(rx.State):
    message: str = ""
    error: str = ""
    supply_search: str = ""
    supply_category: str = "ALL CATEGORIES"
    supply_rows: list[dict[str, str]] = []
    supply_adjustment: dict[str, str] = {"item_id": "", "quantity": "", "notes": ""}

    count_domain: str = "PACKAGING"
    count_scope: str = ""
    count_selected_domain: str = ""
    count_notes: str = ""
    count_session_id: str = ""
    count_acknowledged: bool = False
    count_entry: dict[str, str] = {
        "item_id": "", "location": "", "lot_number": "", "expiration_date": "",
        "quantity": "", "notes": "",
    }
    count_session_rows: list[list[str]] = []
    count_line_rows: list[list[str]] = []
    count_session_options: list[str] = []

    po_number: str = ""
    po_selected_status: str = ""
    po_status_reason: str = ""
    po_header: dict[str, str] = {
        "supplier": "", "purchasing_channel": "", "required_date": "",
        "contact_name": "", "contact_email": "", "notes": "",
    }
    po_line: dict[str, str] = {
        "inventory_domain": "PACKAGING", "item_id": "", "description": "",
        "quantity": "", "uom": "EACH", "unit_cost": "0", "notes": "",
    }
    po_charges: dict[str, str] = {
        "standard_shipping": "0", "expedited_shipping": "0", "sales_tax": "0",
    }
    po_supplier_options: list[str] = []
    _po_suppliers: list[dict[str, Any]] = []
    _po_packaging_items: list[dict[str, Any]] = []
    _po_supply_items: list[dict[str, Any]] = []
    receipt: dict[str, str] = {
        "line_id": "", "quantity": "", "location": "UNASSIGNED",
        "lot_number": "", "notes": "",
    }
    po_rows: list[list[str]] = []
    po_line_rows: list[list[str]] = []
    po_status_rows: list[list[str]] = []
    po_options: list[str] = []
    po_line_options: list[str] = []

    label: dict[str, str] = {
        "format": "4 x 6 Packaging Box", "material_id": "", "description": "",
        "lot_or_po": "", "quantity": "", "print_date": "",
    }

    async def _actor(self) -> str:
        from .qcc_reflex_pilot import DashboardState
        dashboard = await self.get_state(DashboardState)
        if not dashboard._require_active_session():
            raise ValueError("Sign in again before accessing Materials & Procurement.")
        return dashboard.auth_email or dashboard.auth_name or "QCC USER"

    def _clear_status(self) -> None:
        self.message = ""
        self.error = ""

    async def _load_supply(self) -> None:
        records = await rx.run_in_thread(service.supply_items)
        search = self.supply_search.casefold().strip()
        category = self.supply_category
        if search:
            records = [row for row in records if search in " ".join(
                str(row.get(key, "")) for key in ("item_id", "description", "supplier", "purchasing_channel")
            ).casefold()]
        if category != "ALL CATEGORIES":
            records = [row for row in records if row.get("category") == category]
        self.supply_rows = [{
            "item_id": str(row.get("item_id", "")),
            "description": str(row.get("description", "")),
            "category": str(row.get("category", "")),
            "purchasing_channel": str(row.get("purchasing_channel", "")),
            "supplier": str(row.get("supplier", "")),
            "pack_description": str(row.get("pack_description", "")),
            "uom": str(row.get("uom", "")),
            "on_hand": str(int(row.get("on_hand", 0) or 0)),
            "safety_stock": f"{int(row.get('safety_stock', 0) or 0):,}",
            "reorder_quantity": f"{int(row.get('reorder_quantity', 0) or 0):,}",
            "status": str(row.get("status", "")),
        } for row in records]

    async def _load_counts(self) -> None:
        sessions = await rx.run_in_thread(service.count_sessions)
        self.count_session_options = [str(row["session_id"]) for row in sessions]
        self.count_session_rows = [[
            str(row.get("session_id", "")), str(row.get("inventory_domain", "")),
            str(row.get("started_at", "")), str(row.get("status", "")),
            str(row.get("expected_items", 0)), str(row.get("counted_items", 0)),
            str(row.get("uncounted_items", 0)), str(row.get("started_by", "")),
        ] for row in sessions]
        if self.count_session_id:
            selected = next((row for row in sessions if str(row["session_id"]) == self.count_session_id), None)
            self.count_selected_domain = str((selected or {}).get("inventory_domain", ""))
            lines = await rx.run_in_thread(lambda: service.count_lines(self.count_session_id))
            self.count_line_rows = [[
                str(row.get("item_id", "")), str(row.get("item_description", "")),
                str(row.get("location", "")), str(row.get("lot_number", "")),
                str(row.get("expiration_date") or ""),
                str(row.get("expected_quantity", 0)),
                "NOT COUNTED" if row.get("counted_quantity") is None else str(row.get("counted_quantity")),
                str(row.get("counted_by", "")), str(row.get("counted_at", "")),
            ] for row in lines]
        else:
            self.count_line_rows = []
            self.count_selected_domain = ""

    async def _load_pos(self) -> None:
        orders = await rx.run_in_thread(service.purchase_orders)
        self.po_options = [str(row["po_number"]) for row in orders]
        self.po_rows = [[
            str(row.get("po_number", "")), str(row.get("supplier", "")),
            str(row.get("order_date", "")), str(row.get("required_date") or ""),
            str(row.get("status", "")), str(row.get("line_count", 0)),
            f"{int(row.get('ordered_quantity', 0) or 0):,}",
            f"{int(row.get('received_quantity', 0) or 0):,}",
            f"${float(row.get('total', 0) or 0):,.2f}",
        ] for row in orders]
        if self.po_number:
            detail = await rx.run_in_thread(lambda: service.purchase_order_detail(self.po_number))
            header = detail["header"]
            self.po_selected_status = str(header.get("status", "") or "")
            self.po_charges = {
                "standard_shipping": str(header.get("standard_shipping", 0) or 0),
                "expedited_shipping": str(header.get("expedited_shipping", 0) or 0),
                "sales_tax": str(header.get("sales_tax", 0) or 0),
            }
            lines = detail["lines"]
            self.po_line_options = [str(row["line_id"]) for row in lines]
            self.po_line_rows = [[
                str(row.get("line_id", "")), str(row.get("inventory_domain", "")),
                str(row.get("item_id", "")), str(row.get("description", "")),
                str(row.get("ordered_quantity", 0)), str(row.get("received_quantity", 0)),
                str(int(row.get("ordered_quantity", 0)) - int(row.get("received_quantity", 0))),
                str(row.get("uom", "")), f"${float(row.get('unit_cost', 0) or 0):,.4f}",
            ] for row in lines]
            status_history = await rx.run_in_thread(
                lambda: service.purchase_order_status_history(self.po_number)
            )
            self.po_status_rows = [[
                str(row.get("prior_status", "")),
                str(row.get("new_status", "")),
                str(row.get("reason", "")),
                str(row.get("changed_by", "")),
                str(row.get("changed_at", "")),
            ] for row in status_history]
        else:
            self.po_line_options, self.po_line_rows = [], []
            self.po_selected_status, self.po_status_rows = "", []

    async def _load_po_references(self) -> None:
        self._po_suppliers = await rx.run_in_thread(service.packaging_suppliers)
        self._po_packaging_items = await rx.run_in_thread(service.packaging_items)
        self._po_supply_items = await rx.run_in_thread(service.supply_items)
        self.po_supplier_options = sorted({
            str(row.get("supplier", ""))
            for row in self._po_suppliers
            if row.get("supplier") and str(row.get("status", "ACTIVE")) == "ACTIVE"
        })

    @rx.event
    async def enter(self, section: str):
        self._clear_status()
        try:
            await self._actor()
            if section == "supply":
                await self._load_supply()
            elif section == "counts":
                await self._load_counts()
            elif section == "purchasing":
                await self._load_po_references()
                await self._load_pos()
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def refresh_supply(self):
        self._clear_status()
        try:
            await self._actor()
            await self._load_supply()
        except Exception as error:
            self.error = str(error)

    @rx.event
    def set_supply_search(self, value: str):
        self.supply_search = value

    @rx.event
    def set_supply_category(self, value: str):
        self.supply_category = value

    @rx.event
    def set_supply_adjustment(self, key: str, value: str):
        self.supply_adjustment[key] = value

    @rx.event
    async def save_supply_adjustment(self):
        self._clear_status()
        try:
            actor = await self._actor()
            transaction = await rx.run_in_thread(lambda: service.adjust_supply_item(
                self.supply_adjustment["item_id"], self.supply_adjustment["quantity"],
                actor, self.supply_adjustment["notes"],
            ))
            await self._load_supply()
            self.message = f"Supply adjustment saved: {transaction}"
            self.supply_adjustment = {"item_id": "", "quantity": "", "notes": ""}
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def save_supply_on_hand(self, item_id: str, value: str):
        self._clear_status()
        try:
            actor = await self._actor()
            transaction = await rx.run_in_thread(
                lambda: service.set_supply_on_hand(item_id, value, actor)
            )
            await self._load_supply()
            self.message = (
                f"{item_id} On Hand updated."
                if transaction else f"{item_id} On Hand was already {value}."
            )
        except Exception as error:
            self.error = str(error)

    @rx.event
    def set_count_domain(self, value: str):
        self.count_domain = value

    @rx.event
    def set_count_notes(self, value: str):
        self.count_notes = value

    @rx.event
    def set_count_scope(self, value: str):
        self.count_scope = value

    @rx.event
    def set_count_entry(self, key: str, value: str):
        self.count_entry[key] = value

    @rx.event
    def set_count_acknowledged(self, value: bool):
        self.count_acknowledged = value

    @rx.event
    async def start_count(self):
        self._clear_status()
        try:
            actor = await self._actor()
            self.count_session_id = await rx.run_in_thread(lambda: service.start_count_session(
                self.count_domain, actor, self.count_notes, self.count_scope,
            ))
            self.count_acknowledged = False
            await self._load_counts()
            self.message = "Count session started. Uncounted items remain Not Counted and will never become zero automatically."
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def select_count_session(self, value: str):
        self.count_session_id = value
        self.count_acknowledged = False
        self._clear_status()
        try:
            await self._actor()
            await self._load_counts()
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def save_count_entry(self):
        self._clear_status()
        try:
            actor = await self._actor()
            await rx.run_in_thread(lambda: service.record_count(
                self.count_session_id, self.count_entry["item_id"],
                self.count_entry["quantity"], actor, self.count_entry["location"],
                self.count_entry["lot_number"], self.count_entry["expiration_date"],
                self.count_entry["notes"],
            ))
            if self.count_selected_domain == "PACKAGING":
                self.label = {
                    "format": "2.25 x 1.25 Quantity", "material_id": self.count_entry["item_id"],
                    "description": "", "lot_or_po": self.count_entry["lot_number"],
                    "quantity": self.count_entry["quantity"], "print_date": date.today().isoformat(),
                }
            await self._load_counts()
            self.message = (
                "Count recorded. A quantity-label draft was prepared in Label Printing."
                if self.count_selected_domain == "PACKAGING" else "Supply count recorded."
            )
            self.count_entry["quantity"] = ""
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def complete_count(self):
        self._clear_status()
        try:
            actor = await self._actor()
            result = await rx.run_in_thread(lambda: service.complete_count_session(
                self.count_session_id, actor, self.count_acknowledged,
            ))
            await self._load_counts()
            await self._load_supply()
            self.message = f"Count completed: {result['counted']} counted; {result['uncounted']} acknowledged as Not Counted."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def set_po_header(self, key: str, value: str):
        self.po_header[key] = value

    @rx.event
    def select_po_supplier(self, value: str):
        self.po_header["supplier"] = value
        supplier = next((
            row for row in self._po_suppliers
            if str(row.get("supplier", "")).casefold() == value.casefold()
        ), None)
        if supplier:
            self.po_header["contact_name"] = str(supplier.get("contact_name", ""))
            self.po_header["contact_email"] = str(supplier.get("contact_email", ""))

    @rx.event
    def set_po_line(self, key: str, value: str):
        self.po_line[key] = value
        if key in {"inventory_domain", "item_id"}:
            item_id = self.po_line["item_id"].strip().upper()
            self.po_line["item_id"] = item_id
            records = (
                self._po_packaging_items
                if self.po_line["inventory_domain"] == "PACKAGING"
                else self._po_supply_items
            )
            id_key = "material_id" if self.po_line["inventory_domain"] == "PACKAGING" else "item_id"
            description_key = "item" if self.po_line["inventory_domain"] == "PACKAGING" else "description"
            item = next((
                row for row in records
                if str(row.get(id_key, "")).strip().upper() == item_id
            ), None)
            if item:
                self.po_line["description"] = str(item.get(description_key, ""))
                self.po_line["uom"] = str(item.get("uom", "EACH") or "EACH")

    @rx.event
    def set_po_charge(self, key: str, value: str):
        self.po_charges[key] = value

    @rx.event
    def set_po_status_reason(self, value: str):
        self.po_status_reason = value

    @rx.event
    def set_receipt(self, key: str, value: str):
        self.receipt[key] = value

    @rx.event
    async def create_po(self):
        self._clear_status()
        try:
            actor = await self._actor()
            self.po_number = await rx.run_in_thread(lambda: service.create_purchase_order(dict(self.po_header), actor))
            await self._load_pos()
            self.message = f"Purchase order {self.po_number} created as Draft."
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def select_po(self, value: str):
        self.po_number = value
        self._clear_status()
        try:
            await self._actor()
            await self._load_pos()
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def add_po_line(self):
        self._clear_status()
        try:
            actor = await self._actor()
            await rx.run_in_thread(lambda: service.add_purchase_order_line(
                self.po_number, dict(self.po_line), actor,
            ))
            await self._load_pos()
            self.message = f"Line added to {self.po_number}."
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def save_po_charges(self):
        self._clear_status()
        try:
            actor = await self._actor()
            await rx.run_in_thread(lambda: service.update_purchase_order_charges(
                self.po_number,
                self.po_charges["standard_shipping"],
                self.po_charges["expedited_shipping"],
                self.po_charges["sales_tax"],
                actor,
            ))
            await self._load_pos()
            self.message = f"Freight and tax saved for {self.po_number}."
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def receive_po_line(self):
        self._clear_status()
        try:
            actor = await self._actor()
            receipt_id = await rx.run_in_thread(lambda: service.receive_purchase_order_line(
                self.po_number, self.receipt["line_id"], self.receipt["quantity"], actor,
                self.receipt["location"], self.receipt["lot_number"], self.receipt["notes"],
            ))
            detail = await rx.run_in_thread(lambda: service.purchase_order_detail(self.po_number))
            line = next(row for row in detail["lines"] if str(row["line_id"]) == self.receipt["line_id"])
            self.label = {
                "format": "4 x 6 Packaging Box", "material_id": str(line["item_id"]),
                "description": str(line["description"]), "lot_or_po": self.receipt["lot_number"] or self.po_number,
                "quantity": self.receipt["quantity"], "print_date": date.today().isoformat(),
            }
            await self._load_pos()
            await self._load_supply()
            self.message = f"Receipt {receipt_id} saved. A box-label draft was prepared in Label Printing."
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def close_po(self):
        self._clear_status()
        try:
            actor = await self._actor()
            await rx.run_in_thread(lambda: service.close_purchase_order(self.po_number, actor))
            await self._load_pos()
            self.message = f"{self.po_number} closed. Any remaining quantity is retained as the backorder history."
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def toggle_po_active(self):
        self._clear_status()
        try:
            actor = await self._actor()
            reactivate = self.po_selected_status == "CANCELLED"
            new_status = await rx.run_in_thread(
                lambda: service.set_purchase_order_active(
                    self.po_number,
                    active=reactivate,
                    reason=self.po_status_reason,
                    actor=actor,
                )
            )
            self.po_status_reason = ""
            await self._load_pos()
            self.message = (
                f"{self.po_number} reactivated as {new_status}."
                if reactivate
                else f"{self.po_number} cancelled."
            )
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def download_po(self):
        try:
            await self._actor()
            content = await rx.run_in_thread(lambda: service.purchase_order_pdf(self.po_number))
            return rx.download(data=content, filename=f"{self.po_number}.pdf")
        except Exception as error:
            self.error = str(error)

    @rx.event
    async def email_po(self):
        self._clear_status()
        try:
            await self._actor()
            await rx.run_in_thread(lambda: service.send_purchase_order(self.po_number))
            await self._load_pos()
            self.message = f"{self.po_number} emailed through the configured QCC purchasing mailbox."
        except Exception as error:
            self.error = str(error)

    @rx.event
    def set_label(self, key: str, value: str):
        self.label[key] = value

    @rx.event
    def prefill_label(self, material_id: str, description: str, quantity: str):
        self.label = {
            "format": "4 x 6 Packaging Box", "material_id": material_id,
            "description": description, "lot_or_po": "", "quantity": quantity,
            "print_date": date.today().isoformat(),
        }
        self.message = "Label draft prepared. Open Label Printing to review and download it."

    @rx.event
    async def download_label(self):
        self._clear_status()
        try:
            await self._actor()
            if self.label["format"] == "2.25 x 1.25 Quantity":
                zpl = service.quantity_label_zpl(self.label["material_id"], self.label["quantity"])
                suffix = "quantity"
            else:
                zpl = service.box_label_zpl(
                    self.label["material_id"], self.label["description"],
                    self.label["lot_or_po"], self.label["quantity"],
                    self.label["print_date"] or None,
                )
                suffix = "box"
            name = self.label["material_id"].replace("/", "-") or "material"
            return rx.download(data=zpl.encode("utf-8"), filename=f"{name}_{suffix}.zpl")
        except Exception as error:
            self.error = str(error)


def _field(label: str, value: Any, handler: Any, placeholder: str = "") -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="bold"),
        rx.input(value=value, on_change=handler, placeholder=placeholder, width="100%"),
        spacing="1", width="100%",
    )


def _table(headers: list[str], rows: Any) -> rx.Component:
    return rx.box(
        rx.table.root(
            rx.table.header(rx.table.row(*[
                rx.table.column_header_cell(header, white_space="normal") for header in headers
            ])),
            rx.table.body(rx.foreach(rows, lambda row: rx.table.row(
                rx.foreach(row, lambda value: rx.table.cell(value, white_space="normal"))
            ))),
            width="100%", variant="surface", size="1",
        ),
        width="100%", overflow_x="auto",
    )


def _status() -> rx.Component:
    return rx.vstack(
        rx.cond(ProcurementState.message != "", rx.callout(
            ProcurementState.message, icon="circle-check", color_scheme="green", width="100%")),
        rx.cond(ProcurementState.error != "", rx.callout(
            ProcurementState.error, icon="triangle-alert", color_scheme="red", width="100%")),
        width="100%", spacing="2",
    )


def supply_inventory_panel() -> rx.Component:
    state = ProcurementState
    return rx.vstack(
        rx.heading("Supply Inventory", size="5"),
        rx.text("Facility-wide consumable and office-supply inventory seeded from the 88-row workbook. Formal counts run biweekly; supply items do not use scanning or inventory labels."),
        _status(),
        rx.flex(
            rx.input(placeholder="Search item, supplier, or channel", value=state.supply_search,
                     on_change=state.set_supply_search, width="320px"),
            rx.select(["ALL CATEGORIES", *service.SUPPLY_CATEGORIES], value=state.supply_category,
                      on_change=state.set_supply_category, width="230px"),
            rx.button("Apply / Refresh", on_click=state.refresh_supply), gap="2", wrap="wrap",
        ),
        rx.card(
            rx.heading("Between-Count Adjustment", size="3"),
            rx.text("Use for a known receipt, use, or correction between formal biweekly counts. Enter a negative number for consumption."),
            rx.grid(
                _field("Supply Item ID", state.supply_adjustment["item_id"], lambda v: state.set_supply_adjustment("item_id", v)),
                _field("Whole-number adjustment", state.supply_adjustment["quantity"], lambda v: state.set_supply_adjustment("quantity", v)),
                _field("Required reason", state.supply_adjustment["notes"], lambda v: state.set_supply_adjustment("notes", v)),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", gap="2",
            ),
            rx.button("Save Adjustment", on_click=state.save_supply_adjustment),
            width="100%",
        ),
        rx.box(
            rx.table.root(
                rx.table.header(rx.table.row(*[
                    rx.table.column_header_cell(header, white_space="normal")
                    for header in [
                        "Item ID", "Supply Item", "Category", "Purchasing Channel",
                        "Supplier", "Pack", "UOM", "On Hand", "Safety Stock",
                        "Qty to Order", "Status",
                    ]
                ])),
                rx.table.body(rx.foreach(state.supply_rows, lambda row: rx.table.row(
                    rx.table.cell(row["item_id"]),
                    rx.table.cell(row["description"], white_space="normal"),
                    rx.table.cell(row["category"], white_space="normal"),
                    rx.table.cell(row["purchasing_channel"], white_space="normal"),
                    rx.table.cell(row["supplier"], white_space="normal"),
                    rx.table.cell(row["pack_description"], white_space="normal"),
                    rx.table.cell(row["uom"]),
                    rx.table.cell(rx.input(
                        default_value=row["on_hand"],
                        type="number", min="0", step="1", width="92px",
                        on_blur=lambda value: state.save_supply_on_hand(
                            row["item_id"], value,
                        ),
                    )),
                    rx.table.cell(row["safety_stock"], text_align="right"),
                    rx.table.cell(row["reorder_quantity"], text_align="right"),
                    rx.table.cell(row["status"]),
                ))),
                width="100%", variant="surface", size="1",
            ),
            width="100%", overflow_x="auto",
        ),
        on_mount=lambda: state.enter("supply"), width="100%", spacing="3",
    )


def count_sessions_panel() -> rx.Component:
    state = ProcurementState
    return rx.vstack(
        rx.heading("Formal Count Sessions", size="5"),
        rx.callout("Packaging counts begin with every active packaging item and use item + location + lot. Supply counts are facility-wide and expected every 14 days. Uncounted items stay Not Counted—never zero.", icon="clipboard-check", width="100%"),
        _status(),
        rx.card(
            rx.heading("Start a Count", size="3"),
            rx.grid(
                rx.vstack(rx.text("Inventory type", size="2", weight="bold"),
                          rx.select(service.COUNT_DOMAINS, value=state.count_domain, on_change=state.set_count_domain, width="100%"), width="100%"),
                _field("Count notes", state.count_notes, state.set_count_notes),
                _field("Optional item scope", state.count_scope, state.set_count_scope,
                       "Blank = all active items; enter an ID or description to narrow"),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", gap="2",
            ),
            rx.button("Start Count Session", on_click=state.start_count), width="100%"),
        rx.card(
            rx.heading("Scan / Enter Count", size="3"),
            rx.text("The counted quantity accepts digits only. Zero is valid; letters, decimals, and another item's barcode are rejected."),
            rx.select(state.count_session_options, value=state.count_session_id,
                      on_change=state.select_count_session, placeholder="Select an open or historical session", width="100%"),
            rx.grid(
                _field("Item ID", state.count_entry["item_id"], lambda v: state.set_count_entry("item_id", v)),
                _field("Location (packaging)", state.count_entry["location"], lambda v: state.set_count_entry("location", v)),
                _field("Lot (when applicable)", state.count_entry["lot_number"], lambda v: state.set_count_entry("lot_number", v)),
                _field("Expiration YYYY-MM-DD (when applicable)", state.count_entry["expiration_date"], lambda v: state.set_count_entry("expiration_date", v)),
                _field("Whole-number quantity", state.count_entry["quantity"], lambda v: state.set_count_entry("quantity", v)),
                _field("Notes", state.count_entry["notes"], lambda v: state.set_count_entry("notes", v)),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", gap="2",
            ),
            rx.button("Record Count", on_click=state.save_count_entry),
            rx.hstack(
                rx.switch(checked=state.count_acknowledged, on_change=state.set_count_acknowledged),
                rx.text("I reviewed the uncounted items and acknowledge they will remain Not Counted."),
                align="center", width="100%",
            ),
            rx.button("Complete Count Session", on_click=state.complete_count, color_scheme="orange"),
            width="100%", spacing="3",
        ),
        rx.heading("Count History", size="4"),
        _table(["Session ID", "Type", "Started", "Status", "Expected", "Counted", "Not Counted", "Started By"], state.count_session_rows),
        rx.heading("Selected Session — Item History", size="4"),
        _table(["Item ID", "Item", "Location", "Lot", "Expiration", "Expected", "Counted", "Employee", "Counted At"], state.count_line_rows),
        on_mount=lambda: state.enter("counts"), width="100%", spacing="3",
    )


def purchasing_panel() -> rx.Component:
    state = ProcurementState
    return rx.vstack(
        rx.heading("Purchasing & Receiving", size="5"),
        rx.text("Create QCC purchase orders, download supplier PDFs, track partial receipts and backorders, and close completed orders. Email activates when IT provides the Microsoft Graph credentials."),
        _status(),
        rx.card(
            rx.heading("Create Purchase Order", size="3"),
            rx.grid(
                rx.vstack(
                    rx.text("Supplier", size="2", weight="bold"),
                    rx.select(
                        state.po_supplier_options,
                        value=state.po_header["supplier"],
                        on_change=state.select_po_supplier,
                        placeholder="Select supplier",
                        width="100%",
                    ),
                    spacing="1", width="100%",
                ),
                _field("Purchasing channel", state.po_header["purchasing_channel"], lambda v: state.set_po_header("purchasing_channel", v)),
                _field("Required date YYYY-MM-DD", state.po_header["required_date"], lambda v: state.set_po_header("required_date", v)),
                _field("Supplier contact", state.po_header["contact_name"], lambda v: state.set_po_header("contact_name", v)),
                _field("Supplier email", state.po_header["contact_email"], lambda v: state.set_po_header("contact_email", v)),
                _field("Notes", state.po_header["notes"], lambda v: state.set_po_header("notes", v)),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", gap="2",
            ),
            rx.button("Create Draft PO", on_click=state.create_po), width="100%",
        ),
        rx.card(
            rx.heading("Selected Purchase Order", size="3"),
            rx.select(state.po_options, value=state.po_number, on_change=state.select_po,
                      placeholder="Select purchase order", width="100%"),
            rx.cond(
                state.po_number != "",
                rx.callout(
                    "CURRENT STATUS: " + state.po_selected_status,
                    icon="file-check-2",
                    color_scheme=rx.cond(
                        state.po_selected_status == "CANCELLED",
                        "red",
                        "teal",
                    ),
                    width="100%",
                ),
            ),
            rx.grid(
                rx.vstack(rx.text("Item type", size="2", weight="bold"),
                          rx.select(service.COUNT_DOMAINS, value=state.po_line["inventory_domain"],
                                    on_change=lambda v: state.set_po_line("inventory_domain", v), width="100%"), width="100%"),
                _field("Item ID", state.po_line["item_id"], lambda v: state.set_po_line("item_id", v)),
                _field("Description", state.po_line["description"], lambda v: state.set_po_line("description", v)),
                _field("Ordered quantity", state.po_line["quantity"], lambda v: state.set_po_line("quantity", v)),
                _field("UOM", state.po_line["uom"], lambda v: state.set_po_line("uom", v)),
                _field("Unit cost", state.po_line["unit_cost"], lambda v: state.set_po_line("unit_cost", v)),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", gap="2",
            ),
            rx.button("Add PO Line", on_click=state.add_po_line),
            rx.grid(
                _field("Standard shipping", state.po_charges["standard_shipping"],
                       lambda v: state.set_po_charge("standard_shipping", v)),
                _field("Expedited shipping", state.po_charges["expedited_shipping"],
                       lambda v: state.set_po_charge("expedited_shipping", v)),
                _field("Sales tax", state.po_charges["sales_tax"],
                       lambda v: state.set_po_charge("sales_tax", v)),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", gap="2",
            ),
            rx.button("Save Shipping & Tax", on_click=state.save_po_charges, variant="outline"),
            _table(["Line ID", "Type", "Item ID", "Description", "Ordered", "Received", "Backordered", "UOM", "Unit Cost"], state.po_line_rows),
            rx.heading("Record Partial or Full Receipt", size="3"),
            rx.select(state.po_line_options, value=state.receipt["line_id"],
                      on_change=lambda v: state.set_receipt("line_id", v), placeholder="Select PO line", width="100%"),
            rx.grid(
                _field("Quantity received", state.receipt["quantity"], lambda v: state.set_receipt("quantity", v)),
                _field("Receiving location", state.receipt["location"], lambda v: state.set_receipt("location", v)),
                _field("Supplier lot", state.receipt["lot_number"], lambda v: state.set_receipt("lot_number", v)),
                _field("Receipt notes", state.receipt["notes"], lambda v: state.set_receipt("notes", v)),
                columns=rx.breakpoints(initial="1", md="2"), width="100%", gap="2",
            ),
            _field(
                "Required reason to cancel or reactivate this PO",
                state.po_status_reason,
                state.set_po_status_reason,
            ),
            rx.flex(
                rx.button("Receive & Prepare Label", on_click=state.receive_po_line),
                rx.button("Download PO PDF", on_click=state.download_po, variant="outline"),
                rx.button("Email PO", on_click=state.email_po, variant="outline"),
                rx.button("Close PO", on_click=state.close_po, color_scheme="orange", variant="outline"),
                rx.cond(
                    state.po_selected_status == "CANCELLED",
                    rx.button(
                        "Reactivate PO",
                        on_click=state.toggle_po_active,
                        disabled=state.po_status_reason == "",
                        color_scheme="teal",
                    ),
                    rx.button(
                        "Cancel PO",
                        on_click=state.toggle_po_active,
                        disabled=(state.po_number == "") | (state.po_status_reason == ""),
                        color_scheme="red",
                        variant="outline",
                    ),
                ),
                gap="2", wrap="wrap",
            ),
            rx.separator(),
            rx.heading("Purchase Order Activity", size="3"),
            rx.text(
                "Cancelling a PO blocks lines, receipts, shipping and tax changes, and email. The PDF and audit history remain available.",
                size="2",
            ),
            rx.heading("Status Audit History", size="3"),
            _table(
                ["Previous Status", "New Status", "Reason", "Changed By", "Changed At"],
                state.po_status_rows,
            ),
            width="100%", spacing="3",
        ),
        rx.heading("Purchase Order Register", size="4"),
        _table(["PO Number", "Supplier", "Order Date", "Required", "Status", "Lines", "Ordered", "Received", "Total"], state.po_rows),
        on_mount=lambda: state.enter("purchasing"), width="100%", spacing="3",
    )


def label_printing_panel() -> rx.Component:
    state = ProcurementState
    return rx.vstack(
        rx.heading("Packaging Inventory Labels", size="5"),
        rx.callout("Zebra-ready ZPL. The 4 × 6 box label keeps Print Date, Material ID, Description, Lot / PO, and Quantity as five separate QR values. Supply inventory is intentionally excluded.", icon="printer", width="100%"),
        _status(),
        rx.card(
            rx.select(["4 x 6 Packaging Box", "2.25 x 1.25 Quantity"], value=state.label["format"],
                      on_change=lambda v: state.set_label("format", v), width="100%"),
            rx.grid(
                _field("Material ID", state.label["material_id"], lambda v: state.set_label("material_id", v)),
                _field("Description", state.label["description"], lambda v: state.set_label("description", v)),
                _field("Lot / PO", state.label["lot_or_po"], lambda v: state.set_label("lot_or_po", v)),
                _field("Whole-number quantity", state.label["quantity"], lambda v: state.set_label("quantity", v)),
                _field("Print date (blank = today)", state.label["print_date"], lambda v: state.set_label("print_date", v)),
                columns=rx.breakpoints(initial="1", md="2"), width="100%", gap="2",
            ),
            rx.button("Download Zebra ZPL", on_click=state.download_label, width="100%"),
            width="100%", spacing="3",
        ),
        width="100%", spacing="3",
    )


def procurement_workspace(section: str) -> rx.Component:
    return {
        "supply": supply_inventory_panel,
        "counts": count_sessions_panel,
        "purchasing": purchasing_panel,
        "labels": label_printing_panel,
    }[section]()
