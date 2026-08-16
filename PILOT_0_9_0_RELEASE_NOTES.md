# QCC Control Tower Reflex 0.9.0

## Offline QA migration

- Adds Quality Assurance to the primary workspace navigation.
- Reads the same Supabase `lab_result_records`, `lab_import_log`,
  `qa_label_templates`, and `qa_label_print_log` records used by Streamlit.
- Adds duplicate-safe multi-file Metrc LabResultsReport CSV imports.
- Separates Cultivation and Manufacturing compliance results.
- Applies the global Brand and Strain filters to both QA operation views.
- Keeps Compliance Test Type inside both Cultivation and Manufacturing.
- Prevents Flower and Pre-Roll potency history from being averaged together.
- Uses published inventory package mappings first for Brand, Strain, and SKU.
- Adds package-level pass success, potency consistency charts, and average/range
  tables.
- Adds Compliance Label Search and Printing with Expiration Date calculation or
  manual override.
- Logs printable-label generation without changing the Metrc lab source data.

## Test boundary

This is an offline candidate. Do not push it to the Render production branch
until Cultivation, Manufacturing, and label outputs are compared with Streamlit
Version 81.4.
