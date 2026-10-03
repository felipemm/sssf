## 2026-10-03 - Screen reader text for cards
**Learning:** When a card serves as a button containing complex data (like a title and metadata), adding an `aria-label` to the button overrides all inner text, hiding critical metadata from screen readers. This causes an accessibility regression and violates WCAG (Label in Name).
**Action:** Instead of `aria-label`, use a visually hidden `<span>` (e.g., `<span class="sr-only">Open ticket: </span>`) prepended inside the button to provide action context while preserving the rest of the content.
