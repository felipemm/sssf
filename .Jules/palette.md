## 2024-09-17 - Missing aria-expanded on collapsible sections
**Learning:** Collapsible/accordion patterns in this app (like `DetailSection` and Kanban column toggles) were completely missing the `aria-expanded` state, impacting screen reader usability as users would not know if a section was expanded or collapsed. Purely visual icons (chevrons) were also lacking `aria-hidden="true"`.
**Action:** When working with togglable or collapsible UI elements in this codebase, always ensure `aria-expanded` is bound to the open state, and decorative icons have `aria-hidden="true"`.
