"""Reading flight results out of a MakeMyTrip results page.

The browsing model is good at getting to the page and poor at copying numbers
off it: it mixed up departure times between flights. So the figures shown to
the user are parsed here, from the page's own text, not taken from the model.

Each result on the page reads, line by line:

    IndiGo
    6E-817
    20:45
    BLR
    02h 50m
    Non stop
    23:35
    DEL
    ...
    ₹ 11,664
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FLIGHT_RE = re.compile(
    r"(?P<airline>[A-Za-z][A-Za-z .&'-]{1,30})\n"
    r"(?P<code>[A-Z0-9]{2}-\d{2,4}(?: \| [A-Z0-9]{2}-\d{2,4})*)\n"
    r"(?P<dep>\d{2}:\d{2})\n"
    r"(?P<src>[A-Z]{3})\n+"
    r"(?P<duration>\d{1,2}h(?: \d{1,2}m)?)\n+"
    r"(?P<stops>[^\n]{3,40})\n+"
    r"(?P<arr>\d{2}:\d{2})\n"
    r"(?:[^\n]{0,8}\n){0,3}?"  # "+ 1" / "DAY" when it lands the next day
    r"(?P<dst>[A-Z]{3})\n"
    r"(?:[^\n]*\n){0,8}?"
    r"₹ ?(?P<fare>\d[\d,]*)"
)


@dataclass(frozen=True)
class Flight:
    airline: str
    code: str
    departure: str
    arrival: str
    origin: str
    destination: str
    duration: str
    stops: str
    fare_inr: int

    @property
    def non_stop(self) -> bool:
        return self.stops.lower().replace("-", " ").startswith("non stop")

    def short(self) -> str:
        return f"{self.airline} {self.code}, {self.departure}, INR {self.fare_inr:,}"


def parse_flights(page_text: str) -> list[Flight]:
    """Every flight listed in the page text, in page order, without repeats."""
    flights: list[Flight] = []
    seen: set[tuple[str, str]] = set()
    for match in _FLIGHT_RE.finditer(page_text.replace("\r", "")):
        key = (match["code"], match["dep"])
        if key in seen:
            continue
        seen.add(key)
        flights.append(
            Flight(
                airline=match["airline"].strip(),
                code=match["code"].replace(" | ", " + "),
                departure=match["dep"],
                arrival=match["arr"],
                origin=match["src"],
                destination=match["dst"],
                duration=match["duration"],
                stops=match["stops"].strip(),
                fare_inr=int(match["fare"].replace(",", "")),
            )
        )
    return flights


def best_flight(flights: list[Flight]) -> Flight | None:
    """The cheapest non-stop flight, or the cheapest of all if none is non-stop."""
    if not flights:
        return None
    direct = [f for f in flights if f.non_stop]
    return min(direct or flights, key=lambda f: f.fare_inr)
