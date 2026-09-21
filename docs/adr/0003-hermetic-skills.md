# Hermetic skills: agent sessions never load global skills

Every agent session runs `pi --no-skills` and loads only the sssf-curated
skill set, vendored with the codebase and stamped into each project at
`sssf init`. The vendored set is the transitive closure of the workflow skills
(grill-me, wayfinder, grill-with-docs, to-spec, to-tickets, triage, implement,
code-review, tdd, grilling, domain-modeling, codebase-design, plus in-directory
reference docs and agent defs), loaded per-agent as subsets via a `skills:`
key in the roster config. References to external setup tooling
(`/setup-matt-pocock-skills`) are rewritten to sssf's own stamped config.

## Considered Options

- **Rely on the operator's global skill library**: zero packaging cost, but a
  run's behavior drifts with the operator's machine and a missing or renamed
  skill silently changes what agents can do. Rejected: a factory must be
  reproducible; the same project must behave the same on any machine.
- **Vendor the workflow skills only, let skills pull their own dependencies**:
  rejected once the audit showed the closure (wayfinder → grilling +
  domain-modeling, implement → tdd + code-review, tdd → codebase-design, …);
  an unpinned dependency defeats hermeticity.

## Consequences

- The stamped skill set is per-project and versioned with the project's
  `adws/`, so a project keeps behaving the way it was initialized even after
  the global library changes.
- New workflow skills must be added to the vendored closure in the same change
  that starts using them.
