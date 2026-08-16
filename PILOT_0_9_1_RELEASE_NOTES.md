# QCC Control Tower Reflex 0.9.1

Offline validation release. This version is not deployed automatically.

## Sales & Demand Planning

- Only the visible subtab renders, reducing browser and WebSocket work.
- Opened Sales datasets remain available during the session for faster returns.
- SKU Planning & Coverage and Stockouts default to **Active Products Only**.
- Lifecycle recommendations use last customer shipment, current inventory, and
  committed production:
  - Active: shipped within 90 days, or has inventory/committed production.
  - Dormant: last shipped 91-180 days ago.
  - Retirement Candidate: over 180 days without inventory or a commitment.
  - Seasonal and Retired remain manual business decisions.
- Last Shipped and Lifecycle Status are visible in planning tables and exports.

## Quality Assurance

- Supabase QA reads retry once after a transient SSL/idle disconnect.
- **Reconnect & Reload QA** gives the user an explicit recovery action.
- Non-finite THC/terpene values are removed from chart payloads.
- Only the selected Cultivation, Manufacturing, or Labels view renders.

## Compliance Labels

- Direct package-tag and harvest search remains available.
- Guided Operation, Brand, Strain, and SKU browsing replaces the long random
  compliance-record dropdown.
- Includes a classified catalog of all 30 supplied `.nlbl` template names.
- Templates with unresolved brand/SKU details are visibly marked Needs Review.
- Native `.nlbl` variable injection and Zebra printing are deferred until the
  installed ZebraDesigner/NiceLabel product and edition are confirmed.

## Validation

- Python syntax validation completed.
- Lifecycle thresholds were exercised with representative recent and dormant
  shipment records.
- A full Reflex compile should be run from the extracted folder using its local
  `.venv` before user acceptance testing.
