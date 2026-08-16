# Security Policy

## Supported versions

sssf follows a single-supported-version model: only the latest release is
supported with security fixes.

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | ✅                 |
| < 1.0   | ❌                 |

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Report them
privately through GitHub's Security Advisory feature:

**[Report a vulnerability](https://github.com/felipemm/sssf/security/advisories/new)**

You should receive an acknowledgment within a few days. Please include, when
possible:

- A description of the vulnerability and the affected version(s)
- Steps to reproduce (or a minimal PoC)
- Any potential impact you've assessed

We ask that you keep details private until a fix is released and announced in
the [CHANGELOG](CHANGELOG.md). Once a fix is out, you'll be credited for the
report (unless you prefer to stay anonymous).

## Scope

The security of a run's **sandbox isolation** (the docker worktrees) and the
**trace database** (WAL-mode SQLite with local data) matters most. Note that
the visualizer binds to localhost by default and is a local-development tool —
exposing `sssf viz` beyond your machine is out of the supported threat model.
