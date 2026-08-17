# QCC Control Tower 0.9.4 - Staging Release Notes

## 0.9.4.2 Nearby-Shop Map

- Replaces the multi-stop route with one availability map containing a marker
  for each matching shop.
- Uses the entered address or ZIP as the map's starting-location marker.
- Sorts the visible shop list by approximate straight-line distance and displays
  the mileage to every successfully located shop.
- Opens directions only for the individual retailer selected from the map or
  nearby-shop list.
- Uses the Clade9 directory addresses with OpenStreetMap map tiles and address
  lookup services; no Google Maps API key is required for the availability map.

## 0.9.4.1 Retail Map Update

- Adds **Map All Matching Shops** so filtered brand, strain, SKU, and delivery
  results are no longer represented by only one retailer on the map.
- Adds an optional starting address or ZIP for opening the matching locations as
  a Google Maps route.
- Adds a matching-shop directory with each retailer's Clade9 address, last
  matching delivery date, website, and individual map link.
- Keeps the existing retailer selector for focusing on one store at a time.
- Limits a single multi-stop Google Maps route to ten shops; larger result sets
  remain available in the matching-shop directory and retailer selector.

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
