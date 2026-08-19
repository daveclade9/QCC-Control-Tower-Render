# QCC Control Tower Reflex 0.9.5.17

## 0.9.5.17 diagnostic correction

- Ignores duplicate same-tab change events emitted after a controlled
  Inventory tab receives its selected value.
- Preserves the original source-to-destination timing result instead of
  replacing it with an incorrect `Aging Risk Bulk to Aging Risk Bulk` result.

## 0.9.5.16 Inventory navigation diagnostics

- Adds a staging diagnostic after each Inventory tab change showing the
  server-update time, returned table-row count, and serialized row-payload size.
- Writes the same measurement to Render logs with the
  `INVENTORY_NAV_DIAGNOSTIC` prefix.
- Measures server/state preparation separately from the user's perceived
  browser-rendering time so the next optimization can target the actual delay.
- Makes no Supabase schema or data changes.

## 0.9.5.15 Inventory navigation optimization

- Keeps one Inventory Grid.js instance mounted when moving between Inventory
  tabs instead of destroying and rebuilding the table on every tab click.
- Reuses the existing cached row matrix for each Inventory view when users
  return to a previously opened tab.
- Preserves the current columns, formatting, search, sorting, page-size
  selector, summaries, filters, and downloads.
- Makes no Supabase schema or data changes.

## 0.9.5.14 faster repeat navigation

- Restores client-managed top-level tabs so switching workspaces does not wait
  for a server round trip.
- Reuses Production plans and calendar data already included in the fast
  Inventory/operational load instead of reading those tables a second time.
- Retains the compact Inventory header and background Sales refresh from
  0.9.5.13.

## 0.9.5.13 workspace and production loading

- Renders only the active top-level workspace so hidden Inventory, Sales, QA,
  and Administration tables do not compete for browser layout work.
- Loads Saved Plans and the Production Calendar independently before the
  heavier Sales and velocity payload finishes in the background.
- Locks Inventory table headers to a compact 72px height after Grid.js reflow.

## 0.9.5.12 faster production-plan deletion

- Keeps the shared inventory snapshot warm when production plans are deleted,
  while refreshing saved plans and committed WIP in the background.
- Adds production child-table indexes and removes one unnecessary database
  round trip from single and bulk plan deletion.

## 0.9.5.11 multirow operational headers

- Splits every multiword header in Stockouts, Customer Shipment History, and
  Recent Transfer Records one word per row.
- Two-word headings use two rows and three-word headings use three rows while
  retaining the 14px header and body size.

## 0.9.5.10 shared table sizing

- Sets the Stockouts, Customer Shipment History, and Recent Transfer Records
  table headers and body cells to 14px.
- Leaves all other shared Grid tables and their existing formatting unchanged.

## 0.9.5.9 SKU Planning presentation

- Sets the SKU Planning & Coverage table header, body text, action text, and
  committed-WIP badges to 13.5px.
- Uses a uniform black header with white text while preserving all existing
  column widths and the highlighted WIP body cells.

## 0.9.5.8 compatibility correction

- Removes the automatic Clade9 fallback for all unfinished inventory in
  Building 33 and removes sales-history compatibility inference.
- Assigns Clade9 compatibility only to the 16 approved strains after the
  Building 1A origin and ownership gates have passed.
- Leaves unmatched Building 33 material as Compatibility Needs Review.
- Preserves production-planning blend exceptions and all existing table
  formatting.

## 0.9.5.7 compatibility model

- Calculates Compatible Brand for unfinished inventory without changing the
  Supabase snapshot or renaming finished goods.
- Applies Building 1A facility and ownership gates before explicit product and
  approved strain rules.
- Replaces Brand with Compatible Brand in Bulk, WIP & Pre-WIP, and Aging Risk
  Bulk; All Inventory retains Brand and adds Compatible Brand.
- Uses Compatible Brand consistently for global filtering and Executive WIP
  totals, and removes the earlier Aging Risk Bulk brand exclusion.
- Keeps Hybrid Blend, Sativa Blend, and Indica Blend broad-source behavior only
  inside production planning. It does not relabel every source Craft Kings.
- Makes no typography, spacing, width, pagination, or other table-format changes.

## 0.9.5.6 adjustment

- Sets Retail Availability column headings to an explicit 12px size.

## 0.9.5.5 hotfix

- Constrains each Retail Availability sorting control to a small transparent
  icon area and explicitly layers the white heading text above the header.

## 0.9.5.4 correction

- Splits multiword Retail Availability table headings across two deliberate
  lines and reserves dedicated space beside them for the sorting controls.

## 0.9.5.3 corrections

- Gives Matching WIP popup headings dedicated wrapping space and keeps each
  sorting control in a reserved area beside—not over—the column title.
- Applies a newly saved plan's source commitments to the loaded inventory
  immediately. Fully consumed source lots disappear from Build & Compare;
  partially consumed lots remain with their reduced available weight.

## 0.9.5.2 corrections

- Brand, Strain, and SKU are aligned single dropdowns with native cumulative
  keyboard type-ahead; there is no separate Strain search field.
- Inventory sends Grid.js `{limit: selected_rows}` instead of the unsupported
  `{page_size: selected_rows}`, so 10, 25, 50, and 100 now change the page size.

## 0.9.5.1 corrections

- Restores a complete Strain selector and pairs it with cumulative phrase search.
- Removes the duplicate server-side Inventory page buttons; the grid now owns
  paging and immediately honors 10, 25, 50, or 100 rows per page.
- Flushes Reset Global Filters progress to the browser before applying the reset.
- Gives every SKU Planning heading a fixed readable width, taller header area,
  wrapping, and room beside sorting controls.
- Fixes the post-save background refresh callable that caused the
  `run_in_thread()` positional-argument error.
- Uses five production lines: Flower Line 1, Flower Line 2, Manufacturing Line
  1, Manufacturing Line 2, and Flex Line 3. The two earlier Pre-Roll labels are
  displayed under their corresponding Manufacturing Line names.

## Team-review improvements

- Global Strain uses cumulative text entry with strain suggestions.
- Executive Dashboard WIP respects Brand, Strain, and Brand + Strain filters.
- Inventory and SKU Planning default to 10 rows with 25, 50, and 100 options.
- Reset Global Filters displays an immediate working state and blocks duplicate clicks.
- White Label is available as a lifecycle filter without guessing the assignment rule.
- SKU Planning has compact text, sticky headers, corrected numeric WIP age ordering,
  and a trailing-30-day average weekly units column.
- Velocity choices are 1 Week, 60 Days, 90 Days, 120 Days, and All Time.
- Saved production plans persist one of four color-coded packaging lines.
- Production Calendar groups plans by line and supports Month, Week, and Day views.
- Saving a plan refreshes Production and Sales planning caches in the background so
  committed and potential WIP update without a full application reload.
- QA recognizes Craft Kings Hybrid, Sativa, and Indica Blend finished products even
  when their source harvests contain multiple strains.

## Database compatibility

The release adds one backward-compatible `production_line` column to the existing
`production_plans` table. Existing plans are labeled `Unassigned`. No production
inventory, transfer, lab, employee, or historical plan records are rewritten.
