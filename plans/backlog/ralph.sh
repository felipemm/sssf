#!/bin/bash
# Ralph loop: run /implement for each open ready-for-agent GitHub issue, one
# per iteration. Replicates mattpocock/course-video-manager plans/backlog/afk.sh,
# adapted for this repo: GitHub issues via gh, `pi` coding agent
# (non-interactive). Skills (/implement, /tdd, /code-review) come from pi's
# skill library (~/.pi/agent/skills) — auto-discovered, nothing to load
# explicitly. Output streams live via --mode json (NDJSON events); pi's plain
# text mode buffers until exit, which makes long runs look hung.
set -eo pipefail

cd "$(git rev-parse --show-toplevel)"

# Only issues explicitly marked ready-for-agent are fed to the loop — never
# unlabelled/in-progress/blocked ones. Array on purpose: a string would pass
# "--label ready-for-agent" as ONE argv entry and gh rejects it.
LABEL_FILTER=(--label ready-for-agent)

if [ -z "$1" ]; then
  iterations=$(gh issue list --state open "${LABEL_FILTER[@]}" --json number | jq 'length')
  echo "No iteration count given — processing $iterations ready-for-agent issue(s)."
else
  iterations=$1
fi

if [ "${iterations:-0}" -eq 0 ]; then
  echo "No ready-for-agent issues — nothing to do."
  exit 0
fi

# Print each completed assistant message, and each completed tool call with its
# arguments. All field reads are null-safe (// fallbacks) so the filter can
# never die on event-shape drift — a jq error here SIGPIPEs pi and silently
# kills the loop under set -e. At toolcall_end contentIndex is null: the call
# object lives in .assistantMessageEvent.toolCall.
stream_filter='(.type == "message_end" and .message.role == "assistant") as $msg |
  (.type == "message_update" and .assistantMessageEvent.type == "toolcall_end") as $tool |
  if $msg then
    ([.message.content[]? | select(.type == "text").text] | join("")) as $text |
    if $text != "" then $text + "\n" else empty end
  elif $tool then
    "\n▶ " + (.assistantMessageEvent.toolCall.name // "") + " " + ((.assistantMessageEvent.toolCall.arguments // {}) | tojson) + "\n"
  else empty end'

# COMPLETE is read ONLY from the assistant's own final messages — never from
# the raw stream (which can carry prompt/context text).
complete_filter='select(.type == "message_end" and .message.role == "assistant") | [.message.content[]? | select(.type == "text").text] | join("")'

for ((i=1; i<=iterations; i++)); do
  echo "Iteration $i"
  echo "--------------------------------"
  tmpfile=$(mktemp)
  trap "rm -f $tmpfile" EXIT

  # Ready-for-agent issues only, condensed to an index (full descriptions would
  # overflow the context window; the agent fetches details with gh issue view).
  issues=$(gh issue list --state open "${LABEL_FILTER[@]}" --json number,title,state,labels,url | jq -c '[.[] | {number, title, state, labels: [.labels[].name], web_url: .url}]')
  ralph_commits=$(git log --grep="RALPH" -n 10 --format="%H%n%ad%n%B---" --date=short 2>/dev/null || echo "No RALPH commits found")

  # The streaming pipeline must not kill the loop: capture pi's exit status
  # instead of letting pipefail abort the script mid-run.
  set +e
  pi -p \
    --approve \
    --no-session \
    --mode json \
    --model litellm/deepseek-v4-flash-official \
    "$issues Previous RALPH commits: $ralph_commits @plans/backlog/prompt.md" 2>&1 \
  | tee "$tmpfile" \
  | jq --unbuffered -r "$stream_filter"
  pi_status=${PIPESTATUS[0]}
  set -e

  result=$(jq -r "$complete_filter" "$tmpfile" 2>/dev/null)
  if [[ "$result" == *"<promise>COMPLETE</promise>"* ]]; then
    echo "Ralph complete after $i iterations."
    exit 0
  fi
  echo "Iteration $i finished (pi exit $pi_status)."
done
