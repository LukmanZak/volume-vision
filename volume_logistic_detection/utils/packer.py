"""Pack boxes into a truck box the way a loader would, one item at a time.

Items are placed in the order given (the order they were first seen). Each item
goes to the lowest free spot nearest the front wall (x = 0), is then dropped and
slid tight against whatever is below and behind it, and may rotate 90 degrees on
the floor. Items stack on top of each other as long as enough of their base rests
on the floor or on the boxes below, so nothing hangs in the air.

An item's spot depends only on the items before it, so earlier items never move
when later ones arrive. Pure Python, no Odoo. Lengths are centimetres.

Placement dict keys: id, x, y, z, l, w, h, fitted.
Item dict keys: id, l, w, h.
Axes: x along the truck length, y across the width, z up the height.
"""

EPS = 1e-6
# Share of an item's base that must rest on the floor or on boxes below it.
MIN_SUPPORT = 0.7


def pack(truck_l, truck_w, truck_h, items, min_support=MIN_SUPPORT):
    """Return one placement per item, in input order.

    fitted=False means the item did not fit in the remaining space. Such items
    are kept in the result so the UI can show them as overflow.
    """
    placed = []          # fitted boxes: (x, y, z, l, w, h)
    points = {(0.0, 0.0, 0.0)}  # corners where the next item may be tried
    placements = []
    for item in items:
        spot = _find_spot(truck_l, truck_w, truck_h, placed, points, item, min_support)
        if spot is None:
            placements.append({"id": item["id"], "x": None, "y": None, "z": None,
                               "l": item["l"], "w": item["w"], "h": item["h"], "fitted": False})
            continue
        x, y, z, dl, dw = spot
        h = item["h"]
        placed.append((x, y, z, dl, dw, h))
        points.update({(x + dl, y, z), (x, y + dw, z), (x, y, z + h)})
        placements.append({"id": item["id"], "x": x, "y": y, "z": z,
                           "l": dl, "w": dw, "h": h, "fitted": True})
    return placements


def fill_ratio(truck_l, truck_w, truck_h, placements):
    """Fraction of truck volume occupied by fitted items."""
    truck_volume = truck_l * truck_w * truck_h
    if truck_volume <= 0:
        return 0.0
    used = sum(p["l"] * p["w"] * p["h"] for p in placements if p["fitted"])
    return used / truck_volume


def fits_empty_truck(truck_l, truck_w, truck_h, item):
    """True if the item would fit in an empty truck, rotated on the floor if needed."""
    if item["h"] > truck_h + EPS:
        return False
    return ((item["l"] <= truck_l + EPS and item["w"] <= truck_w + EPS)
            or (item["w"] <= truck_l + EPS and item["l"] <= truck_w + EPS))


def _find_spot(truck_l, truck_w, truck_h, placed, points, item, min_support):
    l, w, h = item["l"], item["w"], item["h"]
    if h > truck_h + EPS:
        return None
    orientations = [(l, w)] if abs(l - w) < EPS else [(l, w), (w, l)]
    # Lowest first, then nearest the front wall, then along the left side.
    for px, py, pz in sorted(points, key=lambda p: (p[2], p[0], p[1])):
        for dl, dw in orientations:
            if px + dl > truck_l + EPS or py + dw > truck_w + EPS or pz + h > truck_h + EPS:
                continue
            if _collides(placed, px, py, pz, dl, dw, h):
                continue
            x, y, z = _settle(placed, px, py, pz, dl, dw, h)
            if _support(placed, x, y, z, dl, dw) >= min_support - EPS:
                return x, y, z, dl, dw
    return None


def _collides(placed, x, y, z, dl, dw, h):
    for bx, by, bz, bl, bw, bh in placed:
        if (x < bx + bl - EPS and x + dl > bx + EPS
                and y < by + bw - EPS and y + dw > by + EPS
                and z < bz + bh - EPS and z + h > bz + EPS):
            return True
    return False


def _settle(placed, x, y, z, dl, dw, h):
    """Drop the item, then slide it toward the wall behind and the side, until it rests."""
    moved = True
    while moved:
        moved = False
        # Down: land on the highest top below the item's footprint.
        floor = 0.0
        for bx, by, bz, bl, bw, bh in placed:
            if (x < bx + bl - EPS and x + dl > bx + EPS and y < by + bw - EPS and y + dw > by + EPS
                    and bz + bh <= z + EPS):
                floor = max(floor, bz + bh)
        if floor < z - EPS:
            z, moved = floor, True
        # Toward y = 0: stop at the nearest box that shares its x and z range.
        stop = 0.0
        for bx, by, bz, bl, bw, bh in placed:
            if (x < bx + bl - EPS and x + dl > bx + EPS and z < bz + bh - EPS and z + h > bz + EPS
                    and by + bw <= y + EPS):
                stop = max(stop, by + bw)
        if stop < y - EPS:
            y, moved = stop, True
        # Toward x = 0 (the front wall).
        stop = 0.0
        for bx, by, bz, bl, bw, bh in placed:
            if (y < by + bw - EPS and y + dw > by + EPS and z < bz + bh - EPS and z + h > bz + EPS
                    and bx + bl <= x + EPS):
                stop = max(stop, bx + bl)
        if stop < x - EPS:
            x, moved = stop, True
    return x, y, z


def _support(placed, x, y, z, dl, dw):
    """Fraction of the item's base resting on the floor or on the tops of boxes below."""
    if z <= EPS:
        return 1.0
    covered = 0.0
    for bx, by, bz, bl, bw, bh in placed:
        if abs(bz + bh - z) <= EPS:
            overlap_x = min(x + dl, bx + bl) - max(x, bx)
            overlap_y = min(y + dw, by + bw) - max(y, by)
            if overlap_x > 0 and overlap_y > 0:
                covered += overlap_x * overlap_y
    return covered / (dl * dw)
