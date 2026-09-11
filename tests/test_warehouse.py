import pytest
from qcc_reflex_pilot.warehouse import preview_import, item_version, activity_legs, number
from qcc_reflex_pilot.packaging_inventory import packaging_seed_items


def test_import_never_uses_quantity_and_preserves_blanks():
    item = packaging_seed_items()[0]
    content = f'Material ID,Packaging Inventory Item,On Hand,Secondary Supplier\n{item["material_id"]},NEW DESCRIPTION,999999,\n'.encode()
    result = preview_import(content, [item])
    assert not result["errors"]
    assert result["rows"][0]["record"]["on_hand"] == item["on_hand"]
    assert result["rows"][0]["record"]["secondary_vendor"] == item["secondary_vendor"]
    assert len(result["rows"]) == 1


def test_stale_export_is_rejected():
    item = packaging_seed_items()[0]
    token = item_version(item)
    changed = dict(item, item="CHANGED SINCE EXPORT")
    content = f'Material ID,Packaging Inventory Item,Registry Version\n{item["material_id"]},NEW,{token}\n'.encode()
    assert preview_import(content,[changed])["errors"]


def test_quantity_changes_do_not_invalidate_metadata_version():
    item = packaging_seed_items()[0]
    assert item_version(item) == item_version(dict(item,on_hand=999))


def test_duplicate_rows_fail_entire_preview():
    content = b'Material ID,Packaging Inventory Item\nPKG-9000,TEST\nPKG-9000,TEST2\n'
    assert preview_import(content,[])["errors"]


def test_new_item_zero_quantity():
    result = preview_import(b'Material ID,Packaging Inventory Item,On Hand\nPKG-9000,TEST,200\n',[])
    assert not result["errors"]
    assert result["rows"][0]["record"]["on_hand"] == 0


@pytest.mark.parametrize("bad",["nan","inf","-1","abc"])
def test_quantity_validation(bad):
    with pytest.raises(ValueError):
        number(bad)


def test_transfer_preserves_total():
    legs = activity_legs("Transfer Location",25,"A","B",100)
    assert legs == [("A",-25),("B",25)]
    assert sum(delta for _,delta in legs) == 0


def test_count_records_difference_including_zero():
    assert activity_legs("Physical Count",80,"A","",100) == [("A",-20)]
    assert activity_legs("Physical Count",0,"A","",100) == [("A",-100)]


@pytest.mark.parametrize("action",["Issue to Production","Scrap / Damage","Transfer Location"])
def test_no_negative_stock(action):
    with pytest.raises(ValueError):
        activity_legs(action,101,"A","B",100)


def test_same_location_transfer_rejected():
    with pytest.raises(ValueError):
        activity_legs("Transfer Location",10,"A","A",100)


class FakeConnection:
    def __init__(self, duplicate=False):
        self.calls = []
        self.duplicate = duplicate
        self.last = ""
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        self.committed = kind is None

    def execute(self, sql, params=()):
        self.last = sql
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        if "WHERE activity_id=" in self.last:
            return (1,) if self.duplicate else None
        return (True,)


def configure_ledger(monkeypatch, duplicate=False):
    from qcc_reflex_pilot import warehouse
    conn = FakeConnection(duplicate)
    monkeypatch.setattr(warehouse, "initialize", lambda: None)
    monkeypatch.setattr(warehouse, "database_url", lambda: "TEST")
    monkeypatch.setattr(warehouse.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(warehouse, "packaging_items", lambda c: [{"material_id":"PKG-1","status":"ACTIVE","uom":"EACH"}])
    monkeypatch.setattr(warehouse, "_balances", lambda c,m: [{"location":"A","lot":"","expiration":"","quantity":100}])
    return warehouse,conn


def form(action):
    from datetime import date
    return dict(material_id="PKG-1", action=action, date=date.today().isoformat(),
                reason="TEST", location="A", destination="B", quantity=25, expected=100)


def test_paired_transfer_is_one_transaction(monkeypatch):
    warehouse,conn = configure_ledger(monkeypatch)
    warehouse.post_activity(form("Transfer Location"),"employee","request-1")
    inserts = [params for sql,params in conn.calls if "INSERT INTO qcc_packaging_inventory_transactions" in sql]
    assert [p[3] for p in inserts] == [-25,25]
    assert all(p[-1] == "request-1" for p in inserts)
    assert conn.committed


def test_duplicate_confirmation_does_not_post_twice(monkeypatch):
    warehouse,conn = configure_ledger(monkeypatch,True)
    warehouse.post_activity(form("Receive"),"employee","request-1")
    assert not any("INSERT" in sql for sql,_ in conn.calls)


def test_stale_count_does_not_write(monkeypatch):
    warehouse,conn = configure_ledger(monkeypatch)
    pending = form("Physical Count")
    pending["expected"] = 99
    with pytest.raises(ValueError,match="Stock changed"):
        warehouse.post_activity(pending,"employee","request-1")
    assert not conn.committed
    assert not any("INSERT" in sql for sql,_ in conn.calls)
