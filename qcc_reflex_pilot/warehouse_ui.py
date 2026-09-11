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
            self.view, self.page = "CSV Preview", 0
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


def warehouse_workspace():
    state = WarehouseState
    def field(label, key, location=False):
        values = state.location if location else state.activity
        event = state.location_field if location else state.activity_field
        return rx.vstack(rx.text(label, size="2"), rx.input(value=values[key], on_change=lambda value: event(key,value), width="100%"), spacing="1")
    return rx.vstack(
        rx.heading("Warehouse Activity & Registry Updates", size="5"),
        rx.text("Receive adds stock. Issue consumes stock. Transfer moves stock without changing the total. Physical Count adjusts the selected location and lot to the counted quantity. Default Location does not move stock."),
        rx.button("Refresh Warehouse Records", on_click=state.refresh),
        rx.callout(state.message, icon="info"),
        rx.heading("Locations", size="4"),
        rx.grid(*[field(label,key,True) for label,key in [("Unique code / scan code","code"),("Name","name"),("Warehouse","warehouse"),("Zone","zone"),("Rack","rack"),("Shelf / Bin","bin")]], columns=rx.breakpoints(initial="1",md="3"), width="100%"),
        rx.select(["ACTIVE","INACTIVE"], value=state.location["status"], on_change=lambda v: state.location_field("status",v)),
        rx.hstack(rx.button("Load Location for Editing",on_click=state.load_location),rx.button("Save Location",on_click=state.save_location)),
        rx.heading("Record Inventory Activity",size="4"),
        rx.select(service.ACTIVITIES,value=state.activity["action"],on_change=lambda v: state.activity_field("action",v)),
        rx.grid(*[field(label,key) for label,key in [("Material ID (scan or type)","material_id"),("Source / receiving location (scan or type)","location"),("Destination (transfers only)","destination"),("Quantity / actual counted quantity","quantity"),("Supplier lot","lot"),("Expiration YYYY-MM-DD","expiration"),("Activity date YYYY-MM-DD (blank = today)","date"),("Unit cost","unit_cost"),("PO / production reference","reference"),("Reason / note (required)","reason"),("Original Activity ID (reversal only)","reversal_of")]],columns=rx.breakpoints(initial="1",md="3"),width="100%"),
        rx.button("Preview Activity",on_click=state.preview_activity),
        rx.cond(state.confirmation != "",rx.vstack(rx.text(state.confirmation),rx.button("Confirm & Record Activity",on_click=state.confirm_activity))),
        rx.heading("Update Registry from CSV",size="4"),
        rx.text("Metadata only. Blank fields preserve existing values. Missing items are not deleted. New items start at zero. Review changes before applying; use inventory activity for all quantity changes."),
        rx.upload(rx.text("Select or drop one registry CSV"),id="warehouse_csv",accept={"text/csv":[".csv"]},max_files=1),
        rx.hstack(rx.button("Preview CSV",on_click=state.upload(rx.upload_files(upload_id="warehouse_csv"))),rx.button("Apply Reviewed Updates",on_click=state.apply_csv,disabled=~state.ready)),
        rx.text(state.warnings,white_space="pre-wrap"),
        rx.select(["Balances","Locations","Activity","Import History","CSV Preview"],value=state.view,on_change=state.set_view),
        rx.input(placeholder="Search material, location, reference or activity ID",value=state.search,on_change=state.set_search,width="100%"),
        rx.text("Activity shows the latest 500 records; import history shows the latest 100. Older records are retained in the database."),
        rx.box(rx.table.root(rx.table.header(rx.table.row(rx.foreach(state.headers,lambda h:rx.table.column_header_cell(h,white_space="normal",min_width="120px")))),rx.table.body(rx.foreach(state.visible_rows,lambda row:rx.table.row(rx.foreach(row,lambda cell:rx.table.cell(cell,white_space="normal"))))),width="100%"),width="100%",overflow_x="auto"),
        rx.hstack(rx.select(["10","25","50"],value=state.size,on_change=state.set_size),rx.button("Previous",on_click=state.previous),rx.text(state.row_summary),rx.button("Next",on_click=state.next)),
        width="100%",spacing="3",on_mount=state.refresh,
    )
