"""Reading a cinema seat map from a screenshot.

BookMyShow draws its seat layout as a single image, so there is no page
element to click and nothing in the DOM to read. Asking the browsing model to
click it by eye does not work reliably — and worse, the model will report
seats it never selected. So this step is done here, deterministically:

* an available seat is a white box with a green outline,
* a selected seat is a solid green box,
* sold seats are grey and are ignored.

`find_seats` returns every seat it can see; `pick_run` chooses `n` free seats
next to each other. The caller clicks them and then calls `find_seats` again
to confirm they really turned green before reporting anything.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image

MIN_SIDE, MAX_SIDE = 20, 52


@dataclass(frozen=True)
class Seat:
    cx: int
    cy: int
    side: int
    selected: bool


def _is_green(pixel: Any) -> bool:
    r, g, b = pixel[:3]
    return g - r > 25 and g - b > 20


def find_seats(png: bytes) -> list[Seat]:
    """Every available or selected seat in the screenshot, in pixel coordinates."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    px = image.load()
    assert px is not None
    seen = bytearray(width * height)
    seats: list[Seat] = []
    for y0 in range(0, height, 2):
        for x0 in range(0, width, 2):
            if seen[y0 * width + x0] or not _is_green(px[x0, y0]):
                continue
            # Flood-fill one connected green shape and take its bounding box.
            stack = [(x0, y0)]
            seen[y0 * width + x0] = 1
            left = right = x0
            top = bottom = y0
            count = 0
            while stack:
                x, y = stack.pop()
                count += 1
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
                if right - left > MAX_SIDE * 3 or bottom - top > MAX_SIDE * 3:
                    continue  # far too big to be a seat; stop growing the box
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < width and 0 <= ny < height and not seen[ny * width + nx] and _is_green(px[nx, ny]):
                        seen[ny * width + nx] = 1
                        stack.append((nx, ny))
            w, h = right - left + 1, bottom - top + 1
            if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE and abs(w - h) <= 6):
                continue
            # Solid green box = selected. Outline only = available.
            filled = count / float(w * h)
            if filled > 0.55:
                seats.append(Seat((left + right) // 2, (top + bottom) // 2, w, True))
            elif 0.08 < filled < 0.45:
                seats.append(Seat((left + right) // 2, (top + bottom) // 2, w, False))
    return seats


def rows_of(seats: list[Seat]) -> list[list[Seat]]:
    """Seats grouped into rows (same height on screen), each sorted left to right."""
    rows: list[list[Seat]] = []
    for seat in sorted(seats, key=lambda s: (s.cy, s.cx)):
        if rows and abs(rows[-1][0].cy - seat.cy) <= 6:
            rows[-1].append(seat)
        else:
            rows.append([seat])
    return [sorted(row, key=lambda s: s.cx) for row in rows]


def pick_run(seats: list[Seat], n: int) -> list[Seat]:
    """`n` available seats next to each other, as near the middle as possible."""
    free = [s for s in seats if not s.selected]
    if not free or n < 1:
        return []
    mid_x = sum(s.cx for s in free) / len(free)
    mid_y = sum(s.cy for s in free) / len(free)
    best: tuple[float, list[Seat]] | None = None
    for row in rows_of(free):
        for i in range(len(row) - n + 1):
            run = row[i : i + n]
            gap = max((b.cx - a.cx for a, b in zip(run, run[1:], strict=False)), default=0)
            if gap > run[0].side * 1.6:
                continue  # an aisle or a sold seat sits between them
            centre = sum(s.cx for s in run) / n
            distance = abs(centre - mid_x) + 1.5 * abs(run[0].cy - mid_y)
            if best is None or distance < best[0]:
                best = (distance, run)
    return best[1] if best else []
