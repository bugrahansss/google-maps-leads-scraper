# Google Maps Leads Scraper

[![PyPI](https://img.shields.io/pypi/v/google-maps-leads-scraper)](https://pypi.org/project/google-maps-leads-scraper/)
[![Python](https://img.shields.io/pypi/pyversions/google-maps-leads-scraper)](https://pypi.org/project/google-maps-leads-scraper/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Turn a Google Maps search into a lead list. Give it a query and a point on the map; get back every business with its **name, phone, website, address, rating, review count, opening hours, coordinates and place ID**, as JSON or CSV. It can also pull a single place's full details (rating breakdown, plus code, attributes) and its **reviews** with owner replies.

It runs on the [Scrape.do Google Maps API](https://scrape.do/?utm_source=github&utm_medium=readme&utm_campaign=google-maps-leads-scraper), which returns parsed JSON, so there is no HTML, protobuf or browser automation to maintain.

```bash
pip install google-maps-leads-scraper
export SCRAPEDO_TOKEN=your_token
google-maps-leads-scraper search "coffee shop" --ll 30.2672,-97.7431 --pages 6 -f csv -o austin-coffee.csv
```

```
ok    'coffee shop' @ 30.2672,-97.7431: 104 place(s)
104 record(s), 60 credits used, saved to austin-coffee.csv
```

## Why not just request google.com/maps?

Google Maps is a JavaScript app. The page you get from `requests` has no business data in it, and the internal endpoints the app calls (`/search?tbm=map`, `/maps/preview/place`) answer in a nested protobuf-over-JSON array with an anti-XSSI prefix and no stable field names. Scripts that send many of them meet the consent wall, CAPTCHAs and rate limits, and the reviews endpoint most old tutorials used (`listentitiesreviews`) has been retired.

The Scrape.do Google Maps API makes the request from a residential IP and returns clean JSON:

| Endpoint | Input | Returns |
| --- | --- | --- |
| `/plugin/google/maps/search` | `q`, `ll`, `start` | 20 places per page, about 120 per query |
| `/plugin/google/maps/place` | `place_id` or `data_cid` | one place with hours, rating breakdown, attributes |
| `/plugin/google/maps/reviews` | `data_id` or `place_id`, `sort_by` | about 10 reviews per page, owner replies, topics |

Every successful call costs 10 credits, so a full page of 20 leads costs 10 credits and 1,000 leads cost about 500. The free plan has 1,000 credits a month.

## Command line

```bash
# One query around a point, 6 pages (the ~120 place maximum)
google-maps-leads-scraper search "plumber" --ll 40.7128,-74.0060 --pages 6 -f csv -o nyc-plumbers.csv

# The location can be in the query instead
google-maps-leads-scraper search "vegan restaurant in Lisbon" --gl pt

# Several queries across several points, merged without duplicates
google-maps-leads-scraper search "dentist" "orthodontist" \
  --ll 30.2672,-97.7431 --ll 30.3072,-97.7559 --pages 2 -f csv -o dentists.csv

# Full details for places
google-maps-leads-scraper place ChIJ_UbNVbC1RIYRTavD-VMmvZA

# Reviews, newest first, two pages
google-maps-leads-scraper reviews --data-id 0x8644b5b055cd46fd:0x90bd2653f9c3ab4d --sort newest --pages 2 -f csv -o reviews.csv
```

`--ll` takes `lat,lng`, `lat,lng,zoom` or the `@lat,lng,14z` form from a Maps URL. `search --details` also fetches every place's detail page (10 more credits per place).

## Python

```python
from google_maps_leads_scraper import GoogleMapsScraper

maps = GoogleMapsScraper(token="YOUR_SCRAPEDO_TOKEN")  # or set SCRAPEDO_TOKEN

places = maps.search("coffee shop", ll="30.2672,-97.7431", pages=3)
for p in places[:3]:
    print(p.name, p.rating, p.reviews, p.phone, p.website)

details = maps.place(places[0].place_id)
print(details.hours)              # Mon: 7 AM–2 PM; Tue: 7 AM–2 PM; ...
print(details.rating_breakdown)   # {'1': 16, '2': 6, '3': 14, '4': 41, '5': 604}

for review in maps.reviews(data_id=places[0].data_id, sort_by="lowest_rating", pages=2):
    print(review.rating, review.date, review.text[:80], "| reply:", bool(review.owner_reply))

print(maps.credits_used)
```

To cover a whole city, run the query around several points; `search_grid` does that and drops duplicates by place ID. See [`examples/leads_grid.py`](examples/leads_grid.py).

## Output fields

**Places:** `name`, `category`, `categories`, `rating`, `reviews`, `phone`, `website`, `address`, `latitude`, `longitude`, `hours`, `open_state`, `place_id`, `data_id`, `data_cid`, `plus_code`, `thumbnail`, `maps_url`. JSON output adds `rating_breakdown` and `attributes` (accessibility, service options and so on).

**Reviews:** `place_id`, `data_id`, `review_id`, `rating`, `date`, `iso_date`, `text`, `likes`, `author`, `author_reviews`, `local_guide`, `owner_reply`, `owner_reply_date`, `link`.

`place_id` feeds `place`; `data_id` feeds `reviews`.

## Use it responsibly

Business listings are public, but reviews carry the names of the people who wrote them. Store only what you need, follow the privacy rules that apply to you (GDPR, CCPA), respect opt-outs before cold outreach, and read Google's terms for your use case.

## Also available

- **No-code:** [Google Maps Leads Scraper on Apify](https://apify.com/scrapedo/google-maps-leads-scraper)
- **Browser demo:** [Hugging Face Space](https://huggingface.co/spaces/bugrahans/google-maps-leads-scraper)
- **Full guide:** [Google Maps Scraper: How to Scrape Google Maps for Leads](https://scrape.do/blog/google-maps-scraping/?utm_source=github&utm_medium=readme&utm_campaign=google-maps-leads-scraper), including the raw Python approach without the API

## Tests

```bash
python -m unittest discover -s tests
```

The fixtures are made-up businesses on example.com; the tests never hit the network.

## License

MIT
