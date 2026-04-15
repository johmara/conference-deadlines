"""SE Conference Deadline Tracker — FastAPI + SQLite backend."""

from __future__ import annotations

import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Paths ─────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "conferences.db"
STATIC  = Path(__file__).parent / "static"
HEADERS = {"User-Agent": "SE-Conference-Tracker/1.0 (+research-tool)"}

# ── Default series list (seeded into DB on first run) ─────────────────────────

DEFAULT_SERIES: list[tuple[str, str, str]] = [
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

# Series slugs to deactivate (and purge discovered conferences for)
DEACTIVATED_SERIES: list[str] = ["pldi", "cgo", "icsm"]

# ── Database ──────────────────────────────────────────────────────────────────

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS conferences (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            key          TEXT    NOT NULL,
            year         INTEGER NOT NULL,
            full_name    TEXT,
            location     TEXT,
            conf_start   TEXT,
            conf_end     TEXT,
            url          TEXT,
            dates_url    TEXT,
            last_updated TEXT,
            UNIQUE(key, year)
        );

        CREATE TABLE IF NOT EXISTS tracks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            conf_id      INTEGER NOT NULL,
            track_type   TEXT    NOT NULL,
            track_name   TEXT,
            submission   TEXT,
            notification TEXT,
            camera_ready TEXT,
            FOREIGN KEY (conf_id) REFERENCES conferences(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS series (
            slug      TEXT PRIMARY KEY,
            display   TEXT NOT NULL,
            full_name TEXT,
            active    INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        _seed_series(conn)
        _seed_settings(conn)
        _seed_conferences(conn)


def _seed_series(conn: sqlite3.Connection) -> None:
    for slug, display, full_name in DEFAULT_SERIES:
        conn.execute(
            "INSERT OR IGNORE INTO series (slug, display, full_name) VALUES (?,?,?)",
            (slug, display, full_name)
        )
    # Deactivate removed series and purge all their conferences
    for slug in DEACTIVATED_SERIES:
        row = conn.execute("SELECT display FROM series WHERE slug=?", (slug,)).fetchone()
        if row:
            conn.execute("DELETE FROM conferences WHERE key LIKE ?", (f"{row['display']} %",))
        conn.execute("UPDATE series SET active=0 WHERE slug=?", (slug,))
    conn.commit()


def _seed_settings(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('year_range', '2')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('hide_past', '0')")
    conn.commit()


def _seed_conferences(conn: sqlite3.Connection) -> None:
    SEED = [
        # ── Past 2026 conferences (all deadlines closed) ──────────────────────
        dict(key="SANER 2026", year=2026,
             full_name="IEEE Intl. Conf. on Software Analysis, Evolution & Reengineering",
             location="Limassol, Cyprus", conf_start="2026-03-17", conf_end="2026-03-20",
             url="https://conf.researchr.org/home/saner-2026",
             dates_url="https://conf.researchr.org/dates/saner-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Track",    submission="2025-10-16", notification="2025-12-09", camera_ready="2026-01-09"),
                 dict(track_type="short",    track_name="Short Papers / ERA", submission="2025-11-17", notification="2025-12-19", camera_ready="2026-01-14"),
                 dict(track_type="tools",    track_name="Tool Demo Track",    submission="2025-11-17", notification="2025-12-19", camera_ready="2026-01-14"),
             ]),
        dict(key="MSR 2026", year=2026,
             full_name="IEEE/ACM Intl. Conf. on Mining Software Repositories",
             location="Rio de Janeiro, Brazil (co-located ICSE)", conf_start="2026-04-13", conf_end="2026-04-14",
             url="https://conf.researchr.org/home/msr-2026",
             dates_url="https://conf.researchr.org/dates/msr-2026",
             tracks=[
                 dict(track_type="research", track_name="Technical Papers",        submission="2025-10-23", notification="2026-01-07", camera_ready="2026-01-26"),
                 dict(track_type="tools",    track_name="Data & Tool Showcase",    submission="2025-11-10", notification="2026-01-05", camera_ready="2026-01-23"),
             ]),
        dict(key="ICPC 2026", year=2026,
             full_name="IEEE/ACM Intl. Conf. on Program Comprehension",
             location="Rio de Janeiro, Brazil (co-located ICSE)", conf_start="2026-04-12", conf_end="2026-04-13",
             url="https://conf.researchr.org/home/icpc-2026",
             dates_url="https://conf.researchr.org/dates/icpc-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Track",          submission="2025-10-23", notification="2026-01-05", camera_ready="2026-01-26"),
                 dict(track_type="short",    track_name="Early Research Achievements", submission="2025-11-17", notification="2026-01-12", camera_ready="2026-01-26"),
                 dict(track_type="tools",    track_name="Tool Demonstration",       submission="2025-12-08", notification="2026-01-12", camera_ready="2026-01-23"),
             ]),
        dict(key="ICSE 2026", year=2026,
             full_name="IEEE/ACM Intl. Conf. on Software Engineering",
             location="Rio de Janeiro, Brazil", conf_start="2026-04-12", conf_end="2026-04-18",
             url="https://conf.researchr.org/home/icse-2026",
             dates_url="https://conf.researchr.org/dates/icse-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Track (C1)",     submission="2025-03-14", notification="2025-06-20", camera_ready=None),
                 dict(track_type="research", track_name="Research Track (C2)",     submission="2025-07-18", notification="2025-10-17", camera_ready=None),
                 dict(track_type="short",    track_name="NIER / SEIP / SEET",      submission="2025-09-29", notification="2025-12-01", camera_ready="2026-01-26"),
                 dict(track_type="tools",    track_name="Tool Demonstrations",      submission="2025-09-29", notification="2025-12-01", camera_ready="2026-01-26"),
                 dict(track_type="doctoral", track_name="Doctoral Symposium",       submission="2025-11-14", notification="2025-12-15", camera_ready="2026-01-26"),
             ]),
        dict(key="ICST 2026", year=2026,
             full_name="Intl. Conf. on Software Testing, Verification & Validation",
             location="Daejeon, South Korea", conf_start="2026-05-18", conf_end="2026-05-22",
             url="https://conf.researchr.org/home/icst-2026",
             dates_url="https://conf.researchr.org/dates/icst-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers",          submission="2025-12-22", notification="2026-02-20", camera_ready="2026-03-06"),
                 dict(track_type="short",    track_name="Vision & Emerging Results", submission="2026-02-20", notification="2026-03-15", camera_ready="2026-04-03"),
                 dict(track_type="tools",    track_name="Testing Tools Showcase",    submission="2026-03-12", notification="2026-04-06", camera_ready="2026-04-11"),
                 dict(track_type="doctoral", track_name="Doctoral Symposium",        submission="2026-03-20", notification="2026-04-03", camera_ready="2026-04-11"),
             ]),
        dict(key="EASE 2026", year=2026,
             full_name="Evaluation & Assessment in Software Engineering",
             location="Glasgow, UK", conf_start="2026-06-09", conf_end="2026-06-12",
             url="https://conf.researchr.org/home/ease-2026",
             dates_url="https://conf.researchr.org/dates/ease-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers",          submission="2026-01-23", notification="2026-03-13", camera_ready="2026-04-30"),
                 dict(track_type="short",    track_name="Short & Emerging Results", submission="2026-03-02", notification="2026-04-06", camera_ready="2026-04-30"),
                 dict(track_type="doctoral", track_name="Doctoral Symposium",       submission="2026-03-02", notification="2026-04-06", camera_ready="2026-04-30"),
             ]),
        dict(key="FSE 2026", year=2026,
             full_name="ACM Foundations of Software Engineering (ESEC/FSE)",
             location="Montreal, Canada", conf_start="2026-07-05", conf_end="2026-07-09",
             url="https://conf.researchr.org/home/fse-2026",
             dates_url="https://conf.researchr.org/dates/fse-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers",              submission="2025-09-11", notification="2025-12-22", camera_ready=None),
                 dict(track_type="short",    track_name="Ideas, Visions & Reflections", submission="2026-01-22", notification="2026-03-17", camera_ready="2026-04-02"),
                 dict(track_type="tools",    track_name="Tool Demonstrations",          submission="2026-01-31", notification="2026-03-17", camera_ready="2026-04-02"),
                 dict(track_type="doctoral", track_name="Doctoral Symposium",           submission="2026-02-05", notification="2026-03-19", camera_ready="2026-04-02"),
             ]),
        dict(key="ICSME 2026", year=2026,
             full_name="Intl. Conf. on Software Maintenance and Evolution",
             location="Benevento, Italy", conf_start="2026-09-14", conf_end="2026-09-18",
             url="https://conf.researchr.org/home/icsme-2026",
             dates_url="https://conf.researchr.org/dates/icsme-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers",            submission="2026-03-06", notification="2026-05-29", camera_ready=None),
                 dict(track_type="short",    track_name="Visions & Emerging Results", submission="2026-05-15", notification="2026-06-28", camera_ready=None),
                 dict(track_type="tools",    track_name="Tool Demo & Data Showcase",  submission="2026-05-28", notification="2026-06-30", camera_ready=None),
                 dict(track_type="doctoral", track_name="Doctoral Symposium",         submission="2026-06-03", notification="2026-06-24", camera_ready=None),
             ]),
        dict(key="SCAM 2026", year=2026,
             full_name="Source Code Analysis and Manipulation (co-located ICSME)",
             location="Benevento, Italy", conf_start="2026-09-14", conf_end="2026-09-18",
             url="https://conf.researchr.org/home/scam-2026",
             dates_url="https://conf.researchr.org/dates/scam-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Track", submission="2026-06-11", notification="2026-07-30", camera_ready="2026-08-14"),
             ]),
        dict(key="VARIABILITY 2026", year=2026,
             full_name="SPLC / VaMoS / ICSR — Intl. Conf. on Software & Systems Variability",
             location="Limassol, Cyprus", conf_start="2026-09-29", conf_end="2026-10-02",
             url="https://conf.researchr.org/home/variability-2026",
             dates_url="https://conf.researchr.org/dates/variability-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers (Round 2)", submission="2026-04-10", notification="2026-06-01", camera_ready="2026-07-15"),
                 dict(track_type="tools",    track_name="Demonstrations & Tools",    submission="2026-06-01", notification="2026-06-21", camera_ready="2026-07-15"),
                 dict(track_type="doctoral", track_name="Doctoral Symposium",        submission="2026-06-06", notification="2026-07-06", camera_ready="2026-07-15"),
                 dict(track_type="short",    track_name="Industry Track",            submission="2026-06-08", notification="2026-07-08", camera_ready="2026-07-15"),
             ]),
        dict(key="ISSTA 2026", year=2026,
             full_name="Intl. Symposium on Software Testing and Analysis (co-located SPLASH)",
             location="Oakland, CA, USA", conf_start="2026-10-03", conf_end="2026-10-09",
             url="https://conf.researchr.org/home/issta-2026",
             dates_url="https://conf.researchr.org/dates/issta-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers",      submission="2026-01-29", notification="2026-06-25", camera_ready="2026-07-23"),
                 dict(track_type="short",    track_name="SPLASH Onward! / SAS", submission="2026-05-01", notification="2026-06-22", camera_ready="2026-08-25"),
                 dict(track_type="tools",    track_name="Tool Demonstrations",  submission="2026-06-26", notification="2026-07-24", camera_ready="2026-08-07"),
                 dict(track_type="doctoral", track_name="Doctoral Symposium",   submission="2026-07-03", notification="2026-08-04", camera_ready="2026-08-30"),
             ]),
        dict(key="ESEM 2026", year=2026,
             full_name="Empirical Software Engineering & Measurement (ESEIW)",
             location="Munich, Germany", conf_start="2026-10-04", conf_end="2026-10-09",
             url="https://conf.researchr.org/home/esem-2026",
             dates_url="https://conf.researchr.org/dates/esem-2026",
             tracks=[
                 dict(track_type="research", track_name="Technical Track",           submission="2026-05-18", notification="2026-06-30", camera_ready="2026-08-17"),
                 dict(track_type="short",    track_name="Emerging Results & Vision", submission="2026-05-29", notification="2026-07-10", camera_ready="2026-08-05"),
                 dict(track_type="doctoral", track_name="IDoESE Doctoral Symposium", submission="2026-07-03", notification="2026-08-03", camera_ready=None),
             ]),
        dict(key="MODELS 2026", year=2026,
             full_name="ACM/IEEE Model Driven Engineering Languages & Systems",
             location="Málaga, Spain", conf_start="2026-10-04", conf_end="2026-10-09",
             url="https://conf.researchr.org/home/models-2026",
             dates_url="https://conf.researchr.org/dates/models-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers",        submission="2026-03-27", notification="2026-06-17", camera_ready="2026-07-31"),
                 dict(track_type="short",    track_name="NIER Track",             submission="2026-07-01", notification="2026-07-29", camera_ready="2026-08-05"),
                 dict(track_type="tools",    track_name="Tools & Demonstrations", submission="2026-07-15", notification="2026-07-31", camera_ready="2026-08-14"),
             ]),
        dict(key="ASE 2026", year=2026,
             full_name="IEEE/ACM Intl. Conf. on Automated Software Engineering",
             location="Munich, Germany", conf_start="2026-10-12", conf_end="2026-10-16",
             url="https://conf.researchr.org/home/ase-2026",
             dates_url="https://conf.researchr.org/dates/ase-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Papers",   submission="2026-03-26", notification="2026-06-18", camera_ready="2026-08-03"),
                 dict(track_type="tools",    track_name="Tools & Datasets",  submission="2026-05-11", notification="2026-06-17", camera_ready=None),
                 dict(track_type="short",    track_name="Industry Showcase", submission="2026-04-23", notification="2026-06-21", camera_ready="2026-08-03"),
             ]),
        dict(key="ISSRE 2026", year=2026,
             full_name="IEEE Intl. Symposium on Software Reliability Engineering",
             location="Limassol, Cyprus", conf_start="2026-10-20", conf_end="2026-10-23",
             url="https://conf.researchr.org/home/issre-2026",
             dates_url="https://conf.researchr.org/dates/issre-2026",
             tracks=[
                 dict(track_type="research", track_name="Research Track",     submission="2026-04-17", notification="2026-07-08", camera_ready="2026-08-19"),
                 dict(track_type="short",    track_name="Fast Abstracts",     submission="2026-06-15", notification="2026-08-05", camera_ready="2026-08-19"),
                 dict(track_type="tools",    track_name="Tool Demos / Talks", submission="2026-08-15", notification=None,         camera_ready=None),
             ]),
        dict(key="SANER 2027", year=2027,
             full_name="IEEE Intl. Conf. on Software Analysis, Evolution & Reengineering",
             location="Richmond, VA, USA", conf_start="2027-03-09", conf_end="2027-03-12",
             url="https://conf.researchr.org/home/saner-2027",
             dates_url="https://conf.researchr.org/dates/saner-2027",
             tracks=[
                 dict(track_type="research", track_name="Research Track (TBA)",  submission=None, notification=None, camera_ready=None),
                 dict(track_type="short",    track_name="Short Papers (TBA)",    submission=None, notification=None, camera_ready=None),
                 dict(track_type="tools",    track_name="Tool Demo (TBA)",       submission=None, notification=None, camera_ready=None),
                 dict(track_type="doctoral", track_name="Doctoral Symp. (TBA)", submission=None, notification=None, camera_ready=None),
             ]),
        dict(key="ICSE 2027", year=2027,
             full_name="IEEE/ACM Intl. Conf. on Software Engineering",
             location="Dublin, Ireland", conf_start="2027-04-25", conf_end="2027-05-01",
             url="https://conf.researchr.org/home/icse-2027",
             dates_url="https://conf.researchr.org/dates/icse-2027",
             tracks=[
                 dict(track_type="research", track_name="Research Track",            submission="2026-06-30", notification="2026-10-20", camera_ready="2027-01-24"),
                 dict(track_type="short",    track_name="NIER / SEIP / SEET / SEIS", submission="2026-10-23", notification="2026-12-18", camera_ready="2027-01-20"),
                 dict(track_type="tools",    track_name="Tool Demo & Data Showcase",  submission="2026-10-23", notification="2026-12-11", camera_ready="2027-01-20"),
                 dict(track_type="doctoral", track_name="Doctoral Symposium (TBA)",  submission=None,         notification=None,         camera_ready=None),
             ]),
    ]

    for conf in SEED:
        tracks = conf.pop("tracks")
        expected_url = conf.get("url", "")

        existing = conn.execute(
            "SELECT id, url, conf_start FROM conferences WHERE key=? AND year=?",
            (conf["key"], conf["year"])
        ).fetchone()

        # Does the seed have any real (non-placeholder) track data?
        seed_has_real_tracks = any(t.get("submission") for t in tracks)

        if existing:
            url_ok    = (not expected_url) or (existing["url"] == expected_url)
            has_dates = bool(existing["conf_start"])
            track_cnt = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE conf_id=?", (existing["id"],)
            ).fetchone()[0]

            if url_ok and has_dates and 0 < track_cnt <= 30:
                if seed_has_real_tracks:
                    # Conference row is fine; just replace tracks with seed data
                    conn.execute("DELETE FROM tracks WHERE conf_id=?", (existing["id"],))
                    for t in tracks:
                        conn.execute("""
                            INSERT INTO tracks (conf_id,track_type,track_name,submission,notification,camera_ready)
                            VALUES (?,?,?,?,?,?)
                        """, (existing["id"], t["track_type"], t["track_name"],
                              t.get("submission"), t.get("notification"), t.get("camera_ready")))
                # Either way, skip re-inserting the conference row
                continue
            # Bad URL / missing dates / too many tracks → wipe and re-seed fully
            conn.execute("DELETE FROM conferences WHERE id=?", (existing["id"],))

        conn.execute("""
            INSERT INTO conferences
              (key,year,full_name,location,conf_start,conf_end,url,dates_url,last_updated)
            VALUES (:key,:year,:full_name,:location,:conf_start,:conf_end,:url,:dates_url,:ts)
        """, {**conf, "ts": datetime.utcnow().isoformat()})
        conf_id = conn.execute(
            "SELECT id FROM conferences WHERE key=? AND year=?", (conf["key"], conf["year"])
        ).fetchone()["id"]
        for t in tracks:
            conn.execute("""
                INSERT INTO tracks (conf_id,track_type,track_name,submission,notification,camera_ready)
                VALUES (?,?,?,?,?,?)
            """, (conf_id, t["track_type"], t["track_name"],
                  t.get("submission"), t.get("notification"), t.get("camera_ready")))
    conn.commit()

