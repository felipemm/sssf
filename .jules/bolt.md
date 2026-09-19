## 2024-05-19 - [Date parsing bottleneck in polling cycles]
**Learning:** In Vue components that poll data every 500ms (like `KanbanBoard.vue`), repeatedly calling `new Date(isoString).getTime()` for sorting large collections of items causes a measurable CPU overhead. JavaScript Date instantiation is surprisingly slow when executing in tight loops.
**Action:** Memoize `Date.parse()` or `new Date(isoString).getTime()` behind an LRU cache or capped Map in utility functions to prevent repetitive parsing of the same timestamp strings during polling.
