"""
generate_feeds.py — Build subscribable deadline feeds from data.json.

Writes static files to feeds/ (no dependencies beyond the standard library):
    feeds/<series>.ics|atom          all tracks of one series (series "all" = everything)
    feeds/<series>-<track>.ics|atom  one track type, e.g. feeds/icse-research.ics, feeds/all-tools.atom
    feeds/index.json                 which feeds exist (read by the Subscribe dialog)
    feeds/history.json               when each deadline first appeared / last moved

history.json is what makes feeds react to changes: a moved deadline gets a new Atom entry
("Moved from X to Y") and a bumped SEQUENCE / LAST-MODIFIED in the calendar. Output is
deterministic, so unchanged data produces no diff.

Usage:
    python generate_feeds.py

Environment variables:
    SITE_URL  — public base URL of the site (default: https://johmara.github.io/conference-deadlines/)
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

SITE_URL = os.environ.get("SITE_URL", "https://johmara.github.io/conference-deadlines/").rstrip("/") + "/"
OUT_DIR  = "feeds"
TITLE    = "SE Conference Deadlines"

# Calendars keep a short history; the Atom feed only lists what is coming up.
ICS_PAST_DAYS   = 30
ATOM_PAST_DAYS  = 7
ATOM_AHEAD_DAYS = 180
FLAG_DAYS       = 14   # how long "New"/"Moved" stays in an Atom entry title

TRACKS = {"research": "Research", "short": "Short/NIER", "tools": "Tools", "doctoral": "Doctoral"}

# ── Data ─────────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _series(conf: dict) -> tuple[str, str]:
    """('ICSE', 'icse') from 'ICSE 2027'."""
    display = re.sub(r"\s+\d{4}$", "", conf["key"])
    return display, _slug(display)


def _deadlines(conf: dict) -> list[dict]:
    """One entry per abstract/submission date, including multi-round tracks."""
    out, seen = [], set()
    for track in conf.get("tracks", []):
        name = track.get("track_name") or "Papers"
        rounds = track.get("rounds") or [track]
        multi = len(track.get("rounds") or []) > 1
        for i, rnd in enumerate(rounds, 1):
            label = f"{name} (round {i})" if multi else name
            for kind in ("abstract", "submission"):
                day = rnd.get(kind)
                if not day:
                    continue
                uid = f"{_slug(conf['key'])}-{_slug(name)}-r{i}-{kind}"
                while uid in seen:
                    uid += "-x"
                seen.add(uid)
                out.append(dict(
                    day=day,
                    uid=uid + "@se-conferences",
                    track_type=track.get("track_type"),
                    summary=f"{conf['key']} — {label}: {kind} deadline",
                    track=name,
                    kind=kind,
                ))
    return out


def _collect(data: list[dict], series_slug: str | None, track_type: str | None) -> list[tuple[dict, dict]]:
    items = []
    for conf in data:
        if series_slug and _series(conf)[1] != series_slug:
            continue
        for dl in _deadlines(conf):
            if track_type and dl["track_type"] != track_type:
                continue
            items.append((conf, dl))
    items.sort(key=lambda it: (it[1]["day"], it[1]["summary"]))
    return items


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _apply_history(data: list[dict], history: dict | None, now: datetime) -> dict:
    """Stamp every deadline with changed_at / seq / prev_day / new and return the updated history."""
    bootstrap = history is None
    old = history or {}
    new_hist = {}
    for conf in data:
        for dl in _deadlines(conf):
            prev = old.get(dl["uid"])
            if prev is None:
                rec = dict(day=dl["day"], changed_at=_iso(_stamp(conf) if bootstrap else now), seq=0, prev_day=None, new=not bootstrap)
            elif prev["day"] != dl["day"]:
                rec = dict(day=dl["day"], changed_at=_iso(now), seq=prev.get("seq", 0) + 1, prev_day=prev["day"], new=False)
            else:
                rec = prev
            new_hist[dl["uid"]] = rec
    return new_hist


def _decorate(items: list[tuple[dict, dict]], history: dict) -> None:
    for _, dl in items:
        dl.update(history[dl["uid"]])


def _description(conf: dict, dl: dict) -> str:
    parts = [f"Track: {dl['track']}", "Deadline: 23:59 AoE (UTC-12)"]
    if conf.get("location"):
        parts.append(f"Location: {conf['location']}")
    if conf.get("url"):
        parts.append(f"Info: {conf['url']}")
    return "\n".join(parts)


def _stamp(conf: dict) -> datetime:
    raw = conf.get("last_updated")
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime(2000, 1, 1, tzinfo=timezone.utc)

# ── iCalendar ────────────────────────────────────────────────────────────────

def _ical_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold to 75 octets per RFC 5545."""
    raw, out = line.encode("utf-8"), []
    while len(raw) > 75:
        cut = 75
        while raw[cut] & 0xC0 == 0x80:  # don't split a multi-byte character
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def _ics_time(iso: str) -> str:
    return iso.replace('-', '').replace(':', '')


