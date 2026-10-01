from __future__ import annotations

import httpx
import pytest
from google.transit import gtfs_realtime_pb2

from subwaypulse import feeds
from subwaypulse.feeds import Snapshot, parse, snapshot
from subwaypulse.metrics import route_health
from subwaypulse.report import render, sparkline, station_name
from subwaypulse.store import Store

NOW = 1_790_000_000


def feed_bytes(trains: dict[str, list[tuple[str, list[tuple[str, int]]]]], ts: int = NOW) -> bytes:
    """route -> [(trip_id, [(stop_id, minutes_from_now), ...])] as a GTFS-realtime protobuf."""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "1.0"
    msg.header.timestamp = ts
    for route, trips in trains.items():
        for trip_id, stops in trips:
            e = msg.entity.add()
            e.id = trip_id
            e.trip_update.trip.trip_id = trip_id
            e.trip_update.trip.route_id = route
            for stop, minutes in stops:
                stu = e.trip_update.stop_time_update.add()
                stu.stop_id = stop
                stu.arrival.time = ts + minutes * 60
    # A vehicle-only entity and a trip without a route should both be ignored
    msg.entity.add(id="veh").vehicle.trip.trip_id = "x"
    msg.entity.add(id="noroute").trip_update.trip.trip_id = "y"
    return msg.SerializeToString()


def evenly(route: str, stop: str, every: int, count: int) -> list[tuple[str, list[tuple[str, int]]]]:
    return [(f"{route}-{i}", [(stop, every * i + 1)]) for i in range(count)]


def test_parse_extracts_arrivals_and_trips() -> None:
    ts, arrivals, trips = parse(feed_bytes({"A": evenly("A", "A27N", 5, 4)}))
    assert ts == NOW
    assert len(arrivals) == 4
    assert trips == {"A": {"A-0", "A-1", "A-2", "A-3"}}


def snap_of(payload: bytes) -> Snapshot:
    ts, arrivals, trips = parse(payload)
    return Snapshot(taken_at=ts, arrivals=arrivals, trips=trips)


def test_even_service_is_good_with_correct_headway() -> None:
    [h] = route_health(snap_of(feed_bytes({"L": evenly("L", "L08N", 6, 8)})))
    assert h.trains == 8
    assert h.median_headway_min == 6.0
    assert h.worst_gap_min == 6.0
    assert h.status == "good"


def test_a_long_gap_is_flagged_with_its_station() -> None:
    trips = [("Q-0", [("R16N", 2)]), ("Q-1", [("R16N", 8)]), ("Q-2", [("R16N", 14)]), ("Q-3", [("R16N", 45)])]
    [h] = route_health(snap_of(feed_bytes({"Q": trips})))
    assert h.worst_gap_min == 31.0
    assert h.worst_gap_stop == "R16N"
    assert h.status == "gaps"


def test_arrivals_outside_the_window_or_sparse_platforms_are_ignored() -> None:
    trips = [*evenly("G", "G22S", 4, 5), ("G-late", [("G22S", 200)]), ("G-x", [("G30N", 3)])]
    [h] = route_health(snap_of(feed_bytes({"G": trips})))
    assert h.worst_gap_min == 4.0  # the 200-minute arrival is beyond the one-hour window
    assert h.platforms_measured == 1  # G30N has a single arrival, too few to judge
    assert h.trains == 7


def test_route_with_trips_but_no_measurable_platforms_reports_limited_data() -> None:
    [h] = route_health(snap_of(feed_bytes({"FS": [("FS-0", [("S01N", 2)])]})))
    assert h.trains == 1
    assert h.status == "limited data"


def test_snapshot_survives_a_failing_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feeds.time, "sleep", lambda _: None)
    good = feed_bytes({"L": evenly("L", "L08N", 6, 4)})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("gtfs-l"):
            return httpx.Response(200, content=good)
        if request.url.path.endswith("gtfs-g"):
            return httpx.Response(200, content=b"\xff\xfe not protobuf")
        return httpx.Response(503)

    snap = snapshot(httpx.Client(transport=httpx.MockTransport(handler)), feeds=["L", "G", "ACEH"])
    assert set(snap.trips) == {"L"}
    assert set(snap.errors) == {"G", "ACEH"}
    assert snap.taken_at == NOW


def test_store_is_idempotent_and_prunes() -> None:
    store = Store()
    h = route_health(snap_of(feed_bytes({"L": evenly("L", "L08N", 6, 4)})))
    store.record(NOW, h)
    store.record(NOW, h)
    store.record(NOW + 1800, h)
    assert store.snapshots() == 2
    assert store.history(NOW)["L"] == [(NOW, 4), (NOW + 1800, 4)]
    assert store.prune(NOW + 1) == 1
    assert store.latest()[0]["taken_at"] == NOW + 1800


def test_report_uses_station_names_line_colors_and_escapes() -> None:
    store = Store()
    trips = [("A-0", [("A27N", 2)]), ("A-1", [("A27N", 7)]), ("A-2", [("A27N", 40)])]
    store.record(NOW, route_health(snap_of(feed_bytes({"A": trips}))))
    page = render(store.latest(), store.history(0), store.snapshots(), {"<feed>": "down"})
    assert "42 St-Port Authority Bus Terminal (northbound)" in page
    assert "background:#0062CF" in page  # official A/C/E blue
    assert "&lt;feed&gt;" in page
    assert "<feed>" not in page


def test_helpers() -> None:
    assert station_name(None) == "-"
    assert station_name("ZZZ9X") == "ZZZ9X"
    assert station_name("ZZZ9S") == "ZZZ9 (southbound)"
    assert sparkline([(0, 1)]) == ""
    assert "<polyline" in sparkline([(0, 1), (1, 5), (2, 3)])
    assert "No snapshots yet" in render([], {}, 0)


def test_cli_end_to_end(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[no-untyped-def]
    from subwaypulse import cli

    monkeypatch.setattr(cli, "snapshot", lambda: snap_of(feed_bytes({"L": evenly("L", "L08N", 6, 6)})))
    db, out = tmp_path / "h.sqlite", tmp_path / "site" / "index.html"
    assert cli.main(["--db", str(db), "--out", str(out)]) == 0
    assert "trains=6" in capsys.readouterr().out
    assert "subwaypulse" in out.read_text(encoding="utf-8")
    assert Store(db).snapshots() == 1