# ── Scraper ───────────────────────────────────────────────────────────────────

DATE_RE = re.compile(
    r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,.\s]+(\d{1,2})\s+'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})',
    re.IGNORECASE,
)
MONTH = dict(Jan=1,Feb=2,Mar=3,Apr=4,May=5,Jun=6,
             Jul=7,Aug=8,Sep=9,Oct=10,Nov=11,Dec=12)

# Track type classification keywords
TRACK_TYPES = {
    "research": ["research", "technical", "full paper", "regular paper", "main track",
                 "research paper"],
    "short":    ["short", "nier", "era ", "emerging result", "vision", "fast abstract",
                 "ivr", "industry", "new idea", "reflection", "poster", "visions and"],
    "tools":    ["tool", "demo", "demonstration", "artifact", "showcase", "dataset",
                 "tool paper"],
    "doctoral": ["doctoral", "phd", "graduate", "doctoral symposium", "phd symposium"],
}

# Label keyword classification
SUBMISSION_KW = ["submission", "abstract", "paper due", "deadline", "due date",
                 "paper submission", "abstract submission", "submission deadline"]
NOTIF_KW      = ["notification", "decision", "accept", "author notification",
                 "notification of", "acceptance notification"]
CAMERA_KW     = ["camera", "final version", "camera-ready", "camera ready",
                 "final manuscript"]


