# QCC Control Tower Reflex 0.9.3

## 0.9.3.13 cold-load, plan deletion, and analyte refinement

- Reuses one Supabase connection for the latest Inventory snapshot, SKU totals,
  and package detail instead of paying for three separate cold connections.
- Reuses one Supabase connection for all Production Planning reads.
- Removes confirmed single and mass plan deletions from the interface immediately,
  then reconciles the small Production dataset in the background.
- Classifies Ancymidol, Ethephon, Flurprimidol, Phosmet, and Piperonyl Butoxide
  as Pesticides; Chromium as a Heavy Metal; and THCVA as a Cannabinoid.

## 0.9.3.12 Reflex compile correction

- Corrects the analyte-category badge expression so Reflex can compile the QA
  page successfully.
- Preserves all compact-summary and analyte-filter changes from 0.9.3.11.

## 0.9.3.11 analyte classification and compact summary build

- Replaces the paginated compliance-summary popup grid with a compact,
  non-paginated two-column summary card.
- Keeps the downloaded compliance summary fixed to one physical 4-by-6 page.
- Classifies detailed analytes as Cannabinoids, Terpenes, Mycotoxins, Heavy
  Metals, Pesticides, Microbials, Water Activity, or Other / Needs Review.
- Adds an analyte-category filter, visible category counts, and a filtered-row
  count without hiding unfamiliar laboratory tests.
- Preserves the concurrent startup and shared-cache behavior from 0.9.3.9.

## 0.9.3.10 compliance summary presentation build

- Restores the missing regular-expression dependency used by Inventory-backed
  compliance searches.
- Produces a compact, fixed 4-by-6-inch one-page compliance summary.
- Removes the redundant Printable Record Preview table.
- Converts detailed analyte records to the row matrix required by the sortable
  data table, so reported rows display visible values.
- Preserves the 0.9.3.9 concurrent cold-start improvements.

## 0.9.3.9 concurrent cold-start and safe label-input build

- Separates the compliance-search draft from submitted search state, so typing
  or pasting a Metrc tag cannot evaluate or render result rows.
- Runs the actual lookup only after Find and Preview is pressed.
- Warms Inventory, published Sales, and QA concurrently for the first user.
- Serializes the shared Inventory/Production context build so concurrent warm-up
  tasks reuse it rather than sending duplicate Supabase queries.
- Keeps the fast shared cache path used by subsequent users.

## 0.9.3.8 protected QA tag-search build

- Shows the selected compliance record and printable summary immediately from
  the already-loaded QA snapshot.
- Moves the full analyte-history query into a protected background task.
- Converts analyte connection, timeout, and data errors into an ordinary status
  message instead of Reflex's generic website-administrator error.
- Adds visible progress to Find and Preview and analyte-detail loading.

## 0.9.3.7 Potential WIP navigation recovery build

- Opens Production Planning immediately with the selected Brand, Strain, and
  SKU Type from SKU Planning & Coverage.
- Keeps all Inventory Brand, Strain, and SKU filters unchanged.
- Removes the duplicate synchronous Supabase read from the Potential Matching
  WIP click.
- Warms Saved Plans and Production Calendar through the existing protected
  background loader, preventing a temporary database failure from breaking the
  active session.

## 0.9.3.6 compliance label direct-search build

- Preserves the verified 0.9.3.5 Inventory, Transfer, and QA loading path.
- Makes Direct Package or Harvest Search an explicit Find and Preview action.
- Searches lab/COA packages first, then falls back to the authoritative current
  Inventory snapshot when a Metrc tag has no associated COA record.
- Automatically selects the matching approved NiceLabel catalog entry when one
  exists; otherwise it selects General Compliance Summary.
- Opens a visible compliance-summary dialog immediately after selection and
  clearly distinguishes an associated lab/COA record from an Inventory-only
  general summary.
- Allows Inventory-only records to download a print-ready HTML summary without
  requiring missing laboratory potency or expiration fields.
- Includes Brand, SKU Type, Category, Location, and Record Source in general
  compliance summaries.

## 0.9.3.5 performance and QA potency corrective build

