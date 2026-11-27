"""The parts of the movie and flight look-ups that are decided by code.

The browsing itself needs a real browser and a network, so it is not tested
here. What is tested is everything that turns a page into the facts shown to
the user — because those must not be left to a model's say-so.
"""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from arena.live.agent import booking_kind, expected_tools_for, is_shop_request  # noqa: E402
from arena.live.flightparse import best_flight, parse_flights  # noqa: E402
from arena.live.seatmap import find_seats, pick_run, rows_of  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mmt_results.txt"
GREEN, SOLD, PICKED = (30, 168, 60), (229, 229, 229), (31, 173, 62)


def seat_map(rows: list[str]) -> bytes:
    """Draw a seat map. 'o' free, 'x' sold, 's' selected, ' ' an aisle."""
    image = Image.new("RGB", (60 + 36 * max(len(r) for r in rows), 60 + 36 * len(rows)), "white")
    draw = ImageDraw.Draw(image)
    for j, row in enumerate(rows):
        for i, kind in enumerate(row):
            box = (30 + 36 * i, 30 + 36 * j, 30 + 36 * i + 29, 30 + 36 * j + 29)
            if kind == "o":
                draw.rectangle(box, outline=GREEN, width=2)
            elif kind == "x":
                draw.rectangle(box, fill=SOLD)
            elif kind == "s":
                draw.rectangle(box, fill=PICKED)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_free_sold_and_selected_seats_are_told_apart():
    seats = find_seats(seat_map(["ooxxoo", "xsxxxo"]))
    assert sum(1 for s in seats if not s.selected) == 5
    assert sum(1 for s in seats if s.selected) == 1
    assert [len(row) for row in rows_of(seats)] == [4, 2]


def test_picked_seats_are_next_to_each_other_not_across_a_gap():
    seats = find_seats(seat_map(["oxo", "oxoo"]))
    run = pick_run(seats, 2)
    assert len(run) == 2
    assert run[0].cy == run[1].cy and run[1].cx - run[0].cx < 50


def test_no_run_when_the_hall_cannot_seat_the_group_together():
    assert pick_run(find_seats(seat_map(["oxoxo"])), 2) == []
    assert pick_run(find_seats(seat_map(["xxx"])), 1) == []


def test_flights_are_parsed_from_the_page_text():
    flights = parse_flights(FIXTURE.read_text(encoding="utf-8"))
    assert flights, "the saved results page lists flights"
    for flight in flights:
        assert flight.origin == "BLR" and flight.destination == "DEL"
        assert flight.fare_inr > 1000
        assert len(flight.departure) == 5 and len(flight.arrival) == 5
    assert best_flight(flights) in flights


def test_cheapest_non_stop_wins_over_a_cheaper_connection():
    text = (
        "IndiGo\n6E-5031 | 6E-330\n16:15\nBLR\n\n06h 25m\n\n1 stop • BOM\n\n22:40\nDEL\n₹ 9,665\nVIEW PRICES\n"
        "SpiceJet\nSG-619\n21:30\nBLR\n\n03h\n\nNon stop\n\n00:30\n+ 1\nDAY\nDEL\nFree Seat\n₹ 10,243\nVIEW PRICES\n"
        "IndiGo\n6E-2455\n05:40\nBLR\n\n02h 35m\n\nNon stop\n\n08:15\nDEL\n₹ 10,611\nVIEW PRICES\n"
    )
    flights = parse_flights(text)
    assert [f.code for f in flights] == ["6E-5031 + 6E-330", "SG-619", "6E-2455"]
    best = best_flight(flights)
    assert best is not None and best.code == "SG-619" and best.arrival == "00:30" and best.fare_inr == 10243


def test_nothing_parsed_from_an_unrelated_page():
    assert parse_flights("Please log in to continue") == []
    assert best_flight([]) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tomorrow", datetime.date(2026, 11, 28)),
        ("12 Dec", datetime.date(2026, 12, 12)),
        ("2026-12-12", datetime.date(2026, 12, 12)),
        ("12/12/2026", datetime.date(2026, 12, 12)),
        ("3rd jan", datetime.date(2027, 1, 3)),
        ("", datetime.date(2026, 12, 11)),
        ("whenever", datetime.date(2026, 12, 11)),
    ],
)
def test_travel_dates(text, expected):
    browser = pytest.importorskip("arena.live.browser")
    assert browser.parse_travel_date(text, datetime.date(2026, 11, 27)) == expected


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Which cinemas in Koramangala are showing Dhurandhar tonight?", "movies"),
        ("Check movie tickets in Bengaluru and pick 2 seats", "movies"),
        ("Find me a flight from Bengaluru to Delhi tomorrow", "flights"),
        ("Buy me a green t-shirt for around 500", ""),
    ],
)
def test_look_ups_are_not_mistaken_for_wallet_purchases(text, kind):
    assert booking_kind(text) == kind
    assert is_shop_request(text) is (kind == "")
    if kind:
        assert {"browse_movie_tickets", "browse_flights"} <= expected_tools_for(text)


def test_price_tiers_are_read_from_the_seat_count_dialog():
    browser = pytest.importorskip("arena.live.browser")
    text = "How many seats?\n1\n2\nRECLINER ROWS\n₹750\nFILLING FAST\nPRIME ROWS\n₹360\nAVAILABLE\nSelect Seats"
    assert browser.price_tiers(text) == "Recliner INR 750 (filling fast); Prime INR 360 (available)"
    assert browser.price_tiers("nothing here") == ""
