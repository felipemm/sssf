# 2.0 verb surface: flows-only work entry, legacy verbs removed

With `plan` / `implement` / `deploy` as the only sanctioned work entry points,
the pre-machine verbs are removed outright — no deprecation period, 2.0 is a
breaking release: `spec create`, `ticket add`, `ticket run`, the trace verbs
(`sessions` / `phases` / `tail` / `procs`), and the manual `mr` CLI (the deploy
chain registers its MRs itself through the ticketing API the monitor reads).
`ticket sync` absorbs writeback and becomes the bidirectional tracker mirror —
pull + push: the pull follows each provider's configured filter (JQL for jira,
in `ticketing.yaml`); the push applies sssf control labels (needs-triage /
ready-for-agent / …) on every sync and transitions `done` tickets to closed,
appending machine comments (rejection feedback, closing note). All wired
origins — jira included, which may already carry labels from sssf or other
sources — get the label control. `ticket writeback` is deleted. Operational verbs
stay: `sandbox` (the board's stop/restart shell it), `heal`, `sweep`, `notify`,
`viz`, and `init` / `upgrade` / `doctor` / `projects`.

Status: accepted

Consequences:
- Legacy `adw_simple_sdlc` stamped projects lose their run path; the 0007
  init reset is mandatory before flows work.
- The board/modal run paths move to `flow implement`, and ticket actions are
  re-vocabularied to machine statuses (`ready-for-agent` backlog, `blocked`
  failed column) so the UI and the machine can never disagree.
- The only writer trigger for tracker mirroring is `ticket sync` (CLI or the
  tracker page's sync button) — human checkpoint preserved; nothing writes to
  trackers on its own.
