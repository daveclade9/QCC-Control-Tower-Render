# QCC Control Tower Reflex 0.9.1.2

Nonblocking data-load hotfix for the Version 0.9.1 QA migration.

- Inventory and Production load without waiting for transfer history.
- Sales history warms in a Reflex background task so navigation remains usable.
- QA data remains isolated and loads only when the QA workspace is opened.
- Sales and QA failures no longer prevent Inventory from remaining available.
- Retains the 0.9.1.1 transfer-query and velocity/WIP performance corrections.
