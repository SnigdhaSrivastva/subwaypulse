"""Fetch and decode the MTA's public GTFS-realtime subway feeds (no API key required)."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx
from google.transit import gtfs_realtime_pb2

log = logging.getLogger(__name__)

BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2F"
FEEDS = {
    "1234567S": BASE + "gtfs",
    "ACEH": BASE + "gtfs-ace",
    "BDFM": BASE + "gtfs-bdfm",
    "G": BASE + "gtfs-g",
    "JZ": BASE + "gtfs-jz",
    "L": BASE + "gtfs-l",
    "NQRW": BASE + "gtfs-nqrw",
    "SIR": BASE + "gtfs-si",
}
RETRYABLE = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Arrival:
    route: str
    trip_id: str
    stop_id: str  # platform id, e.g. "A27N" (parent station + direction)
    time: int  # predicted arrival, unix seconds


@dataclass
class Snapshot:
    taken_at: int
    arrivals: list[Arrival] = field(default_factory=list)
    trips: dict[str, set[str]] = field(default_factory=dict)  # route -> active trip ids
    errors: dict[str, str] = field(default_factory=dict)


def parse(payload: bytes) -> tuple[int, list[Arrival], dict[str, set[str]]]:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    arrivals: list[Arrival] = []
    trips: dict[str, set[str]] = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        route = tu.trip.route_id
        if not route:
            continue
        trips.setdefault(route, set()).add(tu.trip.trip_id)
        for stu in tu.stop_time_update:
            when = stu.arrival.time or stu.departure.time
            if when:
                arrivals.append(Arrival(route, tu.trip.trip_id, stu.stop_id, int(when)))
    return int(feed.header.timestamp), arrivals, trips


def fetch(client: httpx.Client, url: str, attempts: int = 3) -> bytes:
    for attempt in range(attempts):
        try:
            response = client.get(url)
            if response.status_code not in RETRYABLE:
                response.raise_for_status()
                return response.content
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
        time.sleep(2**attempt)
    response.raise_for_status()
    return response.content


def snapshot(client: httpx.Client | None = None, feeds: Iterable[str] = FEEDS) -> Snapshot:
    """Fetch all feeds in parallel; a failing feed is recorded, never fatal."""
    client = client or httpx.Client(timeout=30.0)
    snap = Snapshot(taken_at=0)

    def one(name: str) -> tuple[str, bytes | Exception]:
        try:
            return name, fetch(client, FEEDS[name])
        except (httpx.HTTPError, KeyError) as exc:
            return name, exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, result in pool.map(one, list(feeds)):
            if isinstance(result, Exception):
                snap.errors[name] = f"{type(result).__name__}: {result}"[:200]
                log.warning("feed %s failed: %s", name, result)
                continue
            try:
                ts, arrivals, trips = parse(result)
            except Exception as exc:  # malformed protobuf
                snap.errors[name] = f"parse error: {exc}"[:200]
                continue
            snap.taken_at = max(snap.taken_at, ts)
            snap.arrivals.extend(arrivals)
            for route, ids in trips.items():
                snap.trips.setdefault(route, set()).update(ids)
    if not snap.taken_at:
        snap.taken_at = int(time.time())
    return snap
