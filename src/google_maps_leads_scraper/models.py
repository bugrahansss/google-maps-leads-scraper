"""Turn Scrape.do Google Maps API responses into flat records.

The Scrape.do Google Maps API returns parsed JSON: ``local_results`` for a
search, ``place_results`` for one place and ``reviews`` for a review page. The
helpers here map those objects to dataclasses that export cleanly to CSV.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

PLACE_FIELDS = [
    "name", "category", "categories", "rating", "reviews", "phone", "website", "address",
    "latitude", "longitude", "hours", "open_state", "place_id", "data_id", "data_cid",
    "plus_code", "thumbnail", "maps_url",
]

REVIEW_FIELDS = [
    "place_id", "data_id", "review_id", "rating", "date", "iso_date", "text", "likes",
    "author", "author_reviews", "local_guide", "owner_reply", "owner_reply_date", "link",
]

_LL_RE = re.compile(r"^@?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)(?:\s*,\s*(\d+(?:\.\d+)?)z?)?\s*$")
_SPACES = re.compile(r"[   ]")


def clean_text(value: Any) -> str:
    """Normalize the narrow and non-breaking spaces Google puts in times."""
    if value is None:
        return ""
    return _SPACES.sub(" ", str(value)).strip()


def format_ll(value: str, zoom: float = 14) -> str:
    """Return the ``@lat,lng,zoomz`` form the API expects.

    Accepts ``"30.2672,-97.7431"``, ``"30.2672, -97.7431, 13"`` or an already
    formatted ``"@30.2672,-97.7431,14z"``.
    """
    match = _LL_RE.match(value or "")
    if not match:
        raise ValueError(f"Not a coordinate pair: {value!r} (expected 'lat,lng' or '@lat,lng,14z')")
    lat, lng, z = match.groups()
    if not -90 <= float(lat) <= 90 or not -180 <= float(lng) <= 180:
        raise ValueError(f"Coordinates out of range: {value!r}")
    zoom_str = z if z else (f"{zoom:g}")
    return f"@{lat},{lng},{zoom_str}z"


def format_hours(hours: Any) -> str:
    """Flatten ``operating_hours`` (dict) or ``hours`` (list of dicts) to one line.

    >>> format_hours({"monday": "7 AM-2 PM", "tuesday": "Closed"})
    'Mon: 7 AM-2 PM; Tue: Closed'
    """
    merged: Dict[str, str] = {}
    if isinstance(hours, dict):
        merged = {str(k).lower(): clean_text(v) for k, v in hours.items()}
    elif isinstance(hours, list):
        for item in hours:
            if isinstance(item, dict):
                merged.update({str(k).lower(): clean_text(v) for k, v in item.items()})
    else:
        return ""
    parts = [f"{day[:3].title()}: {merged[day]}" for day in DAYS if day in merged]
    return "; ".join(parts)


def maps_url(place_id: str = "", data_cid: str = "") -> str:
    """A link that opens the place in Google Maps."""
    if data_cid:
        return f"https://maps.google.com/?cid={data_cid}"
    if place_id:
        return f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    return ""


@dataclass
class Place:
    """One business from a search result or a place lookup."""

    name: str = ""
    category: str = ""
    categories: List[str] = field(default_factory=list)
    rating: Optional[float] = None
    reviews: Optional[int] = None
    phone: str = ""
    website: str = ""
    address: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    hours: str = ""
    open_state: str = ""
    place_id: str = ""
    data_id: str = ""
    data_cid: str = ""
    plus_code: str = ""
    thumbnail: str = ""
    maps_url: str = ""
    description: str = ""
    rating_breakdown: Dict[str, int] = field(default_factory=dict)
    attributes: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_api(cls, item: Dict[str, Any]) -> "Place":
        coords = item.get("gps_coordinates") or {}
        types = [clean_text(t) for t in (item.get("types") or []) if t]
        category = clean_text(item.get("type")) or (types[0] if types else "")
        if category and category not in types:
            types.insert(0, category)
        hours = format_hours(item.get("operating_hours") or item.get("hours"))
        attributes: Dict[str, List[str]] = {}
        for ext in item.get("extensions") or []:
            if isinstance(ext, dict):
                for key, values in ext.items():
                    if isinstance(values, list):
                        attributes[str(key)] = [clean_text(v) for v in values]
        breakdown = {
            str(row.get("stars")): int(row.get("amount") or 0)
            for row in item.get("rating_summary") or []
            if isinstance(row, dict) and row.get("stars") is not None
        }
        description = item.get("description")
        if isinstance(description, dict):
            description = description.get("snippet") or ""
        place_id = str(item.get("place_id") or "")
        data_cid = str(item.get("data_cid") or "")
        return cls(
            name=clean_text(item.get("title")),
            category=category,
            categories=types,
            rating=_to_float(item.get("rating")),
            reviews=_to_int(item.get("reviews")),
            phone=clean_text(item.get("phone")),
            website=clean_text(item.get("website")),
            address=clean_text(item.get("address")),
            latitude=_to_float(coords.get("latitude")),
            longitude=_to_float(coords.get("longitude")),
            hours=hours,
            open_state=clean_text(item.get("open_state") or (item.get("hours") if isinstance(item.get("hours"), str) else "")),
            place_id=place_id,
            data_id=str(item.get("data_id") or ""),
            data_cid=data_cid,
            plus_code=clean_text(item.get("plus_code")),
            thumbnail=clean_text(item.get("thumbnail")),
            maps_url=maps_url(place_id, data_cid),
            description=clean_text(description),
            rating_breakdown=breakdown,
            attributes=attributes,
        )

    @property
    def key(self) -> str:
        """Stable identity used to drop duplicates across pages and queries."""
        return self.place_id or self.data_id or self.data_cid or f"{self.name}|{self.address}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_row(self) -> Dict[str, Any]:
        row = {name: getattr(self, name) for name in PLACE_FIELDS}
        row["categories"] = ", ".join(self.categories)
        return {k: ("" if v is None else v) for k, v in row.items()}


@dataclass
class Review:
    """One Google Maps review."""

    review_id: str = ""
    rating: Optional[int] = None
    date: str = ""
    iso_date: str = ""
    text: str = ""
    likes: int = 0
    author: str = ""
    author_reviews: Optional[int] = None
    local_guide: bool = False
    owner_reply: str = ""
    owner_reply_date: str = ""
    link: str = ""
    place_id: str = ""
    data_id: str = ""

    @classmethod
    def from_api(cls, item: Dict[str, Any], place_id: str = "", data_id: str = "") -> "Review":
        user = item.get("user") or {}
        response = item.get("response") or {}
        snippet = item.get("snippet")
        if not snippet:
            snippet = (item.get("extracted_snippet") or {}).get("original")
        reply = response.get("snippet") or (response.get("extracted_snippet") or {}).get("original")
        return cls(
            review_id=str(item.get("review_id") or ""),
            rating=_to_int(item.get("rating")),
            date=clean_text(item.get("date")),
            iso_date=clean_text(item.get("iso_date")),
            text=clean_text(snippet),
            likes=_to_int(item.get("likes")) or 0,
            author=clean_text(user.get("name")),
            author_reviews=_to_int(user.get("reviews")),
            local_guide=bool(user.get("local_guide")),
            owner_reply=clean_text(reply),
            owner_reply_date=clean_text(response.get("date")),
            link=clean_text(item.get("link")),
            place_id=place_id,
            data_id=data_id,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_row(self) -> Dict[str, Any]:
        return {name: ("" if getattr(self, name) is None else getattr(self, name)) for name in REVIEW_FIELDS}


def parse_search(payload: Dict[str, Any]) -> List[Place]:
    """Places from one search response (``local_results``)."""
    results = payload.get("local_results") or []
    if isinstance(results, dict):  # a query that resolves to a single place
        results = [results]
    return [Place.from_api(item) for item in results if isinstance(item, dict)]


def parse_place(payload: Dict[str, Any]) -> Place:
    """The place from a place response (``place_results``)."""
    item = payload.get("place_results")
    if not isinstance(item, dict):
        raise ValueError("Response has no place_results")
    return Place.from_api(item)


def parse_reviews(payload: Dict[str, Any], place_id: str = "", data_id: str = "") -> List[Review]:
    """Reviews from one reviews response."""
    return [
        Review.from_api(item, place_id=place_id, data_id=data_id)
        for item in payload.get("reviews") or []
        if isinstance(item, dict)
    ]


def next_page_token(payload: Dict[str, Any]) -> str:
    return str((payload.get("pagination") or {}).get("next_page_token") or "")


def dedupe(places: Iterable[Place]) -> List[Place]:
    """Keep the first occurrence of every place."""
    seen = set()
    out: List[Place] = []
    for place in places:
        if place.key in seen:
            continue
        seen.add(place.key)
        out.append(place)
    return out


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", "").split(".")[0])
    except (TypeError, ValueError):
        return None
