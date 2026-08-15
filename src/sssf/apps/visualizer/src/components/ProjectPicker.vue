<script setup lang="ts">
import { onMounted } from 'vue'
import { useProjects, fetchProjects } from '../lib/api'

// The global service exposes every registered project here; adhoc single-db
// mode returns an empty list, and the picker hides itself.
const emit = defineEmits<{ (e: 'select', name: string): void }>()
const { selectedProject, projects } = useProjects()

onMounted(() => {
  void fetchProjects()
})
</script>

<template>
  <select
    v-if="projects.length"
    class="project-picker"
    :value="selectedProject ?? ''"
    title="trace db"
    @change="emit('select', ($event.target as HTMLSelectElement).value)"
  >
    <option v-for="p in projects" :key="p.name" :value="p.name">{{ p.name }}</option>
  </select>
</template>

<style scoped>
.project-picker {
  appearance: none;
  background: rgba(11, 15, 24, 0.9);
  border: 1px solid rgba(200, 155, 255, 0.25);
  border-radius: 8px;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  padding: 5px 30px 5px 12px;
  cursor: pointer;
  max-width: 240px;
  background-image: linear-gradient(45deg, transparent 50%, var(--purple) 50%),
    linear-gradient(135deg, var(--purple) 50%, transparent 50%);
  background-position:
    calc(100% - 15px) 50%,
    calc(100% - 10px) 50%;
  background-size: 5px 5px;
  background-repeat: no-repeat;
}

.project-picker:hover {
  border-color: rgba(200, 155, 255, 0.5);
}

.project-picker:focus {
  outline: none;
  border-color: var(--purple);
}

.project-picker option {
  background: #0b0f18;
  color: var(--text);
}
</style>
