#!/bin/bash
# Single Ralph iteration: implement one open ready-for-agent GitHub issue.
# Streams pi output live via --mode json (text mode buffers until exit).
set -eo pipefail

cd "$(git rev-parse --show-toplevel)"

# Only issues explicitly marked ready-for-agent — never unlabelled/in-progress.
# Array on purpose: a string would pass "--label ready-for-agent" as ONE argv
# entry and gh rejects it.
LABEL_FILTER=(--label ready-for-agent)

# Print each completed assistant message, and each completed tool call with its
# arguments. Null-safe reads (// fallbacks) so the filter can never die on
# event-shape drift. At toolcall_end contentIndex is null: the call object
# lives in .assistantMessageEvent.toolCall.
stream_filter='(.type == "message_end" and .message.role == "assistant") as $msg |
  (.type == "message_update" and .assistantMessageEvent.type == "toolcall_end") as $tool |
  if $msg then
    ([.message.content[]? | select(.type == "text").text] | join("")) as $text |
    if $text != "" then $text + "\n" else empty end
  elif $tool then
    "\n▶ " + (.assistantMessageEvent.toolCall.name // "") + " " + ((.assistantMessageEvent.toolCall.arguments // {}) | tojson) + "\n"
  else empty end'

issues=$(gh issue list --state open "${LABEL_FILTER[@]}" --json number,title,state,labels,url | jq -c '[.[] | {number, title, state, labels: [.labels[].name], web_url: .url}]')
ralph_commits=$(git log --grep="RALPH" -n 10 --format="%H%n%ad%n%B---" --date=short 2>/dev/null || echo "No RALPH commits found")

pi -p \
  --approve \
  --no-session \
  --mode json \
  --model litellm/deepseek-v4-flash-official \
  "$issues Previous RALPH commits: $ralph_commits @plans/backlog/prompt.md" 2>&1 \
| jq --unbuffered -r "$stream_filter"
