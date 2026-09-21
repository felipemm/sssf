## 2023-10-27 - N+1 Query Anti-Pattern in Visualizer Backend
**Learning:** The visualizer's Bun backend (specifically in `server/cockpit.ts` and `server/tickets.ts`) was iterating over ticket rows and executing a `SELECT` query per row to look up session statuses. Because SQLite is fast and local, this didn't cause an immediate application crash, but the performance cost still scales linearly with the number of tickets.
**Action:** Replaced loop-based individual queries with single `LEFT JOIN` queries, handling cases where the joined table may not yet exist (using fallback `try/catch` queries).

## 2024-09-07 - Pause UI polling when tab is inactive
**Learning:** The visualizer app relies heavily on active polling (`setInterval` every 500ms - 5000ms) to keep data fresh across multiple components (`MissionControl`, `KanbanBoard`, `SessionsList`, etc). This polling continued relentlessly even when the tab was hidden, draining client resources and keeping unnecessary load on the backend.
**Action:** Use `if (document.hidden) return;` inside high-frequency polling functions to pause API requests when the tab is out of focus. This is a crucial pattern for any real-time observability app built on polling.
