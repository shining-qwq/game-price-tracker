"""Read-only SVG coordinates. Prices remain integer cents, even during scaling."""
from datetime import datetime, timedelta, timezone


WIDTH, HEIGHT = 960, 340
TOP, RIGHT, BOTTOM = 36, 936, 252
MAX_MARKERS = 80


def _utc_time(value):
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    return timestamp


def _microseconds(delta):
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _coordinate(hundredths):
    whole, fraction = divmod(hundredths, 100)
    return f"{whole}.{fraction:02d}"


def _axis_step(highest_cents):
    # Four equal intervals, rounded up to readable 1/2/5 multiples. A zero-only
    # history still gets a nonzero axis; all arithmetic uses Python integers.
    padded = max(100, (highest_cents * 11 + 9) // 10)
    rough_step = (padded + 3) // 4
    magnitude = 10 ** (len(str(rough_step)) - 1)
    return next(multiplier * magnitude for multiplier in (1, 2, 5, 10)
                if multiplier * magnitude >= rough_step)


def build_price_chart(history):
    """Copy/sort existing observations, without querying or modifying the DB.

    Same-time records follow database ID order. Full history is kept in a single
    polyline; only the circle markers are limited for dense histories.
    """
    ordered = sorted(((_utc_time(row["observed_at"]), row) for row in history),
                     key=lambda item: (item[0], item[1]["id"]))
    if not ordered:
        return {"count": 0}

    lowest = min(row["price_cents"] for _, row in ordered)
    highest = max(row["price_cents"] for _, row in ordered)
    step = _axis_step(highest)
    ceiling = step * 4
    # Reserve enough room for the longest exact USD label, including int64
    # prices. This is layout sizing only; price formatting stays in Jinja.
    label_length = len(f"${ceiling // 100:,}.{ceiling % 100:02d}")
    left = max(88, label_length * 8 + 20)
    first, last = ordered[0][0], ordered[-1][0]
    span = _microseconds(last - first)

    def x_position(timestamp):
        if not span:
            return _coordinate((left + RIGHT) * 50)
        offset = _microseconds(timestamp - first)
        return _coordinate(left * 100 + offset * (RIGHT - left) * 100 // span)

    def y_position(cents):
        return _coordinate(BOTTOM * 100 - cents * (BOTTOM - TOP) * 100 // ceiling)

    points = []
    markers = []
    for index, (timestamp, row) in enumerate(ordered):
        x, y = x_position(timestamp), y_position(row["price_cents"])
        points.append(f"{x},{y}")
        if len(ordered) <= MAX_MARKERS or index in (0, len(ordered) - 1):
            markers.append({"x": x, "y": y, "observed_at": row["observed_at"],
                            "price_cents": row["price_cents"]})

    tick_times = [first] if not span else [first, first + timedelta(microseconds=span // 2), last]
    return {
        "count": len(ordered), "width": WIDTH, "height": HEIGHT,
        "left": left, "right": RIGHT, "top": TOP, "bottom": BOTTOM,
        "points": " ".join(points), "markers": markers,
        "markers_limited": len(ordered) > MAX_MARKERS,
        "y_ticks": [{"y": y_position(step * index), "price_cents": step * index}
                    for index in range(5)],
        "x_ticks": [{"x": x_position(timestamp), "date": timestamp.strftime("%Y-%m-%d"),
                     "time": timestamp.strftime("%H:%M:%S")}
                    for timestamp in tick_times],
        "lowest_cents": lowest, "highest_cents": highest,
        "first_time": first.isoformat(sep=" "), "last_time": last.isoformat(sep=" "),
    }
