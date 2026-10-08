## 2024-10-24 - Async Loading States Accessibility
**Learning:** Adding `aria-busy="true"` to buttons during asynchronous operations (like form submissions or data refresh) is an effective pattern to inform screen reader users that a process is ongoing, without needing complex ARIA live regions or visual-only spinner cues.
**Action:** Always add `aria-busy` linked to the same reactive variable that controls the visual loading spinner and disabled state for interactive buttons.
