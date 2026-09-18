## 2024-03-24 - Missing Button Types
**Learning:** Many interactive icons in `MissionControl.vue` (and potentially other components) were implemented as `<button>` elements but lacked an explicit `type="button"` attribute. While they function correctly outside of forms, this is an accessibility and safety risk as they default to `type="submit"` and could accidentally trigger form submissions if wrapped in a form tag later.
**Action:** Always ensure non-submit buttons have `type="button"` explicitly set. Add checking for missing type attributes to initial UI exploration scripts.
