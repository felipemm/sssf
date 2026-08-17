# impeccable pi skill (vendored)

Source: https://github.com/pbakaus/impeccable (npm: impeccable@3.6.0)
Vendored: 2026-08-16 via `npx impeccable install --providers=pi --scope=project`
Refresh: rerun that command in a temp dir, then copy `.pi/skills/impeccable` here
and bump the version above.

Why vendored: image builds stay deterministic and offline. The sandbox
entrypoint copies this into the container's pi home — skills are otherwise
deliberately excluded from the sandbox (they would distract ADW agents).
