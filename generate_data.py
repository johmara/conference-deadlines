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
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

YEAR_RANGE = int(os.environ.get("YEAR_RANGE", "2"))
HEADERS    = {"User-Agent": "SE-Conference-Tracker/1.0 (+research-tool)"}

# ── Series ────────────────────────────────────────────────────────────────────

def _load_series() -> list[tuple[str, str, str]]:
    """Load series from series-config.json if present, else use hardcoded defaults."""
    config_path = os.path.join(os.path.dirname(__file__), "series-config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            data = json.load(f)
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

SEED: list[dict] = [
    # ── 2026 conferences ──────────────────────────────────────────────────────
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
             dict(track_type="research", track_name="Research Track",              submission="2025-10-23", notification="2026-01-05", camera_ready="2026-01-26"),
             dict(track_type="short",    track_name="Early Research Achievements", submission="2025-11-17", notification="2026-01-12", camera_ready="2026-01-26"),
             dict(track_type="tools",    track_name="Tool Demonstration",          submission="2025-12-08", notification="2026-01-12", camera_ready="2026-01-23"),
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
             dict(track_type="research", track_name="Research Papers",           submission="2025-12-22", notification="2026-02-20", camera_ready="2026-03-06"),
             dict(track_type="short",    track_name="Vision & Emerging Results",  submission="2026-02-20", notification="2026-03-15", camera_ready="2026-04-03"),
             dict(track_type="tools",    track_name="Testing Tools Showcase",     submission="2026-03-12", notification="2026-04-06", camera_ready="2026-04-11"),
             dict(track_type="doctoral", track_name="Doctoral Symposium",         submission="2026-03-20", notification="2026-04-03", camera_ready="2026-04-11"),
         ]),
    dict(key="EASE 2026", year=2026,
         full_name="Evaluation & Assessment in Software Engineering",
         location="Glasgow, UK", conf_start="2026-06-09", conf_end="2026-06-12",
         url="https://conf.researchr.org/home/ease-2026",
         dates_url="https://conf.researchr.org/dates/ease-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers",           submission="2026-01-23", notification="2026-03-13", camera_ready="2026-04-30"),
             dict(track_type="short",    track_name="Short & Emerging Results",  submission="2026-03-02", notification="2026-04-06", camera_ready="2026-04-30"),
             dict(track_type="doctoral", track_name="Doctoral Symposium",        submission="2026-03-02", notification="2026-04-06", camera_ready="2026-04-30"),
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
         location="Malaga, Spain", conf_start="2026-10-04", conf_end="2026-10-09",
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
    # ── 2027 conferences ──────────────────────────────────────────────────────
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
    # ── New series: architecture, OOP/PL, variability-adjacent ──────────────────
    dict(key="ICSA 2026", year=2026,
         full_name="IEEE Intl. Conf. on Software Architecture",
         location="Odense, Denmark", conf_start="2026-05-04", conf_end="2026-05-08",
         url="https://conf.researchr.org/home/icsa-2026",
         dates_url="https://conf.researchr.org/dates/icsa-2026",
         tracks=[
             dict(track_type="research", track_name="Research Track",      submission="2025-11-08", notification="2026-01-30", camera_ready="2026-03-06"),
             dict(track_type="short",    track_name="NEMI Track",          submission="2026-01-09", notification="2026-02-20", camera_ready="2026-03-06"),
             dict(track_type="tools",    track_name="Tools & Demos",       submission="2026-01-23", notification="2026-02-27", camera_ready="2026-03-06"),
             dict(track_type="doctoral", track_name="Doctoral Symposium",  submission="2026-02-06", notification="2026-02-27", camera_ready="2026-03-06"),
         ]),
    dict(key="SPLASH 2026", year=2026,
         full_name="ACM SIGPLAN Conf. on Systems, Programming, Languages and Applications (incl. OOPSLA)",
         location="Singapore", conf_start="2026-10-25", conf_end="2026-10-30",
         url="https://conf.researchr.org/home/splash-2026",
         dates_url="https://conf.researchr.org/dates/splash-2026",
         tracks=[
             dict(track_type="research", track_name="OOPSLA",             submission="2026-04-15", notification=None,         camera_ready=None),
             dict(track_type="short",    track_name="Onward! Papers",      submission="2026-05-01", notification=None,         camera_ready=None),
             dict(track_type="tools",    track_name="Onward! Essays",      submission="2026-05-01", notification=None,         camera_ready=None),
         ]),
    dict(key="ECOOP 2026", year=2026,
         full_name="European Conf. on Object-Oriented Programming",
         location="TBD", conf_start=None, conf_end=None,
         url="https://conf.researchr.org/home/ecoop-2026",
         dates_url="https://conf.researchr.org/dates/ecoop-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers (TBA)", submission=None, notification=None, camera_ready=None),
         ]),
    dict(key="GPCE 2026", year=2026,
         full_name="ACM SIGPLAN Intl. Conf. on Generative Programming: Concepts & Experiences",
         location="Singapore (co-located SPLASH)", conf_start="2026-10-25", conf_end="2026-10-30",
         url="https://conf.researchr.org/home/gpce-2026",
         dates_url="https://conf.researchr.org/dates/gpce-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers (TBA)", submission=None, notification=None, camera_ready=None),
         ]),
    dict(key="SLE 2026", year=2026,
         full_name="ACM SIGPLAN Intl. Conf. on Software Language Engineering",
         location="Singapore (co-located SPLASH)", conf_start="2026-10-25", conf_end="2026-10-30",
         url="https://conf.researchr.org/home/sle-2026",
         dates_url="https://conf.researchr.org/dates/sle-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers (TBA)", submission=None, notification=None, camera_ready=None),
         ]),
    dict(key="CAIN 2026", year=2026,
         full_name="IEEE/ACM Intl. Conf. on AI Engineering",
         location="Rio de Janeiro, Brazil (co-located ICSE)", conf_start="2026-04-12", conf_end="2026-04-18",
         url="https://conf.researchr.org/home/cain-2026",
         dates_url="https://conf.researchr.org/dates/cain-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers (TBA)", submission=None, notification=None, camera_ready=None),
         ]),
    dict(key="SSBSE 2026", year=2026,
         full_name="Intl. Symposium on Search-Based Software Engineering",
         location="TBD", conf_start=None, conf_end=None,
         url="https://conf.researchr.org/home/ssbse-2026",
         dates_url="https://conf.researchr.org/dates/ssbse-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers (TBA)", submission=None, notification=None, camera_ready=None),
         ]),
    dict(key="APSEC 2026", year=2026,
         full_name="Asia-Pacific Software Engineering Conference",
         location="TBD", conf_start=None, conf_end=None,
         url="https://conf.researchr.org/home/apsec-2026",
         dates_url="https://conf.researchr.org/dates/apsec-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers (TBA)", submission=None, notification=None, camera_ready=None),
         ]),
    dict(key="QRS 2026", year=2026,
         full_name="IEEE Intl. Conf. on Software Quality, Reliability and Security",
         location="TBD", conf_start=None, conf_end=None,
         url="https://conf.researchr.org/home/qrs-2026",
         dates_url="https://conf.researchr.org/dates/qrs-2026",
         tracks=[
             dict(track_type="research", track_name="Research Papers (TBA)", submission=None, notification=None, camera_ready=None),
         ]),
    dict(key="ICSE 2027", year=2027,
         full_name="IEEE/ACM Intl. Conf. on Software Engineering",
         location="Dublin, Ireland", conf_start="2027-04-25", conf_end="2027-05-01",
         url="https://conf.researchr.org/home/icse-2027",
         dates_url="https://conf.researchr.org/dates/icse-2027",
         tracks=[
             dict(track_type="research", track_name="Research Track",              submission="2026-06-30", notification="2026-10-20", camera_ready="2027-01-24"),
             dict(track_type="short",    track_name="NIER / SEIP / SEET / SEIS",  submission="2026-10-23", notification="2026-12-18", camera_ready="2027-01-20"),
             dict(track_type="tools",    track_name="Tool Demo & Data Showcase",  submission="2026-10-23", notification="2026-12-11", camera_ready="2027-01-20"),
             dict(track_type="doctoral", track_name="Doctoral Symposium (TBA)",   submission=None,         notification=None,         camera_ready=None),
         ]),
]

# ── Scraper helpers ───────────────────────────────────────────────────────────

DATE_RE = re.compile(
    r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,.\s]+(\d{1,2})\s+'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})',
    re.IGNORECASE,
)
MONTH = dict(Jan=1, Feb=2, Mar=3, Apr=4, May=5, Jun=6,
             Jul=7, Aug=8, Sep=9, Oct=10, Nov=11, Dec=12)

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


async def _fetch_conf_page(client: httpx.AsyncClient, home_url: str) -> dict:
    """Extract location and dates from a conference homepage."""
    meta: dict = {}
    try:
        resp = await client.get(home_url)
        resp.raise_for_status()
    except Exception:
        return meta

    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(" ")

    loc_m = re.search(
        r'(?:held|take[s]? place|located|venue|location)[^\n]{0,60}?([A-Z][a-zA-Z\s]+,\s*[A-Z][a-zA-Z\s]+)',
        text
    )
    if loc_m:
        meta["location"] = loc_m.group(1).strip()

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
    for entry in SEED:
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


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
