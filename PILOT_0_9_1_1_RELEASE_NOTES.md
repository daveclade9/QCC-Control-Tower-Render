# QCC Control Tower Reflex 0.9.1.1

Performance hotfix for the offline Version 0.9.1 test.

- Removes an unnecessary database-wide sort from the transfer-history read.
- Calculates Potential Matching WIP once and reuses it across the 1-week,
  30-day, 90-day, and all-time velocity views.
- Preserves the complete shipment history and all existing demand metrics.
