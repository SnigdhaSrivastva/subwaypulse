"""Service metrics from a snapshot: trains running, typical headway, and the worst current gap per line.

Headway is measured per platform (station + direction): the time between consecutive
predicted arrivals of the same line within a look-ahead window. The median across
platforms is the typical wait; the largest is the worst gap a rider faces right now.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

from subwaypulse.feeds import Snapshot

WINDOW_S = 60 * 60  # look one hour ahead
MIN_ARRIVALS = 3  # need a few trains at a platform before trusting its gaps


@dataclass(frozen=True)
class RouteHealth:
    route: str
    trains: int
    median_headway_min: float | None
    worst_gap_min: float | None
    worst_gap_stop: str | None
    platforms_measured: int

    @property
    def status(self) -> str:
        if self.trains == 0:
            return "no service"
        if self.median_headway_min is None or self.worst_gap_min is None:
            return "limited data"
        if self.worst_gap_min >= max(20.0, 2.5 * self.median_headway_min):
            return "gaps"
        return "good"


def route_health(snap: Snapshot) -> list[RouteHealth]:
    by_platform: dict[tuple[str, str], list[int]] = defaultdict(list)
    horizon = snap.taken_at + WINDOW_S
    for a in snap.arrivals:
        if snap.taken_at <= a.time <= horizon:
            by_platform[(a.route, a.stop_id)].append(a.time)

    gaps_by_route: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (route, stop), times in by_platform.items():
        times = sorted(set(times))
        if len(times) < MIN_ARRIVALS:
            continue
        # Include the wait until the first train, so a platform with nothing coming soon counts as a gap.
        waits = [times[0] - snap.taken_at, *(b - a for a, b in pairwise(times))]
        gaps_by_route[route].extend((w / 60, stop) for w in waits)

    results = []
    for route in sorted(set(snap.trips) | set(gaps_by_route)):
        gaps = gaps_by_route.get(route, [])
        platforms = {s for _, s in gaps}
        if gaps:
            worst, worst_stop = max(gaps)
            median = statistics.median(g for g, _ in gaps)
        else:
            worst, worst_stop, median = None, None, None
        results.append(RouteHealth(
            route=route,
            trains=len(snap.trips.get(route, ())),
            median_headway_min=round(median, 1) if median is not None else None,
            worst_gap_min=round(worst, 1) if worst is not None else None,
            worst_gap_stop=worst_stop,
            platforms_measured=len(platforms),
        ))
    return results
