## 2024-05-24 - Vue Deep Reactivity Overhead on Large API Payloads
**Learning:** In the Vue visualizer app, using `ref()` for large API response payloads (like event traces) introduces significant deep reactivity performance overhead. Vue recursively proxies every object in arrays that contain thousands of records, which blocks the main thread during updates.
**Action:** Use `shallowRef()` instead of `ref()` for arrays (like `events`, `phases`, `envelopes`, `logLines`) that are completely replaced or concatenated rather than deeply mutated.
