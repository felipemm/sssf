## 2026-09-09 - Disclosure Widget Accessibility
**Learning:** Disclosure widgets (like accordions or collapsible sections) across this app's components (DetailSection, PhaseDetail) lacked proper accessibility states (`aria-expanded`, `aria-controls`), making them hard to navigate for screen reader users. Visual-only cues like carets (`▾` / `▸`) were not hidden from screen readers.
**Action:** Always map toggle buttons with `aria-expanded` and `aria-controls` to their corresponding content blocks (using uniquely generated IDs via Vue's `useId`), and mark decorative visual indicators with `aria-hidden="true"`.

## 2026-09-10 - Add aria-expanded to collapsibles
**Learning:** Interactive collapsible elements (accordions/panels) without 'aria-expanded' attributes do not communicate their state (open/closed) to screen readers. Adding this attribute to existing button toggles based on component state significantly improves a11y.
**Action:** Use existing open/closed state variables to bind ':aria-expanded' to toggle buttons for expanding panels (e.g., in Vue: ':aria-expanded="open"').

## 2026-09-11 - Added aria-expanded to collapsible buttons
**Learning:** Some toggle buttons for collapsible panels/accordions in Vue components lacked the `aria-expanded` attribute, limiting screen reader accessibility.
**Action:** Always add `:aria-expanded="state"` to the toggle `<button>` when building or maintaining a collapsible UI component.

## 2024-09-14 - [Add loading spinners to inline async buttons]
**Learning:** When implementing async actions in inline cards (like TicketCard), disabled states alone are insufficient feedback. Users need an active loading indicator (like LoaderCircle) directly on the action button to confirm their click registered, especially since board refetches can take a second.
**Action:** Add spinners and `aria-busy` attributes to disabled action buttons.
