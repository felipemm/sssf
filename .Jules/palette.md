## 2026-09-11 - Added aria-expanded to collapsible buttons
**Learning:** Some toggle buttons for collapsible panels/accordions in Vue components lacked the `aria-expanded` attribute, limiting screen reader accessibility.
**Action:** Always add `:aria-expanded="state"` to the toggle `<button>` when building or maintaining a collapsible UI component.
