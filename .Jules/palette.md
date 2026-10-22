## 2026-09-22 - Session Trace Block Accessibility
**Learning:** Interactive visual timeline blocks relying on native titles and nested spans are inconsistently announced by screen readers.
**Action:** Always add explicit aria-label (for context/status) and aria-current="step" (for active state) to complex visual timeline buttons.

## 2024-10-06 - Improve screen reader context for complex toggle buttons
**Learning:** When adding `aria-label` to buttons containing complex visual content like counts, badges, and nested text (e.g. ticket counts on Kanban columns or phase names on event toggles), screen readers completely overwrite the inner text, causing users to lose vital context.
**Action:** Instead of applying an `aria-label` to the outer button, place a visually hidden span (`<span class="sr-only">Toggle column: </span>`) inside the button before its visual content to maintain accessibility without stripping metadata.

## 2023-10-24 - Screen Reader Context for Segmented Controls inside role="group"
**Learning:** When using `role="group"` for a segmented control (such as time windows like 7d, 30d, 90d, etc), screen readers announce the items within the group, but do not infer which item is currently active simply by visual styling (like an `.on` class). A missing state attribute leaves screen reader users blind to the current selection.
**Action:** Always add `aria-pressed="true"` to the active `<button>` (or `aria-selected` if using `role="tablist"` + `role="tab"`) inside segmented controls so the current active state is explicitly announced.
