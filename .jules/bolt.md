## 2024-05-18 - Vue deep reactivity overhead for large datasets
**Learning:** In the Vue visualizer app, using `ref()` for large API response payloads (like event traces in SessionTrace.vue) causes severe deep reactivity performance overhead, which can block the main thread. This happens because Vue recursively walks the objects to setup reactivity proxies.
**Action:** Use `shallowRef()` instead of `ref()` for large API datasets that are fully replaced upon update rather than deeply mutated, to bypass Vue's deep reactivity proxying.
