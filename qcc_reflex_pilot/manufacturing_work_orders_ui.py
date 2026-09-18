"""Reflex workspace for configurable Manufacturing work orders."""

from __future__ import annotations

from datetime import date
from typing import Any

import reflex as rx

from . import manufacturing_work_orders as service

DARK = "#111827"
ACCENT = "#2f9ca2"
MUTED = "#64748b"


class ManufacturingWorkOrderState(rx.State):
    message: str = ""
    error: str = ""
    selected_id: str = ""
    selected_number: str = ""
    selected_status: str = ""
    register_rows: list[list[str]] = []
    order_options: list[str] = []
    _order_ids: dict[str, str] = {}
    input_rows: list[list[str]] = []
    component_rows: list[list[str]] = []
    event_rows: list[list[str]] = []
    package_options: list[str] = []
    material_options: list[str] = []
    bom_options: list[str] = ["NO BOM"]
    _bom_ids: dict[str, str] = {"NO BOM": ""}

    create_form: dict[str, str] = {
        "order_type": "ME", "product_name": "", "strain": "",
        "expected_output_quantity": "", "output_uom": "GRAMS", "linked_bom": "NO BOM",
        "requested_date": date.today().isoformat(), "expected_completion_date": "",
        "requested_by": "", "assigned_to": "", "notes": "",
    }
    input_form: dict[str, str] = {"package_tag": "", "quantity": ""}
    component_form: dict[str, str] = {
        "material_id": "", "quantity": "", "location": "", "lot_number": "",
    }
    consumption_form: dict[str, str] = {"line_type": "CANNABIS", "line_id": "", "quantity": ""}
    substitution_form: dict[str, str] = {
        "component_id": "", "replacement_material_id": "", "reason": "",
    }
    approval_component_id: str = ""
    output_form: dict[str, str] = {
        "package_tag": "", "quantity": "", "location": "", "ats_status": "",
    }
    output_reconciled: bool = False
    cancel_reason: str = ""
    config_form: dict[str, str] = {
        "number_pattern_me": service.DEFAULT_SETTINGS["number_pattern_me"],
        "number_pattern_mp": service.DEFAULT_SETTINGS["number_pattern_mp"],
        "sequence_padding": "4", "me_close_rule": service.DEFAULT_SETTINGS["me_close_rule"],
        "mp_close_rule": service.DEFAULT_SETTINGS["mp_close_rule"],
        "ats_closed_statuses": ", ".join(service.DEFAULT_SETTINGS["ats_closed_statuses"]),
        "substitution_approver_roles": ", ".join(service.DEFAULT_SETTINGS["substitution_approver_roles"]),
    }
    prevent_self_approval: bool = True
    allow_manual_reconciliation: bool = True

    async def _identity(self) -> tuple[str, str]:
        from .qcc_reflex_pilot import DashboardState
        dashboard = await self.get_state(DashboardState)
        if not dashboard._require_active_session():
            raise ValueError("Sign in again before accessing Manufacturing.")
        return dashboard.auth_email or dashboard.auth_name or "QCC USER", dashboard.auth_role or "USER"

    def _clear(self) -> None:
        self.message = ""
        self.error = ""

    def set_create_field(self, key: str, value: str):
        self.create_form[key] = value

    def set_input_field(self, key: str, value: str):
        self.input_form[key] = value

    def set_component_field(self, key: str, value: str):
        self.component_form[key] = value

    def set_consumption_field(self, key: str, value: str):
        self.consumption_form[key] = value

    def set_substitution_field(self, key: str, value: str):
        self.substitution_form[key] = value

    def set_output_field(self, key: str, value: str):
        self.output_form[key] = value

    def set_config_field(self, key: str, value: str):
        self.config_form[key] = value


    def set_approval_component(self, value: str):
        self.approval_component_id = value

    def set_output_reconciled_value(self, value: bool):
        self.output_reconciled = value

    def set_cancel_reason_value(self, value: str):
        self.cancel_reason = value

    def set_prevent_self_approval_value(self, value: bool):
        self.prevent_self_approval = value

    def set_allow_manual_reconciliation_value(self, value: bool):
        self.allow_manual_reconciliation = value
    async def _load_reference(self) -> None:
        packages = await rx.run_in_thread(service.available_cannabis_packages)
        self.package_options = [str(row["package_tag"]) for row in packages]
        materials = await rx.run_in_thread(service.available_packaging_components)
        self.material_options = [str(row["material_id"]) for row in materials]
        boms = service.bom_options()
        self.bom_options = ["NO BOM", *[row["name"] for row in boms]]
        self._bom_ids = {"NO BOM": "", **{row["name"]: row["id"] for row in boms}}

    async def _load_register(self) -> None:
        records = await rx.run_in_thread(service.list_work_orders)
        self.register_rows = [[
            str(row.get("work_order_number", "")), str(row.get("order_type", "")),
            str(row.get("status", "")), str(row.get("product_name", "")), str(row.get("strain", "")),
            f"{float(row.get('expected_output_quantity', 0) or 0):g} {row.get('output_uom', '')}",
            str(row.get("requested_date", "")), str(row.get("expected_completion_date", "")),
            str(row.get("assigned_to", "")), str(row.get("output_package_tag", "")),
        ] for row in records]
        self.order_options = [str(row.get("work_order_number", "")) for row in records]
        self._order_ids = {str(row.get("work_order_number", "")): str(row.get("work_order_id", "")) for row in records}

    async def _load_detail(self) -> None:
        if not self.selected_id:
            self.input_rows = []
            self.component_rows = []
            self.event_rows = []
            return
        detail = await rx.run_in_thread(service.work_order_detail, self.selected_id)
        order = detail["order"]
        self.selected_number = str(order.get("work_order_number", ""))
        self.selected_status = str(order.get("status", ""))
        self.input_rows = [[
            str(row.get("input_id", "")), str(row.get("package_tag", "")), str(row.get("item_name", "")),
            str(row.get("source_location", "")), f"{float(row.get('reserved_quantity', 0) or 0):g}",
            f"{float(row.get('consumed_quantity', 0) or 0):g}", str(row.get("uom", "")),
        ] for row in detail["inputs"]]
        self.component_rows = [[
            str(row.get("component_id", "")), str(row.get("material_id", "")), str(row.get("description", "")),
            f"{float(row.get('required_quantity', 0) or 0):g}", f"{float(row.get('reserved_quantity', 0) or 0):g}",
            f"{float(row.get('consumed_quantity', 0) or 0):g}", str(row.get("uom", "")),
            str(row.get("source_location", "")), str(row.get("lot_number", "")),
            str(row.get("substitution_status", "")), str(row.get("substitution_material_id", "")),
        ] for row in detail["components"]]
        self.event_rows = [[
            str(row.get("created_at", ""))[:19].replace("T", " "), str(row.get("event_type", "")),
            str(row.get("prior_status", "")), str(row.get("new_status", "")), str(row.get("actor", "")),
        ] for row in detail["events"]]
        self.output_form = {
            "package_tag": str(order.get("output_package_tag", "")),
            "quantity": str(order.get("output_quantity") or ""), "location": str(order.get("output_location", "")),
            "ats_status": str(order.get("ats_status", "")),
        }
        self.output_reconciled = bool(order.get("output_reconciled", False))

    async def enter(self):
        self._clear()
        try:
            actor, _ = await self._identity()
            if not self.create_form["requested_by"]:
                self.create_form["requested_by"] = actor
            await self._load_register()
            await self._load_reference()
            config = await rx.run_in_thread(service.settings)
            self.config_form = {
                "number_pattern_me": str(config["number_pattern_me"]), "number_pattern_mp": str(config["number_pattern_mp"]),
                "sequence_padding": str(config["sequence_padding"]), "me_close_rule": str(config["me_close_rule"]),
                "mp_close_rule": str(config["mp_close_rule"]), "ats_closed_statuses": ", ".join(config["ats_closed_statuses"]),
                "substitution_approver_roles": ", ".join(config["substitution_approver_roles"]),
            }
            self.prevent_self_approval = bool(config["prevent_self_approval"])
            self.allow_manual_reconciliation = bool(config["allow_manual_reconciliation"])
        except Exception as error:
            self.error = str(error)

    async def select_order(self, number: str):
        self.selected_number = number
        self.selected_id = self._order_ids.get(number, "")
        try:
            await self._load_detail()
        except Exception as error:
            self.error = str(error)

    async def create_order(self):
        self._clear()
        try:
            actor, _ = await self._identity()
            form = dict(self.create_form)
            form["linked_bom_id"] = self._bom_ids.get(form.pop("linked_bom", "NO BOM"), "")
            self.selected_id = await rx.run_in_thread(service.create_work_order, form, actor)
            self.message = "Manufacturing work order created as a draft."
            await self._load_register(); await self._load_reference(); await self._load_detail()
        except Exception as error:
            self.error = str(error)

    async def add_input(self):
        self._clear()
        try:
            actor, _ = await self._identity()
            await rx.run_in_thread(service.add_cannabis_input, self.selected_id, self.input_form["package_tag"], self.input_form["quantity"], actor)
            self.message = "Cannabis package reserved. Inventory has not been deducted."
            await self._load_reference(); await self._load_detail()
        except Exception as error: self.error = str(error)

    async def add_component(self):
        self._clear()
        try:
            actor, _ = await self._identity()
            await rx.run_in_thread(service.add_packaging_component, self.selected_id, self.component_form["material_id"], self.component_form["quantity"], actor, self.component_form["location"], self.component_form["lot_number"])
            self.message = "Packaging component reserved."
            await self._load_reference(); await self._load_detail()
        except Exception as error: self.error = str(error)

    async def release(self):
        self._clear()
        try:
            actor, _ = await self._identity(); await rx.run_in_thread(service.release_work_order, self.selected_id, actor)
            self.message = "Work order released; inputs and components remain reserved until consumption is confirmed."
            await self._load_register(); await self._load_detail()
        except Exception as error: self.error = str(error)

    async def consume(self):
        self._clear()
        try:
            actor, _ = await self._identity()
            await rx.run_in_thread(service.confirm_consumption, self.selected_id, self.consumption_form["line_type"], self.consumption_form["line_id"], self.consumption_form["quantity"], actor)
            self.message = "Consumption confirmed and recorded in the audit history."
            await self._load_register(); await self._load_reference(); await self._load_detail()
        except Exception as error: self.error = str(error)

    async def request_substitution(self):
        self._clear()
        try:
            actor, _ = await self._identity()
            await rx.run_in_thread(service.request_substitution, self.selected_id, self.substitution_form["component_id"], self.substitution_form["replacement_material_id"], self.substitution_form["reason"], actor)
            self.message = "Substitution sent for independent supervisor/manager approval."
            await self._load_detail()
        except Exception as error: self.error = str(error)

    async def approve_substitution(self):
        self._clear()
        try:
            actor, role = await self._identity()
            await rx.run_in_thread(service.approve_substitution, self.selected_id, self.approval_component_id, actor, role)
            self.message = "Substitution approved."
            await self._load_detail()
        except Exception as error: self.error = str(error)

    async def save_output(self):
        self._clear()
        try:
            actor, _ = await self._identity()
            status = await rx.run_in_thread(service.record_output, self.selected_id, self.output_form["package_tag"], self.output_form["quantity"], self.output_form["location"], self.output_reconciled, self.output_form["ats_status"], actor)
            self.message = f"Output recorded. Work order status: {status}."
            await self._load_register(); await self._load_detail()
        except Exception as error: self.error = str(error)

    async def cancel(self):
        self._clear()
        try:
            actor, _ = await self._identity(); await rx.run_in_thread(service.cancel_work_order, self.selected_id, self.cancel_reason, actor)
            self.message = "Work order cancelled; unused reservations are released."
            await self._load_register(); await self._load_reference(); await self._load_detail()
        except Exception as error: self.error = str(error)

    async def save_configuration(self):
        self._clear()
        try:
            actor, role = await self._identity()
            if service.normalized_role(role) != "ADMIN":
                raise ValueError("Admin access is required to change Manufacturing configuration.")
            values: dict[str, Any] = dict(self.config_form)
            values["prevent_self_approval"] = self.prevent_self_approval
            values["allow_manual_reconciliation"] = self.allow_manual_reconciliation
            await rx.run_in_thread(service.save_settings, values, actor)
            self.message = "Manufacturing configuration saved for this facility."
        except Exception as error: self.error = str(error)


