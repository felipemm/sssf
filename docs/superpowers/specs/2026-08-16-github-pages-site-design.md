# GitHub Pages Site — Design

Date: 2026-08-16
Status: Approved in chat (2026-08-16)
Branch: `feat/github-pages-site` (worktree `.worktrees/github-pages-site`)

## Goal

Publish `sssf` at `https://felipemm.github.io/sssf/`: a dark, control-room-styled
homepage that explains what the factory does and how, embeds the reference video
("My Super Simple Software Factory" — IndyDevDan), and a detailed multi-page
docs section covering core components, the visualizer, and the full CLI
reference. The site is validated with Impeccable (59 deterministic detector
rules) before shipping.

## Stack

- **Astro** static site in a new `site/` directory (the repo's `docs/` folder is
  taken by superpowers specs/plans).
- `base: '/sssf/'`, `site: 'https://felipemm.github.io'`.
- Self-hosted fonts. No UI framework — plain Astro components + global CSS.
- Deploy via GitHub Actions (`actions/upload-pages-artifact` +
  `actions/deploy-pages`); Pages source = GitHub Actions.

## Pages

Homepage `/`:

- Hero — "Agent proposes. Code disposes." + terminal mock + swim-lane motif
- What it does + three principles (Observable / Customizable / Reusable)
- Embedded reference video with credit to disler / IndyDevDan
- Core concepts: ADWs, phases (engineer/agent/code lanes), typed envelopes,
  gates, SQLite trace
- Quickstart (install → init → run → sessions → viz)
- Feature grid: sandboxed parallel runs, kanban, status dashboard, healer,
  ticketing
- Footer: MIT license, origin credit, docs links

Docs (sidebar layout, dark):

1. `/docs/` overview + reading order
2. `/docs/quickstart` — prerequisites, install, first run, smoke test
3. `/docs/core-concepts` — ADW, phases, envelopes, gates, success semantics
4. `/docs/architecture` — core components (adw_modules, engine, registry,
   sandbox, healer, ticketing, obs)
5. `/docs/visualizer` — all views + background service
6. `/docs/cli` — full command reference (every command + flag)
7. `/docs/configuration` — sssf.config.yaml, roster, tools vs writes
8. `/docs/run-semantics` — re-runs, failure handling, WAL
9. `/docs/sandbox`, `/docs/healer`, `/docs/ticketing`

## Design system

- Palette: deep charcoal `#0a0c10` base, lighter panels, hairline borders.
  Accents = roster lane colors (planner `#a78bfa`, builder `#22d3ee`, + others
  from `sssf.config.yaml`). No purple-gradient slop, restrained glow.
- Type: Space Grotesk (display), IBM Plex Mono (code), non-Inter body face.
- Motifs: phase lanes, terminal windows, status dots, cost/token chips.
- No bounce easing.

## Validation

- `npx impeccable detect site/dist` — the 59 deterministic rules, fix all
  findings before merge.
- Manual pass: a11y (contrast, focus), responsive (mobile nav), links.

## Out of scope

- No JS frameworks in the page bundle.
- No changes to engine/CLI code.
- Repo Pages settings (source = GitHub Actions) created via API during deploy.
