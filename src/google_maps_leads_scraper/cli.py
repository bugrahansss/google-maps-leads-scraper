"""Command line entry point.

    google-maps-leads-scraper search "coffee shop" --ll 30.2672,-97.7431 --pages 3 -f csv -o leads.csv
    google-maps-leads-scraper place ChIJ...
    google-maps-leads-scraper reviews --data-id 0x...:0x... --sort newest --pages 2
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from .client import SORT_OPTIONS, TOKEN_ENV, GoogleMapsScraper, ScrapeError
from .models import PLACE_FIELDS, REVIEW_FIELDS, dedupe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-maps-leads-scraper",
        description="Export Google Maps businesses and reviews to JSON or CSV through the Scrape.do Google Maps API.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-t", "--token", help=f"Scrape.do API token (default: ${TOKEN_ENV})")
    common.add_argument("--gl", default="us", help="Country code for results (default: us)")
    common.add_argument("--hl", default="en", help="Language code (default: en)")
    common.add_argument("-f", "--format", choices=("json", "csv"), default="json", help="Output format (default: json)")
    common.add_argument("-o", "--output", help="Write to this file instead of stdout")

    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", parents=[common], help="Businesses for one or more queries")
    search.add_argument("queries", nargs="+", help='Search terms, e.g. "dentist" or "coffee shop in Austin, TX"')
    search.add_argument("--ll", action="append", default=[], help="Centre point lat,lng (repeatable for a grid)")
    search.add_argument("--zoom", type=float, default=14, help="Map zoom for --ll (default: 14)")
    search.add_argument("--pages", type=int, default=1, help="Pages per query and point, 20 places each, max 6 (default: 1)")
    search.add_argument("--details", action="store_true", help="Also fetch each place's details (10 credits per place)")

    place = sub.add_parser("place", parents=[common], help="Details for places by place_id")
    place.add_argument("place_ids", nargs="*", help="Google place IDs (ChIJ...)")
    place.add_argument("--cid", action="append", default=[], help="Look up by data_cid instead (repeatable)")

    reviews = sub.add_parser("reviews", parents=[common], help="Reviews for one place")
    target = reviews.add_mutually_exclusive_group(required=True)
    target.add_argument("--data-id", help="Feature ID, e.g. 0x8644b5b055cd46fd:0x90bd2653f9c3ab4d")
    target.add_argument("--place-id", help="Google place ID (ChIJ...)")
    reviews.add_argument("--sort", choices=SORT_OPTIONS, default="newest", help="Review order (default: newest)")
    reviews.add_argument("--pages", type=int, default=1, help="Review pages, about 10 each (default: 1)")
    reviews.add_argument("--query", default="", help="Only reviews that mention this word")
    return parser


def _write(rows: List[Dict[str, Any]], records: List[Dict[str, Any]], fields: List[str], fmt: str, output: Optional[str]) -> None:
    stream = open(output, "w", encoding="utf-8", newline="" if fmt == "csv" else None) if output else sys.stdout
    try:
        if fmt == "csv":
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        else:
            json.dump(records, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    finally:
        if output:
            stream.close()


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        maps = GoogleMapsScraper(token=args.token, gl=args.gl, hl=args.hl)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    failures = 0
    if args.command == "search":
        places = []
        for query in args.queries:
            for point in args.ll or [None]:
                label = f"{query!r}" + (f" @ {point}" if point else "")
                try:
                    found = maps.search(query, ll=point, pages=args.pages, zoom=args.zoom)
                except (ScrapeError, ValueError) as exc:
                    failures += 1
                    print(f"fail  {label}: {exc}", file=sys.stderr)
                    continue
                print(f"ok    {label}: {len(found)} place(s)", file=sys.stderr)
                places.extend(found)
        places = dedupe(places)
        if args.details:
            detailed = []
            for p in places:
                try:
                    detailed.append(maps.place(p.place_id, p.data_cid) if (p.place_id or p.data_cid) else p)
                except (ScrapeError, ValueError) as exc:
                    print(f"fail  details {p.name}: {exc}", file=sys.stderr)
                    detailed.append(p)
            places = detailed
        _write([p.to_row() for p in places], [p.to_dict() for p in places], PLACE_FIELDS, args.format, args.output)
        count = len(places)
    elif args.command == "place":
        if not args.place_ids and not args.cid:
            print("Pass at least one place_id or --cid.", file=sys.stderr)
            return 2
        places = []
        for pid in args.place_ids:
            try:
                places.append(maps.place(place_id=pid))
            except (ScrapeError, ValueError) as exc:
                failures += 1
                print(f"fail  {pid}: {exc}", file=sys.stderr)
        for cid in args.cid:
            try:
                places.append(maps.place(data_cid=cid))
            except (ScrapeError, ValueError) as exc:
                failures += 1
                print(f"fail  cid {cid}: {exc}", file=sys.stderr)
        _write([p.to_row() for p in places], [p.to_dict() for p in places], PLACE_FIELDS, args.format, args.output)
        count = len(places)
    else:
        try:
            found = maps.reviews(
                data_id=args.data_id or "", place_id=args.place_id or "", sort_by=args.sort, pages=args.pages, query=args.query
            )
        except (ScrapeError, ValueError) as exc:
            print(f"fail  reviews: {exc}", file=sys.stderr)
            return 1
        _write([r.to_row() for r in found], [r.to_dict() for r in found], REVIEW_FIELDS, args.format, args.output)
        count = len(found)

    print(f"{count} record(s), {maps.credits_used} credits used" + (f", saved to {args.output}" if args.output else ""), file=sys.stderr)
    return 1 if failures and not count else 0


if __name__ == "__main__":
    raise SystemExit(main())
