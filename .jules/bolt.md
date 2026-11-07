## 2025-02-28 - Vue Reactivity Overhead on Large Payloads
**Learning:** Vue's `ref()` applies deep reactivity to all nested properties by default. When storing large API response payloads (like `TicketsResponse` which contains complex ticket arrays) that are simply fetched and replaced entirely, this deep reactivity causes unnecessary rendering and memory overhead. Also, mutating `shallowRef` payloads requires re-assigning the whole `.value`, which is standard practice when they represent full API responses. In this case, `KanbanBoard.vue` just maps API data to UI.
**Action:** Used `shallowRef()` instead of `ref()` for `tickets` in `KanbanBoard.vue` to avoid unnecessary deep reactivity setup overhead for API payload arrays.
## 2024-05-24 - Vue Deep Reactivity Overhead with Large API Payloads
**Learning:** In the Vue visualizer app, using `ref()` for large API response payloads of complex objects (like `StatusResponse`, `CockpitData`, event traces) causes severe Vue deep reactivity performance overhead.
**Action:** Use `shallowRef()` instead of `ref()` for these large payloads since they are completely replaced (e.g., `data.value = [...]`) and the component logic does not rely on mutating nested object properties. Always add an inline code comment explaining that the objects are entirely replaced to justify the safety of the optimization.

## 2025-02-28 - Custom Vue router and bundle size
**Learning:** In the visualizer app, because a custom `v-if` based router is used in `App.vue` instead of Vue Router, all views are bundled into the initial payload by default. Vue Router usually handles this automatically for lazy loaded routes.
**Action:** Manually use Vue's `defineAsyncComponent` in `App.vue` to code-split route-level components (`SessionsList`, `SessionTrace`, `KanbanBoard`, `IssueTracker`, `StatusPage`, `MissionControl`) and reduce SPA initial load times and bundle size.
