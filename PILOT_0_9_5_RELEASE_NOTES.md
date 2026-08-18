# QCC Control Tower Reflex 0.9.5.5

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
