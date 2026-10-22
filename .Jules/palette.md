
## 2026-09-22 - Session Trace Block Accessibility
**Learning:** Interactive visual timeline blocks relying on native titles and nested spans are inconsistently announced by screen readers.
**Action:** Always add explicit aria-label (for context/status) and aria-current="step" (for active state) to complex visual timeline buttons.
