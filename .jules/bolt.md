## 2024-05-24 - Vue Deep Reactivity Overhead with Large Payloads
**Learning:** Large API response payloads of complex objects (like event traces) mapped to Vue's `ref()` can cause severe deep reactivity overhead, slowing down the interface significantly.
**Action:** Use `shallowRef()` instead of `ref()` for these large arrays/objects when they are entirely replaced and not mutated deeply (e.g. `events.value = [...]`). Add an inline comment justifying the safety of this optimization to code reviewers. Avoid `shallowRef()` for arrays of primitives, as `ref()` overhead is minimal for them.
