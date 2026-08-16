# QCC Control Tower Reflex 0.9.1.3

Sales transfer-history query hotfix.

- Reads only the 19 fields required for demand and shipment analysis instead
  of all 30 raw Metrc transfer fields.
- Filters out records that cannot affect customer demand, open shipments, or
  shipment exceptions before they leave Supabase.
- Streams qualifying records in bounded batches rather than buffering one
  very large database result.
- Cancels a transfer read after two minutes instead of leaving the Sales
  workspace indefinitely loading; Inventory, Production, and QA remain usable.
- Retains the nonblocking module separation introduced in Version 0.9.1.2.
