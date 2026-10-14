## 2024-10-14 - shallowRef optimization in SessionTrace
**Learning:** In Vue, replacing arrays of complex objects without mutating them enables the use of `shallowRef` to prevent severe deep reactivity overhead. This provides a major performance boost for large API payloads.
**Action:** Use `shallowRef` for large, read-only or entirely replaced object arrays, and explain its safe usage in inline comments.
