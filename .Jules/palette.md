## 2026-09-21 - Escape Key Support for Modals
**Learning:** The TicketModal lacks native Escape key support to close, failing basic keyboard accessibility.
**Action:** Add global 'keydown' event listener for Escape key to close custom modals in the visualizer.
