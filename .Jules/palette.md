## 2026-09-22 - Session Trace Block Accessibility
**Learning:** Interactive visual timeline blocks relying on native titles and nested spans are inconsistently announced by screen readers.
**Action:** Always add explicit aria-label (for context/status) and aria-current="step" (for active state) to complex visual timeline buttons.

## 2023-10-24 - Screen Reader Context for Segmented Controls inside role="group"
**Learning:** When using `role="group"` for a segmented control (such as time windows like 7d, 30d, etc), screen readers announce the items within the group, but do not infer which item is currently active simply by visual styling (like an `.on` class). A missing state attribute leaves screen reader users blind to the current selection.
**Action:** Always add `aria-pressed="true"` to the active `<button>` (or `aria-selected` if using `role="tablist"` + `role="tab"`) inside segmented controls so the current active state is explicitly announced.
