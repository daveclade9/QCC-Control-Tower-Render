# QCC Control Tower - Reflex Inventory & Production Pilot 0.8.9

Version 0.8.9 makes Sales & Demand Planning tab transitions immediate while
their selected datasets finish loading, places the Inventory table weight
selector directly above each table, keeps summary weights in pounds, and uses
wider horizontally scrollable inventory tables with fully readable headers.

Version 0.8.8 removes the Clade9 wordmark from the authentication screen while
preserving the approved signed-in application styling and all Version 0.8.7
performance improvements.

Version 0.8.7 stabilized sortable inventory headers, removed the full dashboard
rebuild from plan saving, and makes primary workspace changes display
immediately while their optional data updates. Saved Plans and Production
Calendar refresh from their small production-only tables when opened.

Version 0.8.6 added memory-safe per-user state, server-side inventory and
transfer paging, and on-demand Sales & Demand module loading. It retains the
0.8.5 authentication, inventory classifications, production planning, and
Reflex 0.9.8 cloud runtime while substantially reducing WebSocket payloads.

Version 0.8.5 updated the pinned Reflex runtime from 0.9.7 to 0.9.8 for
compatibility with Reflex Cloud's separated frontend and backend services. It
preserves the cloud-safe port configuration introduced in Version 0.8.4 and all
existing application functionality. See `PILOT_0_8_5_RELEASE_NOTES.md`.

This is a separate parity pilot for comparing a faster Reflex interface with
Streamlit QCC Control Tower Version 81.4. It does not replace the production
Streamlit application.

## Included in Pilot 0.7.3

- SKU Planning velocity windows for 1 Week, 30 Days, 90 Days, and All Time
- Current package count replaces First Shipped and History Status
- Potential WIP hover summaries show package count, age range, and lot-size range
- A dedicated info action opens the closable package-detail window
- Craft Kings Hybrid, Indica, and Sativa Blend pre-roll plans support multiple
  source-strain selections with Select All and Clear controls
- Successful saves always release the loading state, confirm the Plan ID, and
  open Saved Plans
- Saved Plans no longer inherit SKU Planning's global product filters and have
  dedicated search and status controls

- Potential Matching WIP is highlighted in teal in SKU Planning & Coverage
- Committed WIP is highlighted in blue for fast visual separation
- Clicking a Potential Matching WIP value opens Production Planning directly
- Build & Compare is prefilled with the selected Brand, Strain, SKU Type,
  formulation defaults, and compatible source-lot list
- The interactive planning table retains sorting controls, global filters,
  CSV export, and 25-row pagination

- Product Target retains SKU Type while the user changes among compatible strains
- Quick Bulk Flower, Trim, Shake, and Mids/Smalls source-lot filters
- Broad-pool filters for source strain, location, minimum weight, search, and sort
- Selected source lots remain selected while source filters change
- Saved plans can be reopened in Build & Compare and fully amended
- Saved plans can be duplicated, converted to inventory-free templates, or deleted
- Plan deletion releases committed WIP after one confirmation
- Production calendar can be downloaded as an Outlook/Google-compatible `.ics` file

- Production Planning builder migrated from Streamlit with product targets,
  compatible WIP selection, batch-weight controls, formulations, projected
  output, scenario comparison, and plan saving
- Shared Supabase production-plan writes with transaction-level WIP locking to
  prevent simultaneous planners from committing the same available material
- Saved production-plan summaries and portable production calendar
- Per-table **Summarize matching SKUs** controls throughout Inventory
- Summary mode groups Brand, Strain, and SKU Type; totals units and weight;
  shows package-tag counts; and retains the oldest age for risk visibility
- Standardized Inventory columns: Brand, Strain, SKU Type, Unit Count, Total
  Weight, Age, Location, QA Status, and Metrc Tag
- Every Inventory table column is sortable
- Every Inventory subtab includes filter-responsive record, unit, and weight
  summaries
- Inventory Snapshot values are separated into five responsive cards

- Executive inventory scope now defaults to positive-quantity QCC-owned
  inventory and excludes Retention Storage and Secure Waste
- Active CPG and Retention/Stability inventory are reported separately
- Pre-WIP now displays both package count and calculated weight
- Inventory includes a dedicated Ownership Status filter
- CPG Inventory includes filtered package, unit, and weight summaries plus a
  retention/stability toggle
