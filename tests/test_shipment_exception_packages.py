import pandas as pd

from qcc_reflex_pilot.data import build_shipment_exception_packages


def test_exception_package_builder_keeps_open_rejected_and_returned_rows():
    rows = []
    for state, is_open, is_exception in [
        ("Accepted", False, False),
        ("Shipped", True, False),
        ("Rejected", False, True),
        ("Returned", False, True),
    ]:
        rows.append({
            "manifest": f"manifest-{state.lower()}",
            "invoice_number": "invoice-1",
            "created_at": pd.Timestamp("2026-08-01"),
            "received_at": pd.Timestamp("2026-08-02"),
            "state": state,
            "destination_license": "RE000001",
            "destination_facility": "Test Retailer",
            "package_tag": f"tag-{state.lower()}",
            "item": "Diamond Bar Packaged 3.5g EA",
            "brand": "Clade9",
            "strain": "Diamond Bar",
            "sku_type": "3.5g Flower",
            "shipped_units": 12,
            "shipper_dollar_amount": 120,
            "is_demand": state == "Accepted",
            "is_open_shipment": is_open,
            "is_shipment_exception": is_exception,
        })

    result = build_shipment_exception_packages(pd.DataFrame(rows))

    assert set(result["State"]) == {"Shipped", "Rejected", "Returned"}
    assert set(result["Package Tag"]) == {
        "tag-shipped", "tag-rejected", "tag-returned"
    }

