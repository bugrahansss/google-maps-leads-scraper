"""Call the Scrape.do Google Maps API.

Google Maps renders everything with JavaScript and serves a consent wall,
CAPTCHAs and rate limits to scripts. The Scrape.do Google Maps API runs the
request from a residential IP and returns the result as parsed JSON, so there
is no HTML or protobuf to decode:

* ``/plugin/google/maps/search``  businesses for a query around a point
  (20 per page, about 120 per query)
* ``/plugin/google/maps/place``   one business by ``place_id`` or ``data_cid``
* ``/plugin/google/maps/reviews`` reviews by ``data_id`` or ``place_id``

Every successful call costs 10 credits.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

from .models import Place, Review, dedupe, format_ll, next_page_token, parse_place, parse_reviews, parse_search

API_BASE = "https://api.scrape.do/plugin/google/maps"
TOKEN_ENV = "SCRAPEDO_TOKEN"
PAGE_SIZE = 20
MAX_PAGES = 6  # Google Maps stops at about 120 results per query
SORT_OPTIONS = ("most_relevant", "newest", "highest_rating", "lowest_rating")


class ScrapeError(RuntimeError):
    """Raised when the API call fails."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GoogleMapsScraper:
    """Search Google Maps, look up places and read reviews.

    >>> maps = GoogleMapsScraper(token="YOUR_SCRAPEDO_TOKEN")
    >>> places = maps.search("coffee shop", ll="30.2672,-97.7431", pages=2)
    >>> details = maps.place(places[0].place_id)
    >>> reviews = maps.reviews(data_id=places[0].data_id, sort_by="newest")
    """

    def __init__(
        self,
        token: Optional[str] = None,
        gl: str = "us",
        hl: str = "en",
        timeout: float = 90.0,
        retries: int = 2,
        session: Optional[requests.Session] = None,
    ):
        self.token = token or os.environ.get(TOKEN_ENV, "")
        if not self.token:
            raise ValueError(f"A Scrape.do API token is required. Pass token=... or set {TOKEN_ENV}.")
        self.gl = gl
        self.hl = hl
        self.timeout = timeout
        self.retries = max(0, retries)
        self.session = session or requests.Session()
        self.requests_made = 0

    # -- transport -----------------------------------------------------------------

    def call(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """GET ``/plugin/google/maps/<endpoint>`` and return the decoded JSON."""
        query = {k: v for k, v in params.items() if v not in (None, "")}
        query["token"] = self.token
        url = f"{API_BASE}/{endpoint}"
        last_error: Optional[ScrapeError] = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, params=query, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = ScrapeError(f"Request failed: {exc}")
            else:
                if response.status_code == 200:
                    self.requests_made += 1  # only successful calls are billed
                    try:
                        return response.json()
                    except ValueError:
                        last_error = ScrapeError("The API returned a non-JSON body", 200)
                else:
                    last_error = ScrapeError(
                        f"{endpoint} failed with status {response.status_code}: {response.text[:200]}",
                        status_code=response.status_code,
                    )
                    # A bad token or bad parameters will not fix themselves
                    if 400 <= response.status_code < 500 and response.status_code != 429:
                        break
            if attempt < self.retries:
                time.sleep(2 * (attempt + 1))
        assert last_error is not None
        raise last_error

    # -- search --------------------------------------------------------------------

    def iter_search(self, query: str, ll: Optional[str] = None, pages: int = 1, zoom: float = 14) -> Iterator[Place]:
        """Yield places for ``query`` page by page (20 per page, at most 6 pages).

        ``ll`` centres the search: ``"30.2672,-97.7431"`` or ``"@30.2672,-97.7431,14z"``.
        Without it, put the location in the query ("coffee shop in Austin, TX").
        """
        if not query.strip():
            raise ValueError("query is empty")
        center = format_ll(ll, zoom) if ll else None
        seen = set()
        for page in range(min(max(1, pages), MAX_PAGES)):
            payload = self.call(
                "search",
                {"q": query, "ll": center, "gl": self.gl, "hl": self.hl, "start": page * PAGE_SIZE or None},
            )
            places = parse_search(payload)
            fresh = [p for p in places if p.key not in seen]
            for place in fresh:
                seen.add(place.key)
                yield place
            if len(places) < PAGE_SIZE or not fresh:
                break

    def search(self, query: str, ll: Optional[str] = None, pages: int = 1, zoom: float = 14) -> List[Place]:
        """All places for one query as a list (see :meth:`iter_search`)."""
        return list(self.iter_search(query, ll=ll, pages=pages, zoom=zoom))

    def search_grid(self, queries: List[str], points: List[str], pages: int = 1, zoom: float = 14) -> List[Place]:
        """Run every query at every point and merge the results without duplicates.

        One query tops out at about 120 places, so covering a city means
        repeating it across several centre points (and related categories).
        """
        found: List[Place] = []
        for query in queries:
            for point in points or [None]:
                found.extend(self.iter_search(query, ll=point, pages=pages, zoom=zoom))
        return dedupe(found)

    # -- place ---------------------------------------------------------------------

    def place(self, place_id: str = "", data_cid: str = "") -> Place:
        """Full details for one place: hours, rating breakdown, attributes and more."""
        if not place_id and not data_cid:
            raise ValueError("Pass place_id or data_cid")
        payload = self.call("place", {"place_id": place_id or None, "data_cid": None if place_id else data_cid, "hl": self.hl})
        return parse_place(payload)

    # -- reviews -------------------------------------------------------------------

    def iter_reviews(
        self,
        data_id: str = "",
        place_id: str = "",
        sort_by: str = "newest",
        pages: int = 1,
        query: str = "",
        topic_id: str = "",
    ) -> Iterator[Review]:
        """Yield reviews page by page (about 10 per page).

        ``sort_by`` is one of ``most_relevant``, ``newest``, ``highest_rating``,
        ``lowest_rating``. ``query`` filters by a word; ``topic_id`` by one of the
        topics Google extracted. Pass one of the two, not both.
        """
        if not data_id and not place_id:
            raise ValueError("Pass data_id or place_id")
        if sort_by not in SORT_OPTIONS:
            raise ValueError(f"sort_by must be one of {', '.join(SORT_OPTIONS)}")
        if query and topic_id:
            raise ValueError("Pass query or topic_id, not both")
        token = ""
        seen = set()
        for _ in range(max(1, pages)):
            params = {
                "data_id": data_id or None,
                "place_id": None if data_id else place_id,
                "sort_by": None if sort_by == "most_relevant" else sort_by,
                "query": query or None,
                "topic_id": topic_id or None,
                "next_page_token": token or None,
                "hl": self.hl,
            }
            payload = self.call("reviews", params)
            fresh = [r for r in parse_reviews(payload, place_id=place_id, data_id=data_id) if r.review_id not in seen]
            for review in fresh:
                seen.add(review.review_id)
                yield review
            token = next_page_token(payload)
            if not token or not fresh:
                break

    def reviews(self, data_id: str = "", place_id: str = "", sort_by: str = "newest", pages: int = 1, **kwargs: str) -> List[Review]:
        """All reviews as a list (see :meth:`iter_reviews`)."""
        return list(self.iter_reviews(data_id=data_id, place_id=place_id, sort_by=sort_by, pages=pages, **kwargs))

    @property
    def credits_used(self) -> int:
        """Credits spent so far by this client (10 per call)."""
        return self.requests_made * 10
