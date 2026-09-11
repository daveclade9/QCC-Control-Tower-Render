# Packaging warehouse workflow — 0.9.6.90

Open Materials & Procurement → Warehouse Activity & CSV. Refresh Warehouse Records first.

## Registry updates

Upload the edited registry CSV and click Preview CSV. Review the field-by-field changes and warnings, then Apply Reviewed Updates. IDs are the matching key. No quantities are imported, no missing items are deleted, and blank fields preserve existing values. Newly created items start with zero stock. File bytes, actor, time and changed fields are retained. Reimporting the identical file is blocked. New exports include a Registry Version to detect stale edits; older exports cannot detect edits made before the preview, so review carefully.

## Locations

Create unique location codes with warehouse, zone, rack and shelf/bin details. Enter an existing code and Load Location for Editing to update it. Codes identify locations permanently; entering a different code creates another location. Deactivation requires zero stock. Default Location remains a suggestion, not a movement of stock. Workbook opening balances start at UNASSIGNED; existing receipt locations are retained. Transfer stock into the real bins after identifying it physically.

## Quantities

Receive and Return from Production add stock. Issue to Production consumes it; use Transfer Location instead when stock is merely moved to production staging. Scrap / Damage subtracts stock. Transfers create paired entries atomically. Physical Count accepts the actual count for the selected location, supplier lot and expiration, and posts the difference. Count each lot separately. Stock changes after a count preview require another preview. Negative stock is blocked.

Enter a reason, preview, then confirm. Scan guns operating as keyboards can enter the Material ID and location codes; scanning alone never confirms an activity. Use Reverse Activity with the original Activity ID to correct a mistake without erasing its history. Reversal is blocked if subsequent consumption makes it impossible. Legacy receipts remain in balances but cannot be reversed by the new Activity ID workflow; use a documented count adjustment when needed.

Activity lists show the latest 500 records and imports the latest 100, with search and 10/25/50 paging. Older records remain in the database. Dates entered as blank default to the application server's current date; physical counts must use that date.

## Deployment and SaaS boundary

No quantity updates run during deployment. Idempotent schema setup runs when warehouse services are first used. No live inventory CSV has been applied as part of development. Test a receipt, transfer, count and reversal in staging before operational rollout.

Domain rules are separated from Reflex, with server-validated employee identity and atomic writes. This remains the existing single-company QCC database. A multi-tenant SaaS release still requires a tenant-isolated repository, tenant-scoped authorization and migration management; this is not presented as completed tenant isolation.
