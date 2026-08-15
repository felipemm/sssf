# Customizing a project

`sssf init` stamps the **customization surface** into your repo: everything you
are meant to edit lives in `adws/`, and nothing you are meant to edit lives
anywhere else. The engine (`sssf.adw_modules`) is package code — changing it is
contributing, not customizing (see `contributing.md`).

Three things are yours to shape per project:

## Your chains

Chains are the `adws/adw_*.py` scripts. Copy the closest starter chain, rename
it, and edit its phase list:

```python
# adws/adw_my_flow.py  — Phases: engineer → scout → build
from sssf.adw_modules import run
from sssf.adw_modules.data_types import AgentCall, EnvelopeBase

phases = [
    run.phase("scout", "recon", kind="agent"),
    run.phase("build", "implement", kind="agent"),
]
```

A phase needs: a name, a one-sentence `description` ("what it does and why" —
never a restatement of the name), and a `kind` (`agent` | `engineer` | `code`).
Agent phases carry `AgentCall(...)` with `output_type=`; the script ends in
`run.finish(accepted=...)`.

The **output contract is a synced triad**: (a) the `EnvelopeBase` subclass in
`sssf/adw_modules/data_types.py`, (b) the JSON example in the agent's `user.md`
`## Report` section, (c) `output_type=` at the call site. Change any one, update
all three in the same edit.

## Your roster

`adws/adw_sssf_config/sssf.config.yaml` declares the agents: models as
`provider/model-id` (any id registered in the coding agent's catalog), thinking
levels, per-agent `tools:` and `writes:`, and `protected_files` in `defaults`.

- `tools:` is a capability list; `writes:` is the boundary. `protected_files`
  (default: `adws/adw_modules/`, `adws/adw_sssf_config/`, `adws/adw_*.py`)
  keeps agents from editing the machinery that grades them.
- Prompts live at `adws/adw_data/prompt_engineering/{agent}/system.md` and
  `user.md` — identity in the system prompt, task shape in the user prompt.

## Your definition of done

Acceptance is per-ADW: pass `accepted=` to `run.finish()` so the exit code, the
session status, and the banner agree. Custom gates and quality commands are
**engine-level** — ship them as a project-local module your ADW imports, or as a
PR to the sssf tool repo (see `contributing.md`).