def _parse_date(text: str) -> Optional[str]:
    m = DATE_RE.search(text)
    if not m:
        return None
    day   = int(m.group(1))
    month = MONTH[m.group(2).capitalize()]
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


async def _scrape_dates(client: httpx.AsyncClient, dates_url: str) -> list[dict]:
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
    # <table class="table table-hover"><tr class="clickable-row ...">
    #   <td>Date</td><td>Track Name</td><td>Label</td>
    rows = soup.select("table.table tr.clickable-row")

    if rows:
        # Group by track name: {track_name: {submission, notification, camera_ready}}
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

    # ── Fallback: heading → text walk ────────────────────────────────────────
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    tracks_fb: dict[str, dict] = {}
    current = "General"
    tracks_fb[current] = {}

    for elem in soup.find_all(["h1","h2","h3","h4","li","p","td","div","span"]):
        text = elem.get_text(" ", strip=True)
        if not text:
            continue
        if elem.name in ("h1","h2","h3","h4") and len(text) < 80:
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
        dict(track_type=_classify_track(n), track_name=n, **data)
        for n, data in tracks_fb.items()
        if any(data.values())
    ]


async def _scrape_conf_homepage(client: httpx.AsyncClient, home_url: str) -> dict:
    """Extract location and dates from a conference homepage."""
    meta: dict = {}
    try:
        resp = await client.get(home_url)
        resp.raise_for_status()
    except Exception:
        return meta

    soup = BeautifulSoup(resp.text, "lxml")

    # Location: look for city/country patterns near keywords
    text = soup.get_text(" ")
    loc_m = re.search(
        r'(?:held|take[s]? place|located|venue|location)[^\n]{0,60}?([A-Z][a-zA-Z\s]+,\s*[A-Z][a-zA-Z\s]+)',
        text
    )
    if loc_m:
        meta["location"] = loc_m.group(1).strip()

    # Conference date window from all dates found
    dates = sorted({
        f"{int(y):04d}-{MONTH[m.capitalize()]:02d}-{int(d):02d}"
        for d, m, y in DATE_RE.findall(text)
    })
    if len(dates) >= 2:
        meta["conf_start"] = dates[0]
        meta["conf_end"]   = dates[-1]
    elif len(dates) == 1:
        meta["conf_start"] = meta["conf_end"] = dates[0]

    return meta


