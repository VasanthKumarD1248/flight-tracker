"""Google Flights search via fast-flights, with the EU consent page handled."""

import time

from fast_flights import FlightQuery, Passengers, create_query
from fast_flights.exceptions import FlightsNotFound
from fast_flights.parser import parse
from primp import Client

URL = "https://www.google.com/travel/flights"

# Pre-accepted cookie consent, otherwise EU IPs get the "Before you continue" page.
CONSENT_COOKIE = "SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg; CONSENT=YES+"


class SearchError(Exception):
    pass


def make_client() -> Client:
    return Client(
        impersonate="chrome_145",
        impersonate_os="macos",
        referer=True,
        cookie_store=True,
        headers={"Cookie": CONSENT_COOKIE},
    )


def _fmt_time(sdt) -> str:
    y, m, d = sdt.date
    hh, mm = sdt.time
    return f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}"


def _itinerary(f) -> dict:
    legs = f.flights
    return {
        "price": f.price,
        "airlines": f.airlines,
        "path": [legs[0].from_airport.code] + [leg.to_airport.code for leg in legs],
        "depart": _fmt_time(legs[0].departure),
        "arrive": _fmt_time(legs[-1].arrival),
        "flight_minutes": sum(leg.duration or 0 for leg in legs),
    }


def search_round_trip(client, cfg, depart: str, ret: str, via: str | None = None):
    """Return (google_flights_url, [itineraries sorted by price])."""
    common = {"connecting_airports": [via]} if via else {}
    q = create_query(
        flights=[
            FlightQuery(date=depart, from_airport=cfg["origin"], to_airport=cfg["destination"], **common),
            FlightQuery(date=ret, from_airport=cfg["destination"], to_airport=cfg["origin"], **common),
        ],
        trip="round-trip",
        seat=cfg["seat"],
        passengers=Passengers(adults=cfg["adults"]),
        currency=cfg["currency"],
        language="en",
        max_stops=cfg["max_stops"],
    )
    resp = client.get(URL, params=q.params())
    if resp.status_code != 200:
        raise SearchError(f"HTTP {resp.status_code}")
    html = resp.text
    if "ds:1" not in html:
        raise SearchError("no results payload (consent page, captcha or layout change)")
    try:
        results = parse(html)
    except FlightsNotFound:
        results = []
    items = sorted((_itinerary(f) for f in results if f.price), key=lambda i: i["price"])
    return q.url(), items


def search_with_retry(client, cfg, depart, ret, via=None, attempts=3):
    last = None
    for n in range(attempts):
        try:
            return search_round_trip(client, cfg, depart, ret, via)
        except Exception as e:  # network errors, parse errors, blocks
            last = e
            time.sleep(5 * (n + 1))
    raise SearchError(f"{depart}->{ret} via={via}: {last}")
