## 2026-09-09 - Disclosure Widget Accessibility
**Learning:** Disclosure widgets (like accordions or collapsible sections) across this app's components (DetailSection, PhaseDetail) lacked proper accessibility states (`aria-expanded`, `aria-controls`), making them hard to navigate for screen reader users. Visual-only cues like carets (`▾` / `▸`) were not hidden from screen readers.
**Action:** Always map toggle buttons with `aria-expanded` and `aria-controls` to their corresponding content blocks (using uniquely generated IDs via Vue's `useId`), and mark decorative visual indicators with `aria-hidden="true"`.

## 2026-09-10 - Add aria-expanded to collapsibles
**Learning:** Interactive collapsible elements (accordions/panels) without 'aria-expanded' attributes do not communicate their state (open/closed) to screen readers. Adding this attribute to existing button toggles based on component state significantly improves a11y.
**Action:** Use existing open/closed state variables to bind ':aria-expanded' to toggle buttons for expanding panels (e.g., in Vue: ':aria-expanded="open"').
