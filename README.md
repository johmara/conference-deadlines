<div align="center">

# 📅 SE Conference Deadlines

**Track submission deadlines for top software engineering conferences**

[![Live Demo](https://img.shields.io/badge/demo-live-brightgreen?style=for-the-badge)](https://johan.martinson.phd/conference-deadlines/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)
[![Update data](https://github.com/johmara/conference-deadlines/actions/workflows/update-data.yml/badge.svg)](https://github.com/johmara/conference-deadlines/actions/workflows/update-data.yml)

[Live Demo](https://johan.martinson.phd/conference-deadlines/) • [Report Bug](https://github.com/johmara/conference-deadlines/issues) • [Request Feature](https://github.com/johmara/conference-deadlines/issues)

</div>

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 🚀 Core Functionality
- 📋 **26 SE Conferences** — ICSE, FSE, ASE, MSR, and more
- 🗓️ **Multiple Track Deadlines** — Research, Short, Tools, Doctoral
- ⏰ **Deadline Urgency Chips** — Open / Due Soon / Hot / Past
- 🌍 **Local Timezone Display** — AoE source, shown in your timezone

### 🔍 Filtering & Sorting
- 🔎 **Quick Search** — Filter by conference name
- 📅 **Year Filter** — Focus on a specific conference year
- 🗂️ **Column Sort** — Sort by any deadline column
- 👁️ **Hide Past** — Toggle visibility of expired deadlines

</td>
<td width="50%">

### 📡 Data Pipeline
- 🤖 **Auto-Scraper** — Pulls dates from conf.researchr.org
- 🔄 **GitHub Actions** — Refreshes `data.json` on a schedule
- 🌱 **Seed Fallback** — `conferences-seed.yaml` covers gaps
- ⚙️ **Configurable** — Add/remove series via `series-config.yaml`

### 🎨 UI/UX
- 📱 **Responsive** — Works on all screen sizes
- 🔗 **Deep Links** — Stable URLs per conference / year

</td>
</tr>
</table>

---

## 🚦 Quick Start

### View deadlines

Just open the [live site](https://johan.martinson.phd/conference-deadlines/).

### Run the scraper locally

```bash
pip install -r requirements.txt
python generate_data.py        # writes data.json
# Then open index.html in a browser
```

Set `YEAR_RANGE` to control how many years ahead to scan (default: 2):

```bash
YEAR_RANGE=3 python generate_data.py
```

---

## ➕ Adding a Conference

Edit **`series-config.yaml`** — that's all contributors need to touch:

```yaml
- slug: icse
  display: ICSE
  full_name: IEEE/ACM Intl. Conf. on Software Engineering
```

| Field | Description |
|-------|-------------|
| `slug` | URL slug used on conf.researchr.org |
| `display` | Short label shown in the table header |
| `full_name` | Full conference name (tooltip / aria) |

Re-run `generate_data.py` to pull dates for the new entry.

---

## 🗂️ Project Structure

```
se-conferences/
├── index.html            # Single-page app — all UI and logic
├── data.json             # Scraped deadline data (auto-generated)
├── conferences-seed.yaml # Hand-curated fallback / override data
├── series-config.yaml    # Conference series to track
└── generate_data.py      # Scraper — reads series-config, writes data.json
```

---

## 🔄 Data Refresh

GitHub Actions runs the scraper on a schedule and commits updated `data.json` to `main`. The live site picks up changes automatically via GitHub Pages.

To trigger a manual refresh, re-run the scraper and commit `data.json`.

---

## 🖥️ Server-side alternative

The [`api` branch](https://github.com/johmara/conference-deadlines/tree/api) contains a FastAPI backend with a SQLite database. Use it if you want to self-host a proper server instead of relying on a static `data.json` file and GitHub Pages.

---

## 🌐 Browser Compatibility

Works in all modern browsers with CSS custom properties support:
- Chrome/Edge 88+
- Firefox 85+
- Safari 14+