# ── Edition discovery ─────────────────────────────────────────────────────────

EDITION_RE = re.compile(r'/home/([a-z][a-z0-9]*(?:-[a-z0-9]+)*-(\d{4}))\b')


async def _discover(client: httpx.AsyncClient, year_range: int) -> int:
    """Scan series pages and insert any new conference editions into the DB."""
    today_year = datetime.utcnow().year
    max_year   = today_year + year_range
    discovered = 0

    with db() as conn:
        series_list = conn.execute(
            "SELECT slug, display, full_name FROM series WHERE active=1"
        ).fetchall()

    for s in series_list:
        slug, display, full_name = s["slug"], s["display"], s["full_name"]
        series_url = f"https://conf.researchr.org/series/{slug}"
        try:
            resp = await client.get(series_url)
            if resp.status_code != 200:
                continue
        except Exception:
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
            with db() as conn:
                if conn.execute(
                    "SELECT id FROM conferences WHERE key=? AND year=?", (key, year)
                ).fetchone():
                    continue

            _log(f"  ✦ Discovered {key}")
            home_url  = f"https://conf.researchr.org/home/{edition_slug}"
            dates_url = f"https://conf.researchr.org/dates/{edition_slug}"
            meta = await _scrape_conf_homepage(client, home_url)

            with db() as conn:
                try:
                    conn.execute("""
                        INSERT INTO conferences
                          (key,year,full_name,location,conf_start,conf_end,url,dates_url,last_updated)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (key, year, full_name, meta.get("location"),
                          meta.get("conf_start"), meta.get("conf_end"),
                          home_url, dates_url, datetime.utcnow().isoformat()))
                    discovered += 1
                except sqlite3.IntegrityError:
                    pass

    return discovered


# ── Refresh state ─────────────────────────────────────────────────────────────

_state: dict = {"running": False, "log": [], "discovered": 0, "updated": 0}


def _log(msg: str) -> None:
    _state["log"].append(msg)


async def _refresh_all(year: Optional[int]) -> None:
    global _state
    _state = {"running": True, "log": [], "discovered": 0, "updated": 0}

    with db() as conn:
        year_range = int(conn.execute(
            "SELECT value FROM settings WHERE key='year_range'"
        ).fetchone()["value"])

    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=HEADERS) as client:

        # Phase 1: discover
        _log("Scanning series pages for new conferences…")
        disc = await _discover(client, year_range)
        _state["discovered"] = disc
        _log(f"Discovery done — {disc} new conference(s) added")

        # Phase 2: refresh dates
        with db() as conn:
            sql    = "SELECT id, key, dates_url FROM conferences"
            params: tuple = ()
            if year:
                sql    += " WHERE year=?"
                params  = (year,)
            rows = conn.execute(sql + " ORDER BY key", params).fetchall()

        for row in rows:
            if not row["dates_url"]:
                continue
            _log(f"Fetching {row['key']}…")
            scraped = await _scrape_dates(client, row["dates_url"])
            if not scraped:
                _log("  (no dates found)")
                continue
            with db() as conn:
                conn.execute("DELETE FROM tracks WHERE conf_id=?", (row["id"],))
                for t in scraped:
                    conn.execute("""
                        INSERT INTO tracks
                          (conf_id,track_type,track_name,submission,notification,camera_ready)
                        VALUES (?,?,?,?,?,?)
                    """, (row["id"], t["track_type"], t["track_name"],
                          t.get("submission"), t.get("notification"), t.get("camera_ready")))
                conn.execute(
                    "UPDATE conferences SET last_updated=? WHERE id=?",
                    (datetime.utcnow().isoformat(), row["id"])
                )
            _state["updated"] += 1

    _state["running"] = False
    _log(f"Done — {_state['updated']} updated, {_state['discovered']} discovered")


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield

app = FastAPI(title="SE Conference Tracker", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Conference endpoints ───────────────────────────────────────────────────────

@app.get("/api/years")
def get_years():
    with db() as conn:
        rows = conn.execute("SELECT DISTINCT year FROM conferences ORDER BY year").fetchall()
    return [r["year"] for r in rows]


@app.get("/api/conferences")
def get_conferences(year: Optional[int] = None):
    with db() as conn:
        # Only return conferences whose series is active (key prefix matches series display name)
        base = """
            SELECT c.* FROM conferences c
            WHERE EXISTS (
                SELECT 1 FROM series s
                WHERE s.active = 1
                  AND c.key LIKE s.display || ' %'
            )
        """
        if year:
            base += " AND c.year = ?"
        base += " ORDER BY c.conf_start"
        rows = conn.execute(base, (year,) if year else ()).fetchall()
        result = []
        for c in rows:
            tracks = conn.execute(
                "SELECT * FROM tracks WHERE conf_id=? ORDER BY track_type", (c["id"],)
            ).fetchall()
            result.append({**dict(c), "tracks": [dict(t) for t in tracks]})
    return result


@app.post("/api/conferences/{conf_id}/tracks/{track_type}")
def upsert_track(conf_id: int, track_type: str, body: dict):
    with db() as conn:
        if conn.execute(
            "SELECT id FROM tracks WHERE conf_id=? AND track_type=?", (conf_id, track_type)
        ).fetchone():
            conn.execute(
                "UPDATE tracks SET track_name=?,submission=?,notification=?,camera_ready=? "
                "WHERE conf_id=? AND track_type=?",
                (body.get("track_name"), body.get("submission"),
                 body.get("notification"), body.get("camera_ready"), conf_id, track_type)
            )
        else:
            conn.execute(
                "INSERT INTO tracks (conf_id,track_type,track_name,submission,notification,camera_ready) "
                "VALUES (?,?,?,?,?,?)",
                (conf_id, track_type, body.get("track_name"), body.get("submission"),
                 body.get("notification"), body.get("camera_ready"))
            )
    return {"status": "ok"}

# ── Series endpoints ───────────────────────────────────────────────────────────

@app.get("/api/series")
def get_series():
    with db() as conn:
        rows = conn.execute("SELECT * FROM series ORDER BY display").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/series")
async def add_series(body: dict):
    slug     = (body.get("slug") or "").strip().lower()
    display  = (body.get("display") or "").strip().upper()
    fullname = (body.get("full_name") or "").strip()
    if not slug or not display:
        raise HTTPException(400, "slug and display are required")

    # Validate that the series exists on conf.researchr.org
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=HEADERS) as client:
        try:
            r = await client.get(f"https://conf.researchr.org/series/{slug}")
            valid = r.status_code == 200 and "Important Dates" not in r.text
        except Exception:
            valid = False

    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO series (slug,display,full_name,active) VALUES (?,?,?,1)",
                (slug, display, fullname or None)
            )
        except sqlite3.IntegrityError:
            # Re-activate if it existed but was deactivated
            conn.execute("UPDATE series SET active=1, display=?, full_name=? WHERE slug=?",
                         (display, fullname or None, slug))
    return {"status": "ok", "valid_on_researchr": valid}


@app.delete("/api/series/{slug}")
def delete_series(slug: str):
    with db() as conn:
        row = conn.execute("SELECT display FROM series WHERE slug=?", (slug,)).fetchone()
        if row:
            conn.execute("DELETE FROM conferences WHERE key LIKE ?", (f"{row['display']} %",))
        conn.execute("UPDATE series SET active=0 WHERE slug=?", (slug,))
    return {"status": "ok"}

# ── Settings endpoints ────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings/{key}")
def update_setting(key: str, body: dict):
    ALLOWED = {"year_range"}
    if key not in ALLOWED:
        raise HTTPException(400, f"Unknown setting: {key}")
    value = str(body.get("value", "")).strip()
    if not value:
        raise HTTPException(400, "value required")
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    return {"status": "ok"}

# ── Refresh endpoints ─────────────────────────────────────────────────────────

@app.post("/api/refresh")
async def refresh(background_tasks: BackgroundTasks, year: Optional[int] = None):
    if _state["running"]:
        raise HTTPException(409, "Refresh already in progress")
    background_tasks.add_task(_refresh_all, year)
    return {"status": "started"}


@app.get("/api/refresh/status")
def refresh_status():
    return {
        "running":    _state["running"],
        "log":        _state["log"],
        "discovered": _state["discovered"],
        "updated":    _state["updated"],
    }

# ── Static ────────────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