def _field(label: str, control: rx.Component) -> rx.Component:
    return rx.vstack(rx.text(label, size="2", weight="bold", color=DARK), control, spacing="1", width="100%", align="start")


def _table(headers: list[str], rows: rx.Var) -> rx.Component:
    return rx.box(
        rx.table.root(
            rx.table.header(rx.table.row(*[rx.table.column_header_cell(
                header, background=DARK, color="white", white_space="normal", min_width="110px") for header in headers])),
            rx.table.body(rx.foreach(rows, lambda row: rx.table.row(*[
                rx.table.cell(row[index], white_space="normal", font_size="0.78rem") for index in range(len(headers))
            ]))),
            size="1", width="100%",
        ), overflow_x="auto", width="100%",
    )


def _status(state: type[ManufacturingWorkOrderState]) -> rx.Component:
    return rx.vstack(
        rx.cond(state.message != "", rx.callout(state.message, icon="circle-check", color_scheme="green", width="100%"), rx.fragment()),
        rx.cond(state.error != "", rx.callout(state.error, icon="triangle-alert", color_scheme="red", width="100%"), rx.fragment()),
        width="100%", spacing="2",
    )


def manufacturing_work_orders_workspace() -> rx.Component:
    state = ManufacturingWorkOrderState
    return rx.vstack(
        _status(state),
        rx.tabs.root(
            rx.tabs.list(rx.tabs.trigger("Work Orders", value="orders"), rx.tabs.trigger("Configuration", value="configuration")),
            rx.tabs.content(
                rx.vstack(
                    rx.card(rx.vstack(
                        rx.hstack(rx.heading("New Manufacturing Work Order", size="5"), rx.spacer(),
                                  rx.badge("CONFIGURABLE ME / MP", color_scheme="teal"), width="100%"),
                        rx.callout("ME creates bulk Manufacturing WIP and closes after its output tag is reconciled. MP converts bulk WIP into packaged CPG and closes when its single finished tag reaches ATS.", icon="info", color_scheme="blue", width="100%"),
                        rx.grid(
                            _field("Order Type", rx.select(["ME", "MP"], value=state.create_form["order_type"], on_change=lambda value: state.set_create_field("order_type", value), width="100%")),
                            _field("Product Name", rx.input(value=state.create_form["product_name"], on_change=lambda value: state.set_create_field("product_name", value), width="100%")),
                            _field("Strain", rx.input(value=state.create_form["strain"], on_change=lambda value: state.set_create_field("strain", value), width="100%")),
                            _field("Expected Output", rx.input(value=state.create_form["expected_output_quantity"], on_change=lambda value: state.set_create_field("expected_output_quantity", value), width="100%")),
                            _field("Output UOM", rx.select(["GRAMS", "UNITS"], value=state.create_form["output_uom"], on_change=lambda value: state.set_create_field("output_uom", value), width="100%")),
                            _field("Packaging BOM", rx.select(state.bom_options, value=state.create_form["linked_bom"], on_change=lambda value: state.set_create_field("linked_bom", value), width="100%")),
                            _field("Requested Date", rx.input(type="date", value=state.create_form["requested_date"], on_change=lambda value: state.set_create_field("requested_date", value), width="100%")),
                            _field("Expected Completion", rx.input(type="date", value=state.create_form["expected_completion_date"], on_change=lambda value: state.set_create_field("expected_completion_date", value), width="100%")),
                            _field("Assigned To", rx.input(value=state.create_form["assigned_to"], on_change=lambda value: state.set_create_field("assigned_to", value), width="100%")),
                            columns=rx.breakpoints(initial="1", sm="2", lg="3"), spacing="3", width="100%"),
                        _field("Notes", rx.text_area(value=state.create_form["notes"], on_change=lambda value: state.set_create_field("notes", value), width="100%")),
                        rx.button("Create Draft Work Order", on_click=state.create_order, background=ACCENT, color="white"),
                        width="100%", align="start", spacing="3"), width="100%"),
                    rx.card(rx.vstack(
                        rx.hstack(rx.heading("Work Order Register", size="5"), rx.spacer(), rx.button("Refresh", on_click=state.enter, variant="outline"), width="100%"),
                        _table(["Work Order", "Type", "Status", "Product", "Strain", "Expected", "Requested", "Due", "Assigned", "Output Tag"], state.register_rows),
                        _field("Selected Work Order", rx.select(state.order_options, value=state.selected_number, on_change=state.select_order, placeholder="Select a work order", width="100%")),
                        width="100%", align="start", spacing="3"), width="100%"),
                    rx.cond(state.selected_id != "", _selected_work_order(state), rx.fragment()),
                    width="100%", spacing="4", align="start"),
                value="orders"),
            rx.tabs.content(_configuration(state), value="configuration"),
            default_value="orders", width="100%"),
        width="100%", spacing="4", align="start", on_mount=state.enter,
    )


