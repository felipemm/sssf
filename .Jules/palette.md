## 2024-10-06 - Improve screen reader context for complex toggle buttons
**Learning:** When adding `aria-label` to buttons containing complex visual content like counts, badges, and nested text (e.g. ticket counts on Kanban columns or phase names on event toggles), screen readers completely overwrite the inner text, causing users to lose vital context.
**Action:** Instead of applying an `aria-label` to the outer button, place a visually hidden span (`<span class="sr-only">Toggle column: </span>`) inside the button before its visual content to maintain accessibility without stripping metadata.
