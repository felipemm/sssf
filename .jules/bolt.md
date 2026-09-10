## 2024-05-24 - [Initial Journal]
**Learning:** Initial journal creation.
**Action:** Created for tracking.
## 2024-05-24 - Memoize Date Parsing
**Learning:** Repeatedly parsing identical date strings into `Date` objects in JS is a significant bottleneck during list rendering/sorting in dashboards. Calling `new Date(iso)` inside a sort loop (e.g., in Vue computed properties) is O(n log n) but scaling by an expensive constant.
**Action:** Implemented a `Map` cache for the `ts()` function to store string-to-timestamp mappings. Limits the map size to prevent memory leaks while dramatically speeding up arrays containing repeated timestamps across re-renders.
