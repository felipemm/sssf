# adws → .adws: init resets the footprint, only the DB survives

2.0 makes the flows the only work entry point, so the stamped `adws/` tree is
rebuilt from scratch. `sssf init` on a legacy project now backs up the old
`adws/` folder and writes a fresh hidden `.adws/` from the current templates;
the only state carried forward is the project DB (`.adws/data/sssf.db`),
migrated in place to the latest schema. Legacy chains, prompts, and config are
not migrated — they are superseded by the v2 chains and would silently drift.

Status: accepted

Consequences:
- Legacy projects must run `sssf init` before any flow verb works; flow verbs
  fail with an upgrade warning otherwise.
- Project-specific customization inside `adws/` (edited chains, custom
  prompts, `deploy.yaml`) must be re-applied to the fresh `.adws/` by hand;
  the backup keeps the old tree available under the `sssf-<timestamp>/` backup
  prefix.
- All path references move to the hidden `.adws/` directory: the v2 layout in
  `paths.py`, `sweep`, the viz server's db paths, and the registry's db entry.
