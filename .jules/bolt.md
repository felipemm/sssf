## 2024-05-23 - Vue Reactivity Performance for Large API Payloads
**Learning:** In the Vue visualizer app, using `ref()` for large API response arrays (like `events`, `phases`, `logLines`) that are completely replaced causes severe Vue deep reactivity performance overhead, significantly impacting rendering and memory.
**Action:** Use `shallowRef()` instead of `ref()` for large datasets that are fetched and replaced as a whole, rather than deeply mutated.
