import csv
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from google_maps_leads_scraper import (  # noqa: E402
    PLACE_FIELDS,
    GoogleMapsScraper,
    Place,
    ScrapeError,
    dedupe,
    format_hours,
    format_ll,
    parse_place,
    parse_reviews,
    parse_search,
)
from google_maps_leads_scraper import cli  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload=None, status=200, text=""):
        self.payload = payload
        self.status_code = status
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


class ModelTests(unittest.TestCase):
    def test_format_ll_variants(self):
        self.assertEqual(format_ll("30.2672,-97.7431"), "@30.2672,-97.7431,14z")
        self.assertEqual(format_ll("30.2672, -97.7431, 12"), "@30.2672,-97.7431,12z")
        self.assertEqual(format_ll("@30.2672,-97.7431,15z"), "@30.2672,-97.7431,15z")
        self.assertEqual(format_ll("1,2", zoom=11.5), "@1,2,11.5z")

    def test_format_ll_rejects_bad_input(self):
        for bad in ("austin", "", "91,0", "0,181"):
            with self.assertRaises(ValueError):
                format_ll(bad)

    def test_format_hours_dict_and_list(self):
        self.assertEqual(format_hours({"tuesday": "Closed", "monday": "7 AM–2 PM"}), "Mon: 7 AM–2 PM; Tue: Closed")
        self.assertEqual(format_hours([{"sunday": "9-5"}, {"monday": "8-4"}]), "Mon: 8-4; Sun: 9-5")
        self.assertEqual(format_hours("Open now"), "")

    def test_parse_search(self):
        places = parse_search(load("search_page1.json"))
        self.assertEqual(len(places), 20)
        first = places[0]
        self.assertEqual(first.name, "Example Coffee 1")
        self.assertEqual(first.category, "Coffee shop")
        self.assertEqual(first.categories, ["Coffee shop", "Cafe"])
        self.assertEqual(first.phone, "(512) 555-0001")
        self.assertEqual(first.reviews, 101)
        self.assertAlmostEqual(first.latitude, 30.271)
        self.assertEqual(first.open_state, "Open · Closes 7 PM")
        self.assertEqual(first.hours, "Mon: 7 AM–2 PM; Tue: Closed")
        self.assertEqual(first.attributes, {"accessibility": ["Wheelchair accessible entrance"]})
        self.assertEqual(first.maps_url, "https://maps.google.com/?cid=1001")

    def test_parse_search_single_result_dict(self):
        payload = {"local_results": load("search_page1.json")["local_results"][0]}
        self.assertEqual(len(parse_search(payload)), 1)
        self.assertEqual(parse_search({}), [])

    def test_parse_place(self):
        place = parse_place(load("place.json"))
        self.assertEqual(place.plus_code, "77FH+PW Austin, Texas")
        self.assertEqual(place.hours, "Mon: 7 AM–2 PM; Sun: 7 AM–7 PM")
        self.assertEqual(place.rating_breakdown, {"1": 2, "5": 90})

    def test_place_type_as_list(self):
        payload = load("place.json")
        item = payload["place_results"]
        item.pop("types")
        item["type"] = ["Coffee shop", "Art cafe"]
        place = parse_place(payload)
        self.assertEqual(place.category, "Coffee shop")
        self.assertEqual(place.categories, ["Coffee shop", "Art cafe"])
        with self.assertRaises(ValueError):
            parse_place({})

    def test_parse_reviews(self):
        reviews = parse_reviews(load("reviews_page1.json"), data_id="0x0:0x0001")
        self.assertEqual([r.review_id for r in reviews], ["r1", "r2", "r3"])
        self.assertEqual(reviews[0].owner_reply, "Thanks, Jane!")
        self.assertTrue(reviews[0].local_guide)
        self.assertEqual(reviews[1].text, "Original text 2")  # falls back to extracted_snippet
        self.assertEqual(reviews[1].owner_reply, "")
        self.assertEqual(reviews[0].data_id, "0x0:0x0001")

    def test_rows_match_fields(self):
        place = parse_search(load("search_page1.json"))[0]
        row = place.to_row()
        self.assertEqual(list(row), PLACE_FIELDS)
        self.assertEqual(row["categories"], "Coffee shop, Cafe")

    def test_dedupe(self):
        a = Place(name="A", place_id="1")
        b = Place(name="B", place_id="1")
        c = Place(name="C", address="x")
        self.assertEqual([p.name for p in dedupe([a, b, c, c])], ["A", "C"])


