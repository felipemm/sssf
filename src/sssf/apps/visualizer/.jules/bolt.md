## 2026-09-18 - [Date Parsing Bottleneck]
**Learning:** `new Date(iso).getTime()` is significantly slower than `Date.parse(iso)` for parsing ISO strings, especially in loops and computed properties.
**Action:** Use `Date.parse(iso)` for raw performance when only the timestamp is needed.
