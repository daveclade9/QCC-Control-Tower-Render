# QCC Control Tower 0.9.4 - Staging Release Notes

## Retail Availability

- Adds **Retail Availability** to Sales & Demand Planning.
- Filters recent retailer deliveries by one, two, three, or four weeks.
- Filters independently by brand, strain, SKU type, and retailer.
- Summarizes matching retailers, units shipped, product combinations, and the
  newest matching delivery.
- Matches Metrc retailer names to a cleaned snapshot of 197 New Jersey entries
  from the Clade9 store locator.
- Shows the matched Clade9-listed address, retailer website, selected-retailer
  Google Maps lookup, and a detailed delivery table.
- Keeps Retail Availability separate from production-oriented SKU Planning &
  Coverage.

## Data and accuracy

- Uses the complete compact Sales snapshot rather than the browser's 2,000-row
  transfer-history display.
- Sends daily delivery aggregates to the browser to keep filtering responsive.
- Clearly states that recent deliveries do not guarantee current shelf stock.
- Uses the Clade9-listed street address when a reliable name match is available;
  otherwise it falls back safely to the Metrc retailer name.
