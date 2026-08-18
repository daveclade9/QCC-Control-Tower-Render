# QCC Control Tower Reflex 0.9.5.1

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
