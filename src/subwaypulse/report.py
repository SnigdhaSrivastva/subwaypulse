"""Static HTML status board: one card per line with trains running, waits, worst gap, and a 24h sparkline."""

from __future__ import annotations

import csv
import html
from datetime import UTC, datetime
from importlib import resources
from typing import Any
from zoneinfo import ZoneInfo

NYC = ZoneInfo("America/New_York")


def _load(name: str) -> list[dict[str, str]]:
    text = resources.files("subwaypulse").joinpath("data", name).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


STATIONS = {r["stop_id"]: r["name"] for r in _load("stations.csv")}
ROUTES = {r["route_id"]: r for r in _load("routes.csv")}


def station_name(platform_id: str | None) -> str:
    """'A27N' -> '42 St-Port Authority Bus Terminal (northbound)'."""
    if not platform_id:
        return "-"
    parent, direction = platform_id[:-1], platform_id[-1]
    bound = {"N": "northbound", "S": "southbound"}.get(direction)
    if not bound:
        return STATIONS.get(platform_id, platform_id)
    # Some real-time stop ids are missing from the static station list; keep the code readable.
    return f"{STATIONS.get(parent, parent)} ({bound})"


def sparkline(points: list[tuple[int, int]], width: int = 140, height: int = 28) -> str:
    if len(points) < 2:
        return ""
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = width / (len(values) - 1)
    coords = " ".join(f"{i * step:.1f},{height - 2 - (v - lo) / span * (height - 4):.1f}" for i, v in enumerate(values))
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-hidden="true">'
            f'<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="{coords}"/></svg>')


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>subwaypulse · NYC subway service right now</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--text:#1c1c1e;--muted:#6b6b70;--border:#e3e3e0;--good:#2b8a3e;--warn:#e67700;--bad:#c92a2a}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--text:#ececef;--muted:#9a9aa2;--border:#2e2e33;--good:#69db7c;--warn:#ffc078;--bad:#ff8787}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 16px 48px}h1{margin:0;font-size:26px}.muted{color:var(--muted)}.small{font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-top:18px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}
.head{display:flex;align-items:center;gap:10px}
.bullet{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:17px;flex:none}
.status{margin-left:auto;font-size:12px;font-weight:700;text-transform:uppercase}
.good{color:var(--good)}.gaps{color:var(--warn)}.nos{color:var(--bad)}.lim{color:var(--muted)}
.nums{display:flex;gap:16px;margin:10px 0 4px}.nums b{display:block;font-size:20px}
.spark{color:var(--muted);margin-top:6px}footer{margin-top:28px;text-align:center}
a{color:inherit}
</style></head><body><main>
<h1>subwaypulse</h1>
<p class="muted">Live NYC subway service from the MTA's real-time feeds, as of <b>__TIME__</b>.
Headway is the gap between consecutive trains at a platform over the next hour. Sparklines show trains running over the last 24 hours.</p>
<p class="small muted">__SUMMARY__</p>
<div class="grid">__CARDS__</div>
<footer class="muted small">Data: MTA GTFS-realtime · refreshed every 30 minutes · <a href="https://github.com/SnigdhaSrivastva/subwaypulse">source</a></footer>
</main></body></html>"""

STATUS_CLASS = {"good": "good", "gaps": "gaps", "no service": "nos", "limited data": "lim"}


def _route_sort(route: str) -> tuple[int, str]:
    return (0, route.zfill(3)) if route[0].isdigit() else (1, route)


def render(latest: list[dict[str, Any]], history: dict[str, list[tuple[int, int]]], snapshots: int,
           errors: dict[str, str] | None = None) -> str:
    if not latest:
        return PAGE.replace("__TIME__", "-").replace("__SUMMARY__", "No snapshots yet.").replace("__CARDS__", "")
    taken = datetime.fromtimestamp(latest[0]["taken_at"], tz=UTC).astimezone(NYC)
    cards = []
    for row in sorted(latest, key=lambda r: _route_sort(r["route"])):
        meta = ROUTES.get(row["route"], {"color": "808183", "text_color": "FFFFFF", "long_name": ""})
        status = row["status"]
        median = "-" if row["median_headway"] is None else f'{row["median_headway"]:.0f} min'
        worst = "-" if row["worst_gap"] is None else f'{row["worst_gap"]:.0f} min'
        cards.append(f"""<article class="card">
<div class="head"><span class="bullet" style="background:#{html.escape(meta['color'])};color:#{html.escape(meta['text_color'])}">{html.escape(row['route'])}</span>
<span class="small muted">{html.escape(meta['long_name'])}</span><span class="status {STATUS_CLASS.get(status, 'lim')}">{html.escape(status)}</span></div>
<div class="nums"><div><b>{row['trains']}</b><span class="small muted">trains</span></div>
<div><b>{median}</b><span class="small muted">typical wait</span></div><div><b>{worst}</b><span class="small muted">worst gap</span></div></div>
<div class="small muted">worst at {html.escape(station_name(row['worst_gap_stop']))}</div>
<div class="spark">{sparkline(history.get(row['route'], []))}</div></article>""")
    trains = sum(r["trains"] for r in latest)
    gaps = sum(1 for r in latest if r["status"] == "gaps")
    summary = f"{trains} trains running on {len(latest)} lines · {gaps} line(s) with service gaps · {snapshots} snapshots stored"
    if errors:
        summary += f" · feeds unavailable: {', '.join(sorted(errors))}"
    return (PAGE.replace("__TIME__", taken.strftime("%a %b %d, %I:%M %p ET"))
                .replace("__SUMMARY__", html.escape(summary))
                .replace("__CARDS__", "\n".join(cards)))
