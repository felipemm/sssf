## 2024-11-03 - Avoid `aria-label` on buttons with dynamic state text
**Learning:** Using `aria-label` on elements (like card buttons or sync buttons) that contain complex inner content or dynamic state text (e.g., "syncing...") completely overrides the inner text for screen readers. This causes a loss of critical dynamic context.
**Action:** Instead of `aria-label`, insert a visually hidden span (e.g., `<span class="sr-only">Action: </span>`) inside the element to provide the primary action context while preserving the surrounding semantic content, metadata, or state changes.