def build_ics(items: list[tuple[dict, dict]], name: str, today: date) -> str:
    since = (today - timedelta(days=ICS_PAST_DAYS)).isoformat()
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//SE Conference Deadlines//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ical_escape(name)}",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D", "X-PUBLISHED-TTL:PT12H",
    ]
    for conf, dl in items:
        if dl["day"] < since:
            continue
        start = dl["day"].replace("-", "")
        end = (date.fromisoformat(dl["day"]) + timedelta(days=1)).strftime("%Y%m%d")
        lines += [
            "BEGIN:VEVENT",
            _fold(f"UID:{dl['uid']}"),
            f"DTSTAMP:{_ics_time(dl['changed_at'])}",
            f"LAST-MODIFIED:{_ics_time(dl['changed_at'])}",
            f"SEQUENCE:{dl['seq']}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            _fold(f"SUMMARY:{_ical_escape(dl['summary'])}"),
            _fold(f"DESCRIPTION:{_ical_escape(_description(conf, dl))}"),
        ]
        if conf.get("url"):
            lines.append(_fold(f"URL:{conf['url']}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

# ── Atom ─────────────────────────────────────────────────────────────────────

def build_atom(items: list[tuple[dict, dict]], name: str, self_path: str, today: date) -> str:
    lo = (today - timedelta(days=ATOM_PAST_DAYS)).isoformat()
    hi = (today + timedelta(days=ATOM_AHEAD_DAYS)).isoformat()
    shown = [(c, d) for c, d in items if lo <= d["day"] <= hi]
    updated = max((d["changed_at"] for _, d in shown), default="2000-01-01T00:00:00Z")
    flag_since = _iso(datetime.combine(today, datetime.min.time(), timezone.utc) - timedelta(days=FLAG_DAYS))

    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{escape(name)}</title>",
        f'  <id>{escape(SITE_URL + self_path)}</id>',
        f'  <link rel="self" href="{escape(SITE_URL + self_path)}"/>',
        f'  <link rel="alternate" href="{escape(SITE_URL)}"/>',
        f"  <updated>{updated}</updated>",
    ]
    for conf, dl in shown:
        recent = dl["changed_at"] >= flag_since
        flag = ""
        note = ""
        if recent and dl["seq"] > 0:
            flag, note = "Moved: ", f"Moved from {dl['prev_day']} to {dl['day']}.\n"
        elif recent and dl["new"]:
            flag = "New: "
        text = note + f"Deadline: {dl['day']} (23:59 AoE)\n" + _description(conf, dl)
        out += [
            "  <entry>",
            f"    <title>{escape(flag + dl['day'] + ' — ' + dl['summary'])}</title>",
            f"    <id>{escape(SITE_URL + 'deadline/' + dl['uid'] + '/' + dl['day'])}</id>",
            f'    <link href="{escape(conf.get("url") or SITE_URL)}"/>',
            f"    <updated>{dl['changed_at']}</updated>",
            f'    <summary type="text">{escape(text)}</summary>',
            "  </entry>",
        ]
    out.append("</feed>")
    return "\n".join(out) + "\n"

# ── Main ─────────────────────────────────────────────────────────────────────

def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def main() -> None:
    with open("data.json", encoding="utf-8") as f:
        data = json.load(f)

    os.makedirs(OUT_DIR, exist_ok=True)
    hist_path = f"{OUT_DIR}/history.json"
    history = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else None
    now = datetime.now(timezone.utc)
    history = _apply_history(data, history, now)
    _write(hist_path, json.dumps(history, indent=1, sort_keys=True) + "\n")

    series = {}
    for conf in data:
        display, slug = _series(conf)
        series[slug] = display

    today = now.date()
    names = {None: TITLE, **{sl: f"{d} deadlines" for sl, d in series.items()}}
    available = []
    for series_slug in [None, *sorted(series)]:
        for track_type in [None, *TRACKS]:
            items = _collect(data, series_slug, track_type)
            if not items:
                continue
            _decorate(items, history)
            fname = "-".join([series_slug or "all"] + ([track_type] if track_type else []))
            name = names[series_slug] + (f" — {TRACKS[track_type]}" if track_type else "")
            _write(f"{OUT_DIR}/{fname}.ics", build_ics(items, name, today))
            _write(f"{OUT_DIR}/{fname}.atom", build_atom(items, name, f"{OUT_DIR}/{fname}.atom", today))
            available.append(fname)

    index = dict(
        series=[{"slug": sl, "display": d} for sl, d in sorted(series.items(), key=lambda kv: kv[1].lower())],
        tracks=[{"id": k, "label": v} for k, v in TRACKS.items()],
        available=available,
    )
    _write(f"{OUT_DIR}/index.json", json.dumps(index, indent=1) + "\n")
    print(f"Wrote {len(available)} feed pair(s) to {OUT_DIR}/")


if __name__ == "__main__":
    main()
