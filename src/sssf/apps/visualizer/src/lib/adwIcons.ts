import {
  Bot,
  ClipboardList,
  Eye,
  FileText,
  FlaskConical,
  Gauge,
  Hammer,
  Layers,
  ListChecks,
  MessageSquareText,
  Palette,
  Radar,
  ShieldCheck,
  Workflow,
} from 'lucide-vue-next'
import type { Component } from 'vue'

// One icon per ADW type; a chained run ('adw_plan + adw_build_test') takes its
// first ADW's icon, anything unknown falls back to Bot.
export const ADW_ICONS: Record<string, Component> = {
  adw_simple_sdlc: Workflow,
  adw_sdlc_full: Palette, // simple_sdlc + the impeccable design pass
  adw_plan: ClipboardList,
  adw_build: Hammer,
  adw_build_test: FlaskConical,
  adw_build_review: Eye,
  adw_plan_build: Layers,
  adw_plan_build_test: ListChecks,
  adw_plan_build_test_quality: Gauge,
  adw_document: FileText,
  adw_quality: ShieldCheck,
  adw_prompt: MessageSquareText,
  adw_scout: Radar,
}

export function adwIconFor(adwName: string | null): Component {
  const first = adwName?.split(' + ')[0]?.trim()
  return (first && ADW_ICONS[first]) || Bot
}
