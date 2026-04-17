"""
generate_data.py — Standalone SE conference data scraper.

Scrapes conf.researchr.org for conference dates and tracks, then writes
a data.json file in the current directory. No FastAPI, no SQLite.

Usage:
    python generate_data.py

Environment variables:
    YEAR_RANGE  — how many years ahead to scan (default: 2)

Progress is printed to stderr; stdout is kept clean for piping.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional

import httpx
import yaml
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

YEAR_RANGE = int(os.environ.get("YEAR_RANGE", "2"))
HEADERS    = {"User-Agent": "SE-Conference-Tracker/1.0 (+research-tool)"}

# ── Series ────────────────────────────────────────────────────────────────────

def _load_series() -> list[tuple[str, str, str]]:
    """Load series from series-config.yaml if present, else use hardcoded defaults."""
    config_path = os.path.join(os.path.dirname(__file__), "series-config.yaml")
    if os.path.exists(config_path):
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return [(s["slug"], s["display"], s.get("full_name", "")) for s in data]
    # Fallback hardcoded list
    return [
        ("icse",        "ICSE",        "IEEE/ACM Intl. Conf. on Software Engineering"),
        ("fse",         "FSE",         "ACM Foundations of Software Engineering (ESEC/FSE)"),
        ("ase",         "ASE",         "IEEE/ACM Intl. Conf. on Automated Software Engineering"),
        ("saner",       "SANER",       "IEEE Intl. Conf. on Software Analysis, Evolution & Reengineering"),
        ("icsme",       "ICSME",       "IEEE Intl. Conf. on Software Maintenance and Evolution"),
        ("models",      "MODELS",      "ACM/IEEE Intl. Conf. on Model Driven Engineering Languages & Systems"),
        ("issta",       "ISSTA",       "ACM Intl. Symposium on Software Testing and Analysis"),
        ("esem",        "ESEM",        "IEEE/ACM Intl. Symposium on Empirical Software Engineering and Measurement"),
        ("ease",        "EASE",        "Intl. Conf. on Evaluation and Assessment in Software Engineering"),
        ("icst",        "ICST",        "IEEE Intl. Conf. on Software Testing, Verification and Validation"),
        ("scam",        "SCAM",        "IEEE Intl. Working Conf. on Source Code Analysis and Manipulation"),
        ("icpc",        "ICPC",        "IEEE/ACM Intl. Conf. on Program Comprehension"),
        ("msr",         "MSR",         "IEEE/ACM Intl. Conf. on Mining Software Repositories"),
        ("variability", "VARIABILITY", "Intl. Conf. on Software and Systems Variability (SPLC/VaMoS/ICSR)"),
        ("issre",       "ISSRE",       "IEEE Intl. Symposium on Software Reliability Engineering"),
        ("wcre",        "WCRE",        "IEEE Working Conf. on Reverse Engineering"),
        ("splc",        "SPLC",        "ACM/IEEE Intl. Systems and Software Product Line Conference"),
    ]

DEFAULT_SERIES: list[tuple[str, str, str]] = _load_series()
DEACTIVATED_DISPLAYS: set[str] = set()  # populated during init

# ── Seed data ─────────────────────────────────────────────────────────────────

def _load_seed() -> list[dict]:
    """Load seed conference data from conferences-seed.yaml in the same directory."""
    seed_path = os.path.join(os.path.dirname(__file__), "conferences-seed.yaml")
    with open(seed_path, encoding="utf-8") as f:
        entries = yaml.safe_load(f)
    result = []
    for entry in entries:
        conf = {k: v for k, v in entry.items() if k != "tracks"}
        conf["tracks"] = [dict(t) for t in entry.get("tracks", [])]
        result.append(conf)
    return result

# ── Scraper helpers ───────────────────────────────────────────────────────────

DATE_RE = re.compile(
    r'(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,.\s]+)?'   # optional weekday prefix
    r'(\d{1,2})\s+'
    r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+'
    r'(\d{4})',
    re.IGNORECASE,
)
MONTH = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
         'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}

TRACK_TYPES = {
    "research": ["research", "technical", "full paper", "regular paper", "main track",
                 "research paper"],
    "short":    ["short", "nier", "era ", "emerging result", "vision", "fast abstract",
                 "ivr", "industry", "new idea", "reflection", "poster", "visions and"],
    "tools":    ["tool", "demo", "demonstration", "artifact", "showcase", "dataset",
                 "tool paper"],
    "doctoral": ["doctoral", "phd", "graduate", "doctoral symposium", "phd symposium"],
}

SUBMISSION_KW = ["submission", "abstract", "paper due", "deadline", "due date",
                 "paper submission", "abstract submission", "submission deadline"]
NOTIF_KW      = ["notification", "decision", "accept", "author notification",
                 "notification of", "acceptance notification"]
CAMERA_KW     = ["camera", "final version", "camera-ready", "camera ready",
                 "final manuscript"]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _parse_date(text: str) -> Optional[str]:
    m = DATE_RE.search(text)
    if not m:
        return None
    day   = int(m.group(1))
    month = MONTH[m.group(2).lower()[:3]]
    year  = int(m.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


def _classify_track(name: str) -> str:
    nl = name.lower()
    for ttype, keywords in TRACK_TYPES.items():
        if any(k in nl for k in keywords):
            return ttype
    return "other"


def _classify_label(label: str) -> Optional[str]:
    ll = label.lower()
    if any(k in ll for k in SUBMISSION_KW):
        return "submission"
    if any(k in ll for k in NOTIF_KW):
        return "notification"
    if any(k in ll for k in CAMERA_KW):
        return "camera_ready"
    return None


async def _fetch_dates_page(client: httpx.AsyncClient, dates_url: str) -> list[dict]:
    """
    Scrape a conf.researchr.org/dates/{slug} page.
    The page uses a 3-column table: When | Track | What
    """
    try:
        resp = await client.get(dates_url)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # ── Primary: structured table (conf.researchr.org format) ────────────────
    rows = soup.select("table.table tr.clickable-row")

    if rows:
        tracks: dict[str, dict] = {}
        for row in rows:
            tds = row.find_all("td")
            if len(tds) < 3:
                continue
            date_str   = _parse_date(tds[0].get_text(" ", strip=True))
            track_name = tds[1].get_text(" ", strip=True)
            label      = tds[2].get_text(" ", strip=True)
            if not date_str or not track_name:
                continue
            slot = _classify_label(label)
            if not slot:
                continue
            if track_name not in tracks:
                tracks[track_name] = {}
            # Keep earliest submission date if multiple abstract/paper rows
            if slot == "submission" and slot in tracks[track_name]:
                existing = tracks[track_name][slot]
                tracks[track_name][slot] = min(existing, date_str)
            else:
                tracks[track_name].setdefault(slot, date_str)

        if tracks:
            return [
                dict(
                    track_type=_classify_track(name),
                    track_name=name,
                    submission=data.get("submission"),
                    notification=data.get("notification"),
                    camera_ready=data.get("camera_ready"),
                )
                for name, data in tracks.items()
                if any(data.values())
            ]

    # ── Fallback: heading → text walk ─────────────────────────────────────────
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    tracks_fb: dict[str, dict] = {}
    current = "General"
    tracks_fb[current] = {}

    for elem in soup.find_all(["h1", "h2", "h3", "h4", "li", "p", "td", "div", "span"]):
        text = elem.get_text(" ", strip=True)
        if not text:
            continue
        if elem.name in ("h1", "h2", "h3", "h4") and len(text) < 80:
            current = text
            tracks_fb.setdefault(current, {})
            continue
        d = _parse_date(text)
        if not d:
            continue
        slot = _classify_label(text)
        if slot:
            tracks_fb[current].setdefault(slot, d)

    return [
        dict(track_type=_classify_track(n), track_name=n,
             submission_time="23:59",  # AoE (UTC-12) convention
             **data)
        for n, data in tracks_fb.items()
        if any(data.values())
    ]


CITY_COUNTRY_RE = re.compile(
    r'\b([A-Z][a-zA-Z\s]{2,25}),\s*([A-Z][a-zA-Z\s]{2,20})\b'
)

_MON = (r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?')
_WD  = r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,.\s]+'

# Same-month range: "Sun 5 - Mon 6 July 2026"
DATE_RANGE_RE = re.compile(
    rf'{_WD}(\d{{1,2}})\s*[-–]\s*(?:{_WD})?(\d{{1,2}})\s+({_MON})\s+(\d{{4}})',
    re.IGNORECASE,
)
# Cross-month range: "Mon 29 June - Fri 3 July 2026"
DATE_RANGE_CROSS_RE = re.compile(
    rf'{_WD}(\d{{1,2}})\s+({_MON})\s*[-–]\s*(?:{_WD})?(\d{{1,2}})\s+({_MON})\s+(\d{{4}})',
    re.IGNORECASE,
)

# Country names to validate City, Country matches
COUNTRIES = {
    "australia", "austria", "belgium", "brazil", "canada", "china", "denmark",
    "finland", "france", "germany", "greece", "india", "ireland", "italy",
    "japan", "korea", "netherlands", "norway", "portugal", "singapore",
    "spain", "sweden", "switzerland", "uk", "usa", "united states", "united kingdom",
}


async def _fetch_conf_page(client: httpx.AsyncClient, home_url: str) -> dict:
    """Extract location and dates from a conference homepage."""
    meta: dict = {}
    try:
        resp = await client.get(home_url)
        resp.raise_for_status()
    except Exception:
        return meta

    soup = BeautifulSoup(resp.text, "lxml")

    # ── Location ──────────────────────────────────────────────────────────────
    text = soup.get_text(" ")

    # Strategy 1: keyword-anchored pattern (held in / venue / location: ...)
    loc_m = re.search(
        r'(?:held|take[s]? place|located|venue|location)[^\n]{0,60}?([A-Z][a-zA-Z\s]+,\s*[A-Z][a-zA-Z\s]+)',
        text
    )
    if loc_m:
        meta["location"] = loc_m.group(1).strip()
    else:
        # Strategy 2: look for <a> or <div> tags containing "City, Country" directly
        for el in soup.find_all(["a", "div", "span", "p"], string=CITY_COUNTRY_RE):
            if el.parent and el.parent.name in ("script", "style"):
                continue
            el_text = (el.get_text(" ", strip=True) if not isinstance(el, str) else str(el))
            m = CITY_COUNTRY_RE.search(el_text)
            if m and m.group(2).strip().lower() in COUNTRIES:
                meta["location"] = f"{m.group(1).strip()}, {m.group(2).strip()}"
                break

    # ── Conference dates ───────────────────────────────────────────────────────
    from datetime import date as _date

    def _dates_sane(ds: list[str]) -> bool:
        """True if dates span ≤ 14 days (i.e. they are a conference window, not deadlines)."""
        if len(ds) < 2:
            return True
        return (_date.fromisoformat(ds[-1]) - _date.fromisoformat(ds[0])).days <= 14

    def _iso(d: str, m: str, y: str) -> str:
        return f"{int(y):04d}-{MONTH[m.lower()[:3]]:02d}-{int(d):02d}"

    # Strategy 1: look for the "When:" row in the researchr sidebar/info table.
    # Skip script/style nodes — "When" appears in JS strings too, giving false matches.
    when_dates: list[str] = []
    for el in soup.find_all(string=re.compile(r'\bWhen\b', re.I)):
        if el.parent and el.parent.name in ("script", "style"):
            continue
        parent = el.parent
        # Walk up to find a container that also has sibling/child date text
        for _ in range(4):
            if parent is None:
                break
            chunk = parent.get_text(" ", strip=True)
            found = sorted({
                _iso(d, m, y) for d, m, y in DATE_RE.findall(chunk)
            })
            if found and _dates_sane(found):
                when_dates = found
                break
            parent = parent.parent

    if when_dates:
        meta["conf_start"] = when_dates[0]
        meta["conf_end"]   = when_dates[-1]
    else:
        # Strategy 2: look for date-range elements (same-month or cross-month)
        for el in soup.find_all(["div", "span", "p", "td", "li"]):
            chunk = el.get_text(" ", strip=True)
            rm = DATE_RANGE_RE.search(chunk)
            if rm:
                day1, day2, mon, year = rm.group(1), rm.group(2), rm.group(3), rm.group(4)
                meta["conf_start"] = _iso(day1, mon, year)
                meta["conf_end"]   = _iso(day2, mon, year)
                break
            rm2 = DATE_RANGE_CROSS_RE.search(chunk)
            if rm2:
                day1, mon1, day2, mon2, year = rm2.groups()
                meta["conf_start"] = _iso(day1, mon1, year)
                meta["conf_end"]   = _iso(day2, mon2, year)
                break

    if "conf_start" not in meta:
        # Strategy 3: all dates on page — sanity-check the span.
        dates = sorted({
            _iso(d, m, y) for d, m, y in DATE_RE.findall(text)
        })
        if len(dates) >= 2 and _dates_sane(dates):
            meta["conf_start"] = dates[0]
            meta["conf_end"]   = dates[-1]
        elif len(dates) == 1:
            meta["conf_start"] = meta["conf_end"] = dates[0]

    return meta


# ── Discovery ─────────────────────────────────────────────────────────────────

EDITION_RE = re.compile(r'/home/([a-z][a-z0-9]*(?:-[a-z0-9]+)*-(\d{4}))\b')


async def _discover_new_editions(
    client: httpx.AsyncClient,
    existing_keys: set[str],
    year_range: int,
) -> list[dict]:
    """
    Scan conf.researchr.org series pages and return new conference editions
    not already present in existing_keys.
    """
    today_year = datetime.utcnow().year
    max_year   = today_year + year_range
    new_confs: list[dict] = []

    for slug, display, full_name in DEFAULT_SERIES:
        if display in DEACTIVATED_DISPLAYS:
            continue
        series_url = f"https://conf.researchr.org/series/{slug}"
        _log(f"Scanning series: {series_url}")
        try:
            resp = await client.get(series_url)
            if resp.status_code != 200:
                _log(f"  Skipped ({resp.status_code})")
                continue
        except Exception as exc:
            _log(f"  Error: {exc}")
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        seen: set[tuple[str, int]] = set()

        for a in soup.find_all("a", href=True):
            m = EDITION_RE.search(a["href"])
            if not m:
                continue
            edition_slug = m.group(1)
            year = int(m.group(2))
            if year < today_year or year > max_year:
                continue
            seen.add((edition_slug, year))

        for edition_slug, year in seen:
            key = f"{display} {year}"
            if key in existing_keys:
                continue

            _log(f"  Discovered new edition: {key}")
            home_url  = f"https://conf.researchr.org/home/{edition_slug}"
            dates_url = f"https://conf.researchr.org/dates/{edition_slug}"
            meta = await _fetch_conf_page(client, home_url)

            new_confs.append(dict(
                key=key,
                year=year,
                full_name=full_name,
                location=meta.get("location"),
                conf_start=meta.get("conf_start"),
                conf_end=meta.get("conf_end"),
                url=home_url,
                dates_url=dates_url,
                last_updated=datetime.utcnow().isoformat(),
                tracks=[],
            ))
            existing_keys.add(key)

    return new_confs


# ── Main ─────────────────────────────────────────────────────────────────────

async def _main_async() -> None:
    _log(f"YEAR_RANGE={YEAR_RANGE}")

    # Build the active display names for deactivated series
    # (so we can skip them during discovery — they were already excluded from DEFAULT_SERIES
    # but DEACTIVATED_SERIES may refer to slugs not in DEFAULT_SERIES)
    _log("Initialising from seed data...")

    # Start from a clean copy of the seed; preserve track lists by deep-copying
    conferences: dict[str, dict] = {}
    for entry in _load_seed():
        conf = {k: v for k, v in entry.items() if k != "tracks"}
        conf["tracks"] = [dict(t) for t in entry.get("tracks", [])]
        if "last_updated" not in conf:
            conf["last_updated"] = None
        conferences[conf["key"]] = conf

    _log(f"Seed loaded: {len(conferences)} conferences")

    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=HEADERS) as client:

        # Phase 1: discover new editions from series pages
        _log("\n--- Phase 1: Discovering new conference editions ---")
        new_editions = await _discover_new_editions(
            client,
            set(conferences.keys()),
            YEAR_RANGE,
        )
        for conf in new_editions:
            conferences[conf["key"]] = conf
        _log(f"Discovery done — {len(new_editions)} new edition(s) added")

        # Phase 2: scrape dates for every conference that has a dates_url
        _log("\n--- Phase 2: Scraping dates for all conferences ---")
        updated = 0
        for key in sorted(conferences.keys()):
            conf = conferences[key]
            dates_url = conf.get("dates_url")
            if not dates_url:
                _log(f"  {key}: no dates_url, skipping")
                continue
            _log(f"  Fetching {key} — {dates_url}")

            # Re-fetch homepage if location or conf dates are still unknown
            needs_meta = not conf.get("conf_start") or conf.get("location") in (None, "TBD")
            if needs_meta and conf.get("url"):
                meta = await _fetch_conf_page(client, conf["url"])
                if meta.get("location"):
                    conf["location"] = meta["location"]
                if meta.get("conf_start"):
                    conf["conf_start"] = meta["conf_start"]
                    conf["conf_end"]   = meta.get("conf_end")

            scraped = await _fetch_dates_page(client, dates_url)
            if scraped:
                conf["tracks"] = scraped
                conf["last_updated"] = datetime.utcnow().isoformat()
                updated += 1
                _log(f"    -> {len(scraped)} track(s) found")
            else:
                _log(f"    -> no dates found, keeping seed data")

    _log(f"\nDone — {len(conferences)} total, {updated} updated from web")

    # Build final list sorted by conf_start (nulls last), then key
    def sort_key(c: dict):
        start = c.get("conf_start") or "9999-99-99"
        return (start, c["key"])

    result = sorted(conferences.values(), key=sort_key)

    # Ensure each entry has the required fields
    output = []
    for conf in result:
        output.append({
            "key":          conf.get("key"),
            "year":         conf.get("year"),
            "full_name":    conf.get("full_name"),
            "location":     conf.get("location"),
            "conf_start":   conf.get("conf_start"),
            "conf_end":     conf.get("conf_end"),
            "url":          conf.get("url"),
            "dates_url":    conf.get("dates_url"),
            "last_updated": conf.get("last_updated"),
            "tracks": [
                {
                    "track_type":   t.get("track_type"),
                    "track_name":   t.get("track_name"),
                    "submission":   t.get("submission"),
                    "notification": t.get("notification"),
                    "camera_ready": t.get("camera_ready"),
                }
                for t in conf.get("tracks", [])
            ],
        })

    out_path = "data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")
    _log(f"\nWrote {len(output)} conferences to {out_path}")

    # Write series-config.json derived from YAML so the browser can fetch it as JSON
    series_json_path = os.path.join(os.path.dirname(__file__), "series-config.json")
    series_yaml_path = os.path.join(os.path.dirname(__file__), "series-config.yaml")
    if os.path.exists(series_yaml_path):
        with open(series_yaml_path) as f:
            series = yaml.safe_load(f)
        with open(series_json_path, "w", encoding="utf-8") as f:
            json.dump(series, f, indent=2, ensure_ascii=False)
            f.write("\n")
        _log(f"Wrote {series_json_path}")


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
