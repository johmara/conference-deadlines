## Add / Update Conference Series

**What does this PR do?**
- [ ] Add a new conference series
- [ ] Update an existing entry
- [ ] Fix incorrect deadline data
- [ ] Other (describe below)

---

### Series entry (add to `series-config.json`)

```json
{
  "slug": "your-slug",
  "display": "CONFNAME",
  "full_name": "Full Conference Name"
}
```

> The `slug` must match the series identifier on [conf.researchr.org/series/**slug**](https://conf.researchr.org/series/).  
> Example: for `https://conf.researchr.org/series/icse`, the slug is `icse`.

---

### Checklist

- [ ] I verified the slug exists on conf.researchr.org
- [ ] The `display` name matches the official conference abbreviation
- [ ] I have not introduced duplicate entries
