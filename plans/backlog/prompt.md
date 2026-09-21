# ISSUES

Issues JSON is provided at start of context — a condensed index of open GitHub issues with `number`, `title`, `state`, `labels`, and `web_url` (descriptions and comments are intentionally NOT included to keep the prompt small).

You've also been passed a file containing the last 10 RALPH commits (SHA, date, full message). Review these to understand what work has been done.

# TASK SELECTION

Pick the next task. Prioritize tasks in this order:

1. Critical bugfixes
2. Tracer bullets for new features

Tracer bullets comes from the Pragmatic Programmer. When building systems, you want to write code that gets you feedback as quickly as possible. Tracer bullets are small slices of functionality that go through all layers of the system, allowing you to test and validate your approach early. This helps in identifying potential issues and ensures that the overall architecture is sound before investing significant time in development.

TL;DR - build a tiny, end-to-end slice of the feature first, then expand it out.

3. Polish and quick wins
4. Refactors

Only issues labelled `ready-for-agent` are provided (the loop filters by that
label). If no open, implementable issues remain, output `<promise>COMPLETE</promise>`.

# EXPLORATION

For the chosen issue, fetch the full details and comments:

```
gh issue view <number> --comments
```

Explore the repo and fill your context window with relevant information that will allow you to complete the task. Always start from an up-to-date main: check out main, pull the latest, then create a new branch. If a local branch with that name already exists (e.g. from a previous run), delete it first so you recreate it from fresh main. The branch name must start with the issue number and a short description of the work you are doing.

```
git checkout main
git pull --ff-only origin main
git checkout -b <branch-name>
```

# EXECUTION

Run /implement for the chosen issue — implement the work described in the issue:

- Use /tdd where possible, at pre-agreed seams.
- Run the repo's checks regularly and the full suite at least once at the end. The CI workflow (`.github/workflows/ci.yml`) is the source of truth; the equivalent local commands are:
  - `uv run pytest` (repo root)
  - `uv run ruff check src/sssf tests`
  - `uv run mypy src/sssf`
  - `bun test` (in `src/sssf/apps/visualizer`)
  - `npm run build` (in `site`)
- Once done, use /code-review to review the work.
- Commit your work to the current branch.

ONLY WORK ON A SINGLE ISSUE PER RUN.

# COMMIT

Make a git commit. The commit message must:

1. Start with `RALPH:` prefix and reference the issue (`#<number>`)
2. Include task completed + issue reference
3. Key decisions made
4. Files changed
5. Blockers or notes for next iteration

Keep it concise.

# THE TICKET

If the task is complete, submit a PR to main — never close the issue manually:

1. Push the branch: `git push -u origin <branch-name>`
2. Create the PR with `gh pr create`, base `main`. The PR title must start with the issue number and a short description of the work you are doing. In the PR body, reference the issue with a closing keyword (`Closes #<number>`) so GitHub automatically closes the issue when the PR is merged:

```
gh pr create --base main --title "<number>: <short description>" --body "Closes #<number>

<summary of what was done>"
```

The issue closes automatically on merge — do NOT run `gh issue close`.

If the task is not complete, leave a comment on the GitHub issue with what was done:

```
gh issue comment <number> --body "<what was done>"
```

# FINAL RULES

ONLY WORK ON A SINGLE TASK.