- Removes Sales velocity, lifecycle, customer, exception, and Transfer table
  calculations from the Inventory login path.
- Reads Inventory packages, Production plans, and Production templates
  concurrently after the latest snapshot is identified.
- Restores the proven flexible Total THC and Total Terpenes test-name match so
  Metrc formatting variations populate the consistency charts, average/range
  table, and matching package potency columns.
- Maps compliance test families to finished SKU families. Cultivation Flower
  results, for example, remain visible for 1g, 3.5g, 7g, 14g, and 28g Flower
  selections rather than requiring the lab package's raw SKU label to match.
- Restores the verified Inventory state delivery used before the 0.9.3.4
  regression while retaining backend-only Transfer and QA source records.
- Extends shared server caches to 30 minutes for Inventory and Sales and one
  hour for QA. Manual Refresh and Lab imports still invalidate stale data.
- Runs the QA history, import log, and label-template reads concurrently.

## 0.9.3.4 performance and QA table build

- Keeps the raw Inventory, Transfer, and QA package collections on the server
  instead of duplicating thousands of records through each browser session.
- Reuses the operational Inventory context while the compact Sales snapshot is
  hydrated, avoiding a second Inventory and Production database load.
- Makes Build and Compare, Saved Plans, and Production Calendar switch
  immediately; saved-plan refresh now runs in the background after a save.
- Converts QA summaries and package details into the row format required by the
  sortable table component, fixing tables that remained blank while charts
  worked.
- Resolves historical QA strains from the item, source harvest, and source
  package fields, including Ice Cream Cake, Fruit Stand, and LA Piff.
- Limits each visible matching-package QA table to 250 filtered records to keep
  browser rendering responsive while retaining the full server-side history.

## 0.9.3.3 corrective build

- Shows a full-page secure-connection indicator before Google or Microsoft
  navigation, and no longer styles normal callback progress as a red warning.
- Validates authenticated employees in one database transaction instead of
  repeating several setup and lookup connections.
- Makes production-plan progress visible before session validation begins and
  validates only the selected WIP tags rather than reloading the full inventory.
- Keeps QA chart strain selections synchronized with global filters, applies the
  global SKU Type filter to QA, and adds an always-visible matching-package table.

## 0.9.3.2 corrective build

- Fixes the QA query placeholder error caused by the literal percent sign in
  Total THC and Total Terpenes test names.
- Allows the secure session cookie to finish saving before the OAuth callback
  navigates to the dashboard, reducing the sign-in bounce.
- Shows an immediate spinner and persistent in-page status while a production
  plan is being saved.

## 0.9.3.1 corrective build

- Makes QA and Sales hydration true background events so a slow database read
  cannot block Inventory or subtab navigation.
- Reduces the QA read from every historical analyte to one current compliance
  row per package plus Total THC and Total Terpenes rows used by the charts.
- Adds visible Google/Microsoft connection progress, disables duplicate clicks,
  and persists the secure session before redirecting to the dashboard.

- Restores the Quality Assurance workspace on the verified Version 0.9.2.2
  compact Sales snapshot foundation.
- Keeps QA isolated: opening or reconnecting QA cannot replace working
  Inventory or Sales data with demo data.
- Reads the Streamlit 81.5 compact Sales snapshot instead of scanning the raw
  transfer history during a cold start.
- Retries transient Supabase SSL disconnects with a fresh connection and gives
  the compact snapshot and QA reads bounded extended timeouts.
- Removes database table-creation work from ordinary QA navigation. QA table
  initialization remains part of the authorized Lab Results import workflow.
- Opens Cultivation on All Test Types so tables and charts do not begin behind
  an overly narrow Flower-only filter.
- Keeps the improved Compliance Label workflow: direct tag/harvest search,
  browse filters, a searchable finite NiceLabel catalog, horizontal table
  overflow, and bottom page spacing.
- Retains the Version 0.9.2 mapping-column collision fix for published Sales
  snapshots.

Publish the compact Sales snapshot from Streamlit 81.5.2.1 or newer before
testing this release.
