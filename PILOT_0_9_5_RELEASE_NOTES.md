# QCC Control Tower Reflex 0.9.5.0

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
