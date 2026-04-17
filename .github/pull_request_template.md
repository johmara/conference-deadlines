## Add / Update Conference Series

**What does this PR do?**
- [ ] Add a new conference series
- [ ] Update an existing series entry
- [ ] Fix incorrect deadline data
- [ ] Other (describe below)

---

### Adding a series

Edit `series-config.json` and add one entry:

```json
{
  "slug": "your-slug",
  "display": "CONFNAME",
  "full_name": "Full Conference Name"
}
```

> The `slug` must match the series identifier on [conf.researchr.org/series/**slug**](https://conf.researchr.org/series/).  
> Example: for `https://conf.researchr.org/series/icse`, the slug is `icse`.
>
> That's it — the scraper will automatically discover and fetch deadlines for all editions.

---

### Fixing deadline data

If the scraper picks up wrong dates (e.g. for conferences co-located with ICSE), add a corrected entry to `conferences-seed.json`. This file acts as a maintainer override and is not required for normal series additions.

---

### Checklist

- [ ] I verified the slug exists on conf.researchr.org
- [ ] The `display` name matches the official conference abbreviation
- [ ] No duplicate entries introduced
