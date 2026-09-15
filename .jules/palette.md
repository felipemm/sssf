## 2024-05-24 - Consistent Keyboard Focus via :focus-visible
**Learning:** This app frequently relies on `outline: none` for custom hover/focus states, particularly on inputs and picker dropdowns, leaving keyboard navigators with no reliable focus indicator.
**Action:** Always implement a global `*:focus-visible` ring (e.g. `outline: 2px solid var(--purple) !important;`) to guarantee keyboard accessibility is preserved, even when mouse-based custom active states strip out default outlines.