def _selected_work_order(state: type[ManufacturingWorkOrderState]) -> rx.Component:
    return rx.vstack(
        rx.card(rx.vstack(
            rx.hstack(rx.heading(state.selected_number, size="5"), rx.badge(state.selected_status, color_scheme="purple"), width="100%"),
            rx.heading("1. Reserve Inputs", size="4"),
            rx.grid(
                _field("Cannabis Package Tag", rx.select(state.package_options, value=state.input_form["package_tag"], on_change=lambda value: state.set_input_field("package_tag", value), width="100%")),
                _field("Reserved Quantity", rx.input(value=state.input_form["quantity"], on_change=lambda value: state.set_input_field("quantity", value), width="100%")),
                columns=rx.breakpoints(initial="1", md="2"), width="100%", spacing="3"),
            rx.button("Reserve Cannabis Input", on_click=state.add_input, background=ACCENT, color="white"),
            _table(["Input ID", "Package Tag", "Item", "Location", "Reserved", "Consumed", "UOM"], state.input_rows),
            rx.divider(),
            rx.grid(
                _field("Packaging Material ID", rx.select(state.material_options, value=state.component_form["material_id"], on_change=lambda value: state.set_component_field("material_id", value), width="100%")),
                _field("Required Quantity", rx.input(value=state.component_form["quantity"], on_change=lambda value: state.set_component_field("quantity", value), width="100%")),
                _field("Source Location", rx.input(value=state.component_form["location"], on_change=lambda value: state.set_component_field("location", value), width="100%")),
                _field("Lot", rx.input(value=state.component_form["lot_number"], on_change=lambda value: state.set_component_field("lot_number", value), width="100%")),
                columns=rx.breakpoints(initial="1", md="2", lg="4"), width="100%", spacing="3"),
            rx.button("Reserve Packaging Component", on_click=state.add_component, variant="outline"),
            _table(["Component ID", "Material ID", "Description", "Required", "Reserved", "Consumed", "UOM", "Location", "Lot", "Substitution", "Replacement"], state.component_rows),
            rx.button("Release Work Order", on_click=state.release, background="#2563eb", color="white"),
            width="100%", spacing="3", align="start"), width="100%"),
        rx.card(rx.vstack(
            rx.heading("2. Confirm Consumption", size="4"),
            rx.callout("Releasing reserves inventory. This step records actual use; packaging inventory is deducted only here.", icon="info", width="100%"),
            rx.grid(
                _field("Line Type", rx.select(["CANNABIS", "PACKAGING"], value=state.consumption_form["line_type"], on_change=lambda value: state.set_consumption_field("line_type", value), width="100%")),
                _field("Input / Component ID", rx.input(value=state.consumption_form["line_id"], on_change=lambda value: state.set_consumption_field("line_id", value), width="100%")),
                _field("Consumed Quantity", rx.input(value=state.consumption_form["quantity"], on_change=lambda value: state.set_consumption_field("quantity", value), width="100%")),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", spacing="3"),
            rx.button("Confirm Consumption", on_click=state.consume, background=ACCENT, color="white"),
            width="100%", align="start", spacing="3"), width="100%"),
        rx.card(rx.vstack(
            rx.heading("3. Packaging Substitution", size="4"),
            rx.callout("A reason is required. A different configured Supervisor, Manager, or Admin must approve the request.", icon="shield-check", color_scheme="orange", width="100%"),
            rx.grid(
                _field("Component ID", rx.input(value=state.substitution_form["component_id"], on_change=lambda value: state.set_substitution_field("component_id", value), width="100%")),
                _field("Replacement Material", rx.select(state.material_options, value=state.substitution_form["replacement_material_id"], on_change=lambda value: state.set_substitution_field("replacement_material_id", value), width="100%")),
                _field("Required Reason", rx.input(value=state.substitution_form["reason"], on_change=lambda value: state.set_substitution_field("reason", value), width="100%")),
                columns=rx.breakpoints(initial="1", md="3"), width="100%", spacing="3"),
            rx.hstack(rx.button("Request Substitution", on_click=state.request_substitution, variant="outline"),
                      rx.input(placeholder="Pending Component ID", value=state.approval_component_id, on_change=state.set_approval_component),
                      rx.button("Approve", on_click=state.approve_substitution, color_scheme="green"), wrap="wrap"),
            width="100%", align="start", spacing="3"), width="100%"),
        rx.card(rx.vstack(
            rx.heading("4. Finished Output & Reconciliation", size="4"),
            rx.callout("Each work order accepts one finished Metrc tag. Until the Metrc API is connected, authorized users record reconciliation and ATS status here.", icon="package-check", color_scheme="blue", width="100%"),
            rx.grid(
                _field("Output Metrc Tag", rx.input(value=state.output_form["package_tag"], on_change=lambda value: state.set_output_field("package_tag", value), width="100%")),
                _field("Output Quantity", rx.input(value=state.output_form["quantity"], on_change=lambda value: state.set_output_field("quantity", value), width="100%")),
                _field("Output Location", rx.input(value=state.output_form["location"], on_change=lambda value: state.set_output_field("location", value), width="100%")),
                _field("ATS Status", rx.input(value=state.output_form["ats_status"], on_change=lambda value: state.set_output_field("ats_status", value), width="100%")),
                columns=rx.breakpoints(initial="1", md="2", lg="4"), width="100%", spacing="3"),
            rx.hstack(rx.switch(checked=state.output_reconciled, on_change=state.set_output_reconciled_value), rx.text("Output tag created and reconciled")),
            rx.button("Record Output / Recheck Closure", on_click=state.save_output, background=ACCENT, color="white"),
            rx.divider(),
            rx.heading("Cancel Open Work Order", size="3"),
            rx.hstack(rx.input(placeholder="Required cancellation reason", value=state.cancel_reason, on_change=state.set_cancel_reason_value, width="100%"),
                      rx.button("Cancel Work Order", on_click=state.cancel, color_scheme="red", variant="outline"), width="100%"),
            width="100%", align="start", spacing="3"), width="100%"),
        rx.card(rx.vstack(rx.heading("Audit History", size="4"),
                          _table(["When", "Event", "Prior", "New", "Actor"], state.event_rows),
                          width="100%", align="start"), width="100%"),
        width="100%", spacing="4", align="start",
    )


def _configuration(state: type[ManufacturingWorkOrderState]) -> rx.Component:
    return rx.card(rx.vstack(
        rx.heading("Manufacturing Configuration", size="5"),
        rx.callout("These values are stored by tenant and facility. Admin access is required to change them.", icon="settings", color_scheme="teal", width="100%"),
        rx.grid(
            _field("ME Number Pattern", rx.input(value=state.config_form["number_pattern_me"], on_change=lambda value: state.set_config_field("number_pattern_me", value), width="100%")),
            _field("MP Number Pattern", rx.input(value=state.config_form["number_pattern_mp"], on_change=lambda value: state.set_config_field("number_pattern_mp", value), width="100%")),
            _field("Sequence Padding", rx.input(value=state.config_form["sequence_padding"], on_change=lambda value: state.set_config_field("sequence_padding", value), width="100%")),
            _field("ME Close Rule", rx.select(["RECONCILED OUTPUT TAG", "MANUAL"], value=state.config_form["me_close_rule"], on_change=lambda value: state.set_config_field("me_close_rule", value), width="100%")),
            _field("MP Close Rule", rx.select(["ATS LISTED", "RECONCILED OUTPUT TAG", "MANUAL"], value=state.config_form["mp_close_rule"], on_change=lambda value: state.set_config_field("mp_close_rule", value), width="100%")),
            _field("ATS Closed Statuses", rx.input(value=state.config_form["ats_closed_statuses"], on_change=lambda value: state.set_config_field("ats_closed_statuses", value), width="100%")),
            columns=rx.breakpoints(initial="1", md="2"), width="100%", spacing="3"),
        _field("Substitution Approver Roles", rx.input(value=state.config_form["substitution_approver_roles"], on_change=lambda value: state.set_config_field("substitution_approver_roles", value), width="100%")),
        rx.hstack(rx.switch(checked=state.prevent_self_approval, on_change=state.set_prevent_self_approval_value), rx.text("Prevent self-approval of substitutions")),
        rx.hstack(rx.switch(checked=state.allow_manual_reconciliation, on_change=state.set_allow_manual_reconciliation_value), rx.text("Allow manual reconciliation before Metrc API automation")),
        rx.button("Save Manufacturing Configuration", on_click=state.save_configuration, background=ACCENT, color="white"),
        width="100%", align="start", spacing="3"), width="100%")
