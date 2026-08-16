/**
 * Model → provider icon, by contains-check on the model name.
 *
 * Icons live in public/models/ (served from the site root). First matching
 * needle wins; unknown models render no icon.
 */

// Covers every GenPlat family (checked against the GenPlat /models catalog,
// 161 models). First matching needle wins; the ifood_* aliases resolve to
// their target model's icon (per GenPlat /model/info).
const MODEL_ICONS: [needles: string[], icon: string][] = [
  // ifood aliases → target model's family
  [['ifood_cheap-thinking'], '/models/qwen.svg'],
  [['ifood_smart-thinking', 'ifood_cheap'], '/models/deepseek.svg'],
  [['ifood_default', 'ifood_smart'], '/models/gemini.png'],
  [['ifood_sleep'], '/models/ifood.svg'],
  [['claude', 'opus', 'sonnet', 'haiku', 'fable'], '/models/claude.png'],
  [['gemini', 'chirp3'], '/models/gemini.png'],
  [['kimi', 'moonshot'], '/models/kimi.png'],
  [['gpt', 'openai', 'codex', 'o3', 'o4', 'whisper', 'tts', 'text-embedding', 'omni', 'embed', 'gpt-oss'], '/models/openai.png'],
  [['glm', 'zai', 'z.ai'], '/models/zai.png'],
  [['deepseek'], '/models/deepseek.svg'],
  [['grok'], '/models/xai.svg'],
  [['llama4', 'llama'], '/models/meta.svg'],
  [['qwen'], '/models/qwen.svg'],
  [['minimax'], '/models/minimax.svg'],
  [['command', 'cohere'], '/models/cohere.svg'],
  [['nova'], '/models/nova.svg'],
]

export function modelIcon(model: string | null | undefined): string | null {
  if (!model) return null
  const m = model.toLowerCase()
  for (const [needles, icon] of MODEL_ICONS) {
    if (needles.some((n) => m.includes(n))) return icon
  }
  return null
}

/** Keep provider-qualified IDs compact while preserving the full ID in titles. */
export function modelName(model: string | null | undefined): string {
  if (!model) return ''
  return model.split('/').filter(Boolean).at(-1) ?? model
}
