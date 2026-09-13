## 2023-10-27 - Date parsing performance overhead
**Learning:** Constructing a new `Date` object just to extract its timestamp (`new Date(iso).getTime()`) introduces significant overhead (~30%) compared to using `Date.parse(iso)`. This is a critical codebase-specific performance learning because the frontend heavily relies on timestamp conversions for sorting and rendering charts/timelines.
**Action:** Always prefer `Date.parse(iso)` for timestamp conversion instead of `new Date(iso).getTime()`.
