"""Authenticated packaging operations; domain rules live in warehouse.py."""
import uuid
from datetime import date
import reflex as rx
from . import warehouse as service
from .packaging_inventory import packaging_items, packaging_suppliers


class WarehouseState(rx.State):
    message: str = ""
    _snapshot: dict = {}
    _items: list[dict] = []
    _content: bytes = b""
    _preview: dict = {}
    filename: str = ""
    ready: bool = False
    locations: list[list[str]] = []
    balances: list[list[str]] = []
    activities: list[list[str]] = []
    imports: list[list[str]] = []
    changes: list[list[str]] = []
    warnings: str = ""
    page: int = 0
    size: str = "10"
    view: str = "Balances"
    search: str = ""
    location: dict[str, str] = {"code":"", "name":"", "warehouse":"", "zone":"", "rack":"", "bin":"", "status":"ACTIVE"}
    activity: dict[str, str] = {"material_id":"", "action":"Receive", "quantity":"", "location":"UNASSIGNED", "destination":"", "lot":"", "expiration":"", "date":"", "unit_cost":"", "reference":"", "reason":"", "reversal_of":""}
    _pending: dict = {}
    _request_id: str = ""
    confirmation: str = ""

    async def _actor(self):
        from .qcc_reflex_pilot import DashboardState
        dashboard = await self.get_state(DashboardState)
        if not dashboard._require_active_session():
            raise ValueError("Sign in again before accessing warehouse records.")
        return dashboard.auth_email or dashboard.auth_name

    async def _reload(self):
        self._snapshot = await rx.run_in_thread(service.warehouse_snapshot)
        self._items = await rx.run_in_thread(packaging_items)
        self.locations = [[str(r.get(k,"")) for k in ("code","name","warehouse","zone","rack","bin","active")] for r in self._snapshot["locations"]]
        self.balances = [[str(r.get(k,"")) for k in ("material_id","location","lot","expiration","quantity")] for r in self._snapshot["balances"]]
        self.activities = [[str(r.get(k,"")) for k in ("activity_id","material_id","action","occurred_on","actor","reference","reason","reversed")] + ["; ".join(f"{leg['location']}: {float(leg['quantity']):+g}" for leg in r["details"]["legs"])] for r in self._snapshot["activities"]]
        self.imports = [[str(r.get(k,"")) for k in ("import_id","filename","actor","created_at","rows")] for r in self._snapshot["imports"]]
        from .qcc_reflex_pilot import DashboardState
        dashboard = await self.get_state(DashboardState)
        dashboard._packaging_registry = self._items
        dashboard.packaging_registry_loaded = True
        dashboard._packaging_supplier_registry = await rx.run_in_thread(packaging_suppliers)

    @rx.event
    async def refresh(self):
        try:
            await self._actor()
            await self._reload()
            self.message = "Warehouse records refreshed."
        except Exception as error:
            self.message = str(error)

    @rx.event
    async def enter_section(self, section: str):
        self.view = {"registry_import": "CSV Preview", "locations": "Locations", "inventory_activity": "Activity"}[section]
        self.page, self.search, self.message = 0, "", ""
        yield
        await self.refresh()

    @rx.event
    def set_view(self, value: str):
        self.view, self.page, self.search = value, 0, ""

    @rx.event
    def set_search(self, value: str):
        self.search, self.page = value, 0

    @rx.event
    def set_size(self, value: str):
        self.size, self.page = value, 0

    @rx.event
    def previous(self):
        self.page = max(0, self.page - 1)

    @rx.event
    def next(self):
        if (self.page + 1) * int(self.size) < len(self._rows()):
            self.page += 1

    def _rows(self):
        rows = {"Balances":self.balances,"Locations":self.locations,"Activity":self.activities,"Import History":self.imports,"CSV Preview":self.changes}.get(self.view, [])
        return [r for r in rows if self.search.casefold() in " ".join(r).casefold()]

    @rx.var
    def visible_rows(self) -> list[list[str]]:
        return self._rows()[self.page*int(self.size):(self.page+1)*int(self.size)]

    @rx.var
    def row_summary(self) -> str:
        return f"Page {self.page+1} • {len(self._rows())} records"

    @rx.var
    def headers(self) -> list[str]:
        return {"Balances":["Material ID","Location","Lot","Expiration","Quantity"],"Locations":["Code","Name","Warehouse","Zone","Rack","Shelf / Bin","Active"],"Activity":["Activity ID","Material ID","Activity","Date","Employee","Reference","Reason","Reversed","Location Changes (item UOM)"],"Import History":["Import ID","File","Employee","Imported","Reviewed Items"],"CSV Preview":["Material ID","Action","Field","Before","After"]}[self.view]

    @rx.event
    def location_field(self, key: str, value: str):
        self.location[key] = value

    @rx.event
    async def load_location(self):
        try:
            await self._actor()
            await self._reload()
            code = self.location["code"].strip().upper()
            record = next((r for r in self._snapshot["locations"] if r["code"] == code), None)
            if not record:
                raise ValueError("Location not found. Enter new details to create it.")
            self.location = {k:str(record.get(k,"")) for k in self.location}
            self.location["status"] = "ACTIVE" if record["active"] else "INACTIVE"
            self.message = "Location loaded for editing. Keep its code unchanged."
        except Exception as error:
            self.message = str(error)

    @rx.event
    async def save_location(self):
        try:
            actor = await self._actor()
            code = await rx.run_in_thread(lambda: service.save_location(dict(self.location), actor))
            await self._reload()
            self.message = f"Location {code} saved."
        except Exception as error:
            self.message = str(error)

    @rx.event
    def activity_field(self, key: str, value: str):
        self.activity[key] = value
        self._pending, self.confirmation = {}, ""

    @rx.event
    async def preview_activity(self):
        self._pending, self.confirmation = {}, ""
        try:
            await self._actor()
            await self._reload()
            form = dict(self.activity)
            form["date"] = form["date"] or date.today().isoformat()
            form["material_id"] = form["material_id"].strip().upper()
            form["location"] = form["location"].strip().upper()
            form["destination"] = form["destination"].strip().upper()
            item = next((r for r in self._items if r["material_id"] == form["material_id"]), None)
            if not item:
                raise ValueError("Scan or enter a registered Material ID.")
            balance = sum(r["quantity"] for r in self._snapshot["balances"] if r["material_id"] == form["material_id"] and r["location"] == form["location"] and r["lot"] == form["lot"].strip() and r["expiration"] == form["expiration"].strip())
            form["expected"] = str(balance)
            if form["action"] == "Reverse Activity":
                detail = "Reverse original activity " + form["reversal_of"]
            else:
                legs = service.activity_legs(form["action"], service.number(form["quantity"]), form["location"], form["destination"], balance)
                detail = "; ".join(f"{loc}: {delta:+g} {item['uom']}" for loc,delta in legs)
            self._pending, self._request_id = form, str(uuid.uuid4())
            self.confirmation = f"{item['item']} — {detail}. Confirm to post to the permanent ledger."
        except Exception as error:
            self.message = str(error)

    @rx.event
    async def confirm_activity(self):
        try:
            actor = await self._actor()
            if not self._pending:
                raise ValueError("Preview the activity first.")
            saved = await rx.run_in_thread(lambda: service.post_activity(self._pending, actor, self._request_id))
            self._pending, self.confirmation = {}, ""
            await self._reload()
            self.message = f"Activity saved: {saved}"
        except Exception as error:
            self.message = str(error)

    @rx.event
    async def upload(self, files: list[rx.UploadFile]):
        self.ready, self.changes, self._preview = False, [], {}
        try:
            await self._actor()
            if len(files) != 1:
                raise ValueError("Select one CSV file.")
            self._content = await files[0].read()
            self.filename = files[0].filename or "registry.csv"
            items = await rx.run_in_thread(packaging_items)
            self._preview = service.preview_import(self._content, items)
            self.changes = [[r["material_id"],r["action"],c["field"],str(c["before"]),str(c["after"])] for r in self._preview["rows"] for c in r["changes"]]
            self.warnings = "\n".join(self._preview["errors"] + self._preview["warnings"])
            self.ready = not self._preview["errors"]
            self.view, self.page, self.search = "CSV Preview", 0, ""
            self.message = f"{len(self._preview['rows'])} items reviewed; {len(self.changes)} field changes. Quantities are ignored. Nothing saved yet."
        except Exception as error:
            self.message = str(error)

    @rx.event
    async def apply_csv(self):
        try:
            actor = await self._actor()
            if not self.ready:
                raise ValueError("Upload and review a valid CSV first.")
            await rx.run_in_thread(lambda: service.apply_import(self._preview, self._content, self.filename, actor))
            self.ready = False
            await self._reload()
            self.message = "Registry updates saved. Quantities and omitted items were preserved."
        except Exception as error:
            self.message = str(error)