class ClientTests(unittest.TestCase):
    def test_requires_token(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                GoogleMapsScraper()

    def test_search_paginates_and_dedupes(self):
        session = FakeSession([FakeResponse(load("search_page1.json")), FakeResponse(load("search_page2.json"))])
        maps = GoogleMapsScraper(token="t", session=session, retries=0)
        places = maps.search("coffee shop", ll="30.2672,-97.7431", pages=5)
        self.assertEqual(len(places), 25)  # 20 + 5 new, page 2 had 7 with 2 repeats
        self.assertEqual(len(session.calls), 2)  # page 2 was short, so it stopped
        url, params = session.calls[0]
        self.assertTrue(url.endswith("/plugin/google/maps/search"))
        self.assertEqual(params["ll"], "@30.2672,-97.7431,14z")
        self.assertNotIn("start", params)
        self.assertEqual(session.calls[1][1]["start"], 20)
        self.assertEqual(maps.credits_used, 20)

    def test_search_caps_pages(self):
        page = load("search_page1.json")
        responses = []
        for n in range(6):
            p = json.loads(json.dumps(page))
            for i, item in enumerate(p["local_results"]):
                item["place_id"] = f"p{n}-{i}"
            responses.append(FakeResponse(p))
        session = FakeSession(responses)
        places = GoogleMapsScraper(token="t", session=session).search("x", pages=50)
        self.assertEqual(len(session.calls), 6)
        self.assertEqual(len(places), 120)

    def test_search_rejects_empty_query(self):
        with self.assertRaises(ValueError):
            GoogleMapsScraper(token="t", session=FakeSession([])).search("  ")

    def test_place_by_cid(self):
        session = FakeSession([FakeResponse(load("place.json"))])
        place = GoogleMapsScraper(token="t", session=session).place(data_cid="1001")
        self.assertEqual(place.name, "Example Coffee 1")
        self.assertEqual(session.calls[0][1]["data_cid"], "1001")
        self.assertNotIn("place_id", session.calls[0][1])

    def test_reviews_follow_token(self):
        session = FakeSession([FakeResponse(load("reviews_page1.json")), FakeResponse(load("reviews_page2.json"))])
        reviews = GoogleMapsScraper(token="t", session=session).reviews(data_id="0x0:0x0001", pages=3)
        self.assertEqual([r.review_id for r in reviews], ["r1", "r2", "r3", "r4"])
        self.assertEqual(session.calls[1][1]["next_page_token"], "TOKEN2")
        self.assertEqual(session.calls[0][1]["sort_by"], "newest")
        self.assertEqual(len(session.calls), 2)

    def test_reviews_validation(self):
        maps = GoogleMapsScraper(token="t", session=FakeSession([]))
        with self.assertRaises(ValueError):
            maps.reviews()
        with self.assertRaises(ValueError):
            maps.reviews(data_id="x", sort_by="oldest")
        with self.assertRaises(ValueError):
            maps.reviews(data_id="x", query="a", topic_id="b")

    def test_client_error_not_retried(self):
        session = FakeSession([FakeResponse(None, status=401, text="bad token")] * 3)
        maps = GoogleMapsScraper(token="t", session=session, retries=2)
        with self.assertRaises(ScrapeError) as ctx:
            maps.search("x")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(maps.credits_used, 0)

    def test_server_error_retried(self):
        session = FakeSession([FakeResponse(None, status=502, text="bad gateway"), FakeResponse(load("place.json"))])
        with mock.patch("google_maps_leads_scraper.client.time.sleep"):
            place = GoogleMapsScraper(token="t", session=session, retries=1).place("ChIJexample0001")
        self.assertEqual(place.name, "Example Coffee 1")
        self.assertEqual(len(session.calls), 2)


class CliTests(unittest.TestCase):
    def test_search_csv(self):
        session = FakeSession([FakeResponse(load("search_page2.json"))])
        out = io.StringIO()
        with mock.patch("google_maps_leads_scraper.client.requests.Session", return_value=session), \
                mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", io.StringIO()):
            code = cli.main(["search", "coffee shop", "--ll", "30.2,-97.7", "-t", "t", "-f", "csv"])
        self.assertEqual(code, 0)
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["name"], "Example Coffee 19")

    def test_place_needs_ids(self):
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(cli.main(["place", "-t", "t"]), 2)


if __name__ == "__main__":
    unittest.main()
