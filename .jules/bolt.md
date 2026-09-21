## 2023-10-27 - N+1 Query Anti-Pattern in Visualizer Backend
**Learning:** The visualizer's Bun backend (specifically in `server/cockpit.ts` and `server/tickets.ts`) was iterating over ticket rows and executing a `SELECT` query per row to look up session statuses. Because SQLite is fast and local, this didn't cause an immediate application crash, but the performance cost still scales linearly with the number of tickets.
**Action:** Replaced loop-based individual queries with single `LEFT JOIN` queries, handling cases where the joined table may not yet exist (using fallback `try/catch` queries).

## 2024-05-19 - Vue Component Frequent Polling Optimization
**Learning:** Polling Vue components using `setInterval` (like `SessionsList.vue`, `KanbanBoard.vue`) that re-fetch data every 500ms create unnecessary background API requests and possible UI churn when the user tabs away. Since these apps are dashboards meant to run all the time, background polling drains network and CPU resources when the dashboard isn't actively viewed.
**Action:** The codebase uses `setInterval` for fetching data very aggressively (`500ms`). It should dynamically pause polling when the browser tab is not visible, making use of the Page Visibility API (`document.hidden`). By skipping API calls or pausing intervals when `document.hidden` is true, we prevent massive redundant polling.

## 2024-09-07 - Pause UI polling when tab is inactive
**Learning:** The visualizer app relies heavily on active polling (`setInterval` every 500ms - 5000ms) to keep data fresh across multiple components (`MissionControl`, `KanbanBoard`, `SessionsList`, etc). This polling continued relentlessly even when the tab was hidden, draining client resources and keeping unnecessary load on the backend.
**Action:** Use `if (document.hidden) return;` inside high-frequency polling functions to pause API requests when the tab is out of focus. This is a crucial pattern for any real-time observability app built on polling.
