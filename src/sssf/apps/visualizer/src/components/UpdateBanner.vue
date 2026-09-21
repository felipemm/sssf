<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Check, ChevronDown, Copy, Download, RefreshCw } from 'lucide-vue-next'
import { fetchUpdateCheck } from '../lib/api'
import type { UpdateReport } from '../lib/types'

const report = ref<UpdateReport | null>(null)
const checking = ref(false)
const open = ref(false)
const copied = ref(false)
let copyTimer: ReturnType<typeof setTimeout> | undefined

/** An update is available only when the check succeeded AND we are behind. */
const showBanner = () => report.value?.ok === true && (report.value.behind ?? 0) > 0

async function check() {
  if (checking.value) return
  checking.value = true
  try {
    report.value = await fetchUpdateCheck()
    if (!showBanner()) open.value = false
  } catch {
    // The server/CLI is unreachable or the install is not a git checkout —
    // stay silent; an absent banner is a healthy default.
    report.value = null
  } finally {
    checking.value = false
  }
}

function copyCommand() {
  const cmd = report.value?.command ?? 'sssf upgrade'
  const done = () => {
    copied.value = true
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => (copied.value = false), 2000)
  }
  if (navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(cmd).then(done).catch(() => {})
  } else {
    done()
  }
}

onMounted(check)
</script>

<template>
  <Transition name="banner">
    <div v-if="showBanner()" class="update-banner" role="status">
      <div class="bar" @click="open = !open">
        <Download :size="15" :stroke-width="2.2" class="icon" />
        <span class="title">Update available</span>
        <span class="detail">
          local <code>{{ report?.branch }}</code> is {{ report?.behind }} commit{{
            (report?.behind ?? 0) === 1 ? '' : 's'
          }}
          behind origin — run <code>{{ report?.command ?? 'sssf upgrade' }}</code>
        </span>
        <button
          class="check-btn"
          type="button"
          :disabled="checking"
          :title="checking ? 'Checking…' : 'Check for updates now'"
          aria-label="Check for updates now"
          @click.stop="check"
        >
          <RefreshCw :size="13" :stroke-width="2.2" :class="{ spinning: checking }" />
        </button>
        <ChevronDown :size="14" class="chev" :class="{ open }" />
      </div>
      <Transition name="detail">
        <div v-if="open" class="panel">
          <dl class="grid">
            <div><dt>local</dt><dd><code>{{ report?.local_sha }}</code></dd></div>
            <div><dt>origin</dt><dd><code>{{ report?.remote_sha }}</code></dd></div>
            <div><dt>branch</dt><dd>{{ report?.branch }}</dd></div>
            <div><dt>ahead / behind</dt><dd>{{ report?.ahead ?? 0 }} / {{ report?.behind ?? 0 }}</dd></div>
          </dl>
          <p v-if="(report?.dirty ?? 0) > 0" class="warn">
            Working tree has {{ report?.dirty }} uncommitted change{{
              (report?.dirty ?? 0) === 1 ? '' : 's'
            }}
            — the pull may refuse if it would clobber them (git will say so).
          </p>
          <div class="action-row">
            <code class="cmd">{{ report?.command ?? 'sssf upgrade' }}</code>
            <button class="copy-btn" type="button" :title="'Copy ' + (report?.command ?? 'sssf upgrade')" @click="copyCommand">
              <Check v-if="copied" :size="13" class="copied" />
              <Copy v-else :size="13" />
              {{ copied ? 'copied' : 'copy' }}
            </button>
            <span class="hint">then restart the services: <code>sssf viz stop && sssf viz</code> · <code>sssf heal stop && sssf heal start</code></span>
          </div>
        </div>
      </Transition>
    </div>
  </Transition>
</template>

<style scoped>
.update-banner {
  position: sticky;
  top: 61px;
  z-index: 9;
  margin: 0 28px;
  border: 1px solid rgba(232, 182, 74, 0.45);
  border-radius: 10px;
  background: rgba(20, 17, 8, 0.92);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  color: var(--text);
  font-size: 14px;
  overflow: hidden;
}

.bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  cursor: pointer;
}

.icon {
  color: var(--amber);
  flex: none;
}

.title {
  font-weight: 700;
  color: var(--amber);
  white-space: nowrap;
}

.detail {
  color: var(--dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

code {
  font-family: var(--mono);
  color: var(--cyan);
  background: rgba(90, 210, 221, 0.08);
  padding: 1px 5px;
  border-radius: 5px;
  font-size: 12.5px;
}

.check-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  margin-left: auto;
  flex: none;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--dim);
  cursor: pointer;
}

.check-btn:hover {
  color: var(--text);
  border-color: rgba(232, 182, 74, 0.5);
}

.check-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.spinning {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.chev {
  color: var(--faint);
  flex: none;
  transition: transform 0.2s ease;
}

.chev.open {
  transform: rotate(180deg);
}

.panel {
  border-top: 1px solid rgba(232, 182, 74, 0.25);
  padding: 12px 14px 14px;
  background: rgba(11, 15, 24, 0.6);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px 22px;
  margin: 0 0 10px;
}

.grid div dt {
  color: var(--faint);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 2px;
}

.grid div dd {
  margin: 0;
  font-family: var(--mono);
  font-size: 13px;
  color: var(--text);
}

.warn {
  margin: 0 0 10px;
  color: var(--amber);
  font-size: 13px;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.cmd {
  font-family: var(--mono);
  font-size: 13px;
  color: var(--text);
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--border);
  padding: 4px 10px;
  border-radius: 7px;
}

.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--dim);
  font-size: 12.5px;
  cursor: pointer;
}

.copy-btn:hover {
  color: var(--text);
}

.copied {
  color: var(--green);
}

.hint {
  color: var(--faint);
  font-size: 12.5px;
}

.hint code {
  font-size: 12px;
}

.banner-enter-active,
.banner-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.banner-enter-from,
.banner-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.detail-enter-active,
.detail-leave-active {
  transition: opacity 0.2s ease;
}

.detail-enter-from,
.detail-leave-to {
  opacity: 0;
}
</style>
