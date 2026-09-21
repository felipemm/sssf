# Slack notifications via bot token, thread-per-ticket, alerting only

Notifications use the Slack bot token (`chat.postMessage`) — one thread per
ticket across its lifecycle, five retries with backoff, and every payload
carries the URLs needed to act (workbench, MR, release, tompero). Notifications
alert only: human checkpoints (signoff, MR approval, promote confirmation)
stay in the tools — terminal and GitLab — never in Slack.

## Considered Options

- **Incoming webhook**: zero auth code, but thread-per-ticket replies are the
  flakiest part of webhooks, and threads were a hard requirement so a feature's
  notification history reads as one conversation. A bot token the operator
  already has makes threads native. Webhooks were also the wrong base for the
  planned next phase (interactive approve/reject buttons in the flow).

## Consequences

- The notify layer is a small adapter behind the flow; swapping transport in a
  later phase (e.g. richer blocks or buttons) does not touch call sites.