- WIP & Pre-WIP includes filtered package and weight summaries

- New Executive Dashboard as the default workspace
- Filter-responsive 30-day Business Pulse metrics
- Facility and ownership scope controls for inventory and action metrics
- Color-coded Inventory Position, Ownership, and Immediate Attention cards
- Sortable, searchable Stockouts and Low Inventory action queue with CSV export

- Aging Risk CPG parity with packaged, positive-quantity inventory only
- Aging Risk Bulk parity using Streamlit's eligible non-branded stages
- Specialized Needs Review columns including Material Type and Review Reason

- Exact active CPG parity by excluding negative-quantity packages
- Bulk Inventory parity by routing Needs Review and Secure Waste elsewhere
- Physical WIP visibility even when a lot is fully committed
- Available/Potential WIP still subtracts active production commitments

- Authoritative CPG eligibility published by Streamlit Version 81.4
- CPG reconciliation count and classification-version indicator
- Retention samples included only when Streamlit marks them as eligible
- Clear warning when the latest snapshot predates Version 81.4

- Version 81.2-aligned historical demand and stockout calculations
- Complete Top Historical SKUs table with pagination, search, and sorting
- Fourteen-column SKU Planning & Coverage table
- Familiar Sales & Demand Planning and Inventory workspace navigation
- Clearly labeled Brand, Strain, SKU Type, and current-view filters
- Production Planning workspace with expandable saved-plan summaries
- Planned SKU output and committed Metrc source-tag details
- Month production calendar and agenda view
- Customer Shipment History
- Shipment Exceptions
- Transfer Data and import history
- CSV downloads for operational tables
- Shared Supabase reads using the current QCC tables
- Package-level CPG, Bulk, WIP, Pre-WIP, Aging, All Inventory, and Needs Review views
- Product-specific Potential Matching WIP derived from the shared package snapshot
- Dedicated Production Stage, License, QA Status, Category, and Location filters
- High-performance operational grids with explicit column widths and frozen identifiers
- Exact Metrc package lookup with Clear control and all fields displayed without pagination
- Current-package and Potential-WIP drill-down for one selected SKU
- Filtered record counts and CSV downloads on every Inventory subtab

## Shared-data safety boundary

Streamlit remains the system of record for Metrc uploads, inventory snapshots,
reservations, employee access, and administration. Reflex can create production
plans in the same Supabase tables used by Streamlit. Plan creation revalidates
the latest inventory and active commitments inside a locked database
transaction before saving.

Streamlit Version 81.4 publishes the classified package detail and the CPG
eligibility flags. Reflex uses that shared detail for current inventory and WIP
matching.

## First-time setup on Windows

1. Extract the ZIP to a short path such as `C:\QCC_Reflex_Pilot_V0_7_2`.
2. Double-click **Setup QCC Reflex Pilot.bat**.
3. Open the new `.env` file in Notepad or VS Code.
4. Replace the example value with the pooled Supabase database connection used
   by QCC Control Tower.
5. Add the Supabase project URL and publishable key. Keep
   `QCC_PUBLIC_APP_URL=http://localhost:3000` for local testing.
6. Enable Google and Azure providers in Supabase Auth and allow
   `http://localhost:3000/auth/callback` as a redirect URL.
7. Save `.env`, then double-click **Start QCC Reflex Pilot.bat**.
8. Open `http://localhost:3000` if the browser does not open automatically.

Streamlit Version 81.4 can remain open at `http://localhost:8502` while Reflex
uses ports 3000 and 8000.

## Password safety

- Never email, upload, or commit `.env`.
- URL-encode special password characters inside the PostgreSQL connection URL.
- `.env`, `.venv`, generated web files, and logs are excluded by `.gitignore`.

## Employee access

- Google or Microsoft verifies identity; it does not grant QCC access.
- The verified email must exactly match an active employee in the existing
  `sales_user_profiles` table managed through Streamlit Team & Access.
- QCC sessions are opaque, server-validated, expire after 12 hours, and can be
  revoked by deactivating the employee profile.
- Before online deployment, change `QCC_PUBLIC_APP_URL` to the final HTTPS app
  address and add `<app-address>/auth/callback` to Supabase Auth redirect URLs.

## Demo mode

Without a valid database URL, the pilot opens with clearly marked demo data so
the layout can be inspected without touching Supabase.