def warehouse_workspace(section: str):
    """Three focused panels share the existing authenticated warehouse state."""
    state = WarehouseState
    def field(label, key, location=False):
        values = state.location if location else state.activity
        event = state.location_field if location else state.activity_field
        return rx.vstack(rx.text(label, size="2"), rx.input(value=values[key], on_change=lambda value: event(key,value), width="100%"), spacing="1")
    if section == "locations":
        title = "Locations"
        description = "Define storage locations and review stock by location. Default Location is only a suggestion; use Inventory Activity to move stock."
        views = ["Locations", "Balances"]
        controls = [
        rx.grid(*[field(label,key,True) for label,key in [("Unique code / scan code","code"),("Name","name"),("Warehouse","warehouse"),("Zone","zone"),("Rack","rack"),("Shelf / Bin","bin")]], columns=rx.breakpoints(initial="1",md="3"), width="100%"),
        rx.select(["ACTIVE","INACTIVE"], value=state.location["status"], on_change=lambda v: state.location_field("status",v)),
        rx.hstack(rx.button("Load Location for Editing",on_click=state.load_location),rx.button("Save Location",on_click=state.save_location)),
        ]
    elif section == "inventory_activity":
        title = "Inventory Activity"
        description = "Receive adds stock. Issue consumes it. Transfer moves stock without changing the total. Physical Count records the actual count for one location and lot. Preview before confirming."
        views = ["Activity", "Balances"]
        reverse = state.activity["action"] == "Reverse Activity"
        controls = [
        rx.select(service.ACTIVITIES,value=state.activity["action"],on_change=lambda v: state.activity_field("action",v)),
        rx.grid(
            field("Material ID (scan or type)","material_id"),
            rx.cond(reverse, field("Original Activity ID","reversal_of")),
            rx.cond(~reverse, field("Source / receiving location (scan or type)","location")),
            rx.cond(state.activity["action"] == "Transfer Location", field("Destination location","destination")),
            rx.cond(~reverse, field(rx.cond(state.activity["action"] == "Physical Count", "Actual quantity counted", "Quantity"),"quantity")),
            rx.cond(~reverse, field("Supplier lot","lot")),
            rx.cond(~reverse, field("Expiration YYYY-MM-DD","expiration")),
            field("Activity date YYYY-MM-DD (blank = today)","date"),
            rx.cond(~reverse, field("Unit cost","unit_cost")),
            field("PO / production reference","reference"),
            field("Reason / note (required)","reason"),
            columns=rx.breakpoints(initial="1",md="3"),width="100%"),
        rx.button("Preview Activity",on_click=state.preview_activity),
        rx.cond(state.confirmation != "",rx.vstack(rx.text(state.confirmation),rx.button("Confirm & Record Activity",on_click=state.confirm_activity))),
        rx.text("Activity history shows the latest 500 records. Older records remain in the database."),
        ]
    elif section == "registry_import":
        title = "Registry Import"
        description = "Update item details—not quantities. Blank fields preserve existing values, missing items are not deleted, and new items start at zero."
        views = ["CSV Preview", "Import History"]
        controls = [
        rx.flex(
            rx.badge("1", radius="full", color_scheme="teal", size="2"),
            rx.text("Choose the updated registry CSV", weight="bold"),
            rx.badge("2", radius="full", color_scheme="teal", size="2"),
            rx.text("Preview and review every change", weight="bold"),
            rx.badge("3", radius="full", color_scheme="teal", size="2"),
            rx.text("Apply the reviewed updates", weight="bold"),
            gap="2", align="center", wrap="wrap", width="100%",
        ),
        rx.upload(
            rx.vstack(
                rx.box(
                    rx.icon("cloud-upload", size=42, color="#0f8f92"),
                    background="#e6f7f6", border_radius="999px", padding="1rem",
                ),
                rx.heading("Drop the packaging registry CSV here", size="4", color="#111827"),
                rx.text("or click anywhere in this box to browse your computer", color="#64748b"),
                rx.button(
                    rx.icon("folder-open", size=18),
                    "Choose CSV File",
                    background="#14969b", color="white", size="3",
                ),
                rx.text("CSV files only • One file at a time • Maximum 5 MB", size="2", color="#64748b"),
                spacing="3", align="center", justify="center", width="100%",
            ),
            id="warehouse_csv", accept={"text/csv":[".csv"]}, max_files=1,
            border="3px dashed #14969b", border_radius="14px", padding="2.25rem",
            background="#f6fffe", width="100%", min_height="250px",
            cursor="pointer", _hover={"background":"#ebfbfa", "border_color":"#0f777a"},
        ),
        rx.flex(
            rx.icon("file-check", size=20, color="#14969b"),
            rx.text("Selected file:", weight="bold"),
            rx.foreach(rx.selected_files("warehouse_csv"), lambda name: rx.badge(name, color_scheme="teal", size="2")),
            rx.button("Clear", variant="ghost", size="2", on_click=rx.clear_selected_files("warehouse_csv")),
            gap="2", align="center", wrap="wrap", width="100%",
        ),
        rx.hstack(
            rx.button(rx.icon("scan-search", size=18), "Preview CSV Changes", on_click=state.upload(rx.upload_files(upload_id="warehouse_csv")), size="3"),
            rx.button(rx.icon("database", size=18), "Apply Reviewed Updates", on_click=state.apply_csv, disabled=~state.ready, size="3", background="#14969b", color="white"),
            wrap="wrap",
        ),
        rx.text(state.warnings,white_space="pre-wrap"),
        rx.text("Import history shows the latest 100 files. Older records remain in the database."),
        ]
    else:
        raise ValueError(f"Unknown warehouse section: {section}")
    return rx.vstack(
        rx.heading(title, size="5"),
        rx.text(description),
        rx.button("Refresh Records", on_click=state.refresh),
        rx.cond(state.message != "", rx.callout(state.message, icon="info")),
        *controls,
        rx.separator(),
        rx.select(views,value=state.view,on_change=state.set_view),
        rx.input(placeholder="Search material, location, reference or activity ID",value=state.search,on_change=state.set_search,width="100%"),
        rx.box(rx.table.root(rx.table.header(rx.table.row(rx.foreach(state.headers,lambda h:rx.table.column_header_cell(h,white_space="normal",min_width="120px")))),rx.table.body(rx.foreach(state.visible_rows,lambda row:rx.table.row(rx.foreach(row,lambda cell:rx.table.cell(cell,white_space="normal"))))),width="100%"),width="100%",overflow_x="auto"),
        rx.hstack(rx.select(["10","25","50"],value=state.size,on_change=state.set_size),rx.button("Previous",on_click=state.previous),rx.text(state.row_summary),rx.button("Next",on_click=state.next)),
        width="100%",spacing="3",on_mount=state.enter_section(section),
    )
