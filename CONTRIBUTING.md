# Contributing

Contributions welcome — bug reports, missing conferences, data corrections, and UI improvements.

## Adding or updating a conference

Edit **`series-config.yaml`** and open a pull request. That file is the single source of truth for which conferences are tracked.

```yaml
- slug: icse
  display: ICSE
  full_name: IEEE/ACM Intl. Conf. on Software Engineering
```

The `slug` must match the URL path used on [conf.researchr.org](https://conf.researchr.org) (e.g. `conf.researchr.org/home/icse-2025` → slug `icse`).

If the scraper cannot find dates for a new conference, add an entry to **`conferences-seed.yaml`** with hand-curated data as a fallback.

## Reporting wrong or missing data

Open an issue and include:
- Conference name and year
- The incorrect / missing value
- A link to the official CFP page

## Running the scraper locally

```bash
pip install httpx beautifulsoup4
python generate_data.py
```

Verify `data.json` looks correct, then commit it alongside any changes to `series-config.yaml` or `conferences-seed.yaml`.

## Pull request checklist

- [ ] `series-config.yaml` entries are in alphabetical order by `slug`
- [ ] `generate_data.py` runs without errors
- [ ] `data.json` is committed if you changed which conferences are tracked
- [ ] PR description explains what was added/fixed and links any relevant CFP page

## Code changes

For UI or scraper changes, please open an issue first to discuss the approach. Keep changes focused — one concern per PR.

## License

By contributing you agree that your contributions will be licensed under the [MIT License](LICENSE).
