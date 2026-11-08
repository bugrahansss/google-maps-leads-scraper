"""Build a lead list for one category across a small grid of points in Austin, TX.

One Google Maps query stops at about 120 places, so a city-wide list repeats
the query around several centre points and drops the duplicates.

    export SCRAPEDO_TOKEN=...
    python examples/leads_grid.py
"""

import csv

from google_maps_leads_scraper import PLACE_FIELDS, GoogleMapsScraper

POINTS = [
    "30.2672,-97.7431",  # downtown
    "30.3072,-97.7559",  # north central
    "30.2240,-97.7690",  # south
]

maps = GoogleMapsScraper()  # reads SCRAPEDO_TOKEN
places = maps.search_grid(["dentist"], POINTS, pages=2)

# Keep the ones a sales team can actually contact
leads = [p for p in places if p.phone or p.website]

with open("austin-dentists.csv", "w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=PLACE_FIELDS)
    writer.writeheader()
    writer.writerows(p.to_row() for p in leads)

print(f"{len(leads)} leads from {len(places)} places, {maps.credits_used} credits")
