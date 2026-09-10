# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Use GitHub's private vulnerability reporting instead: go to the
[Security tab](../../security/advisories/new) and open a draft advisory. That
notifies the maintainers privately.

Expect an initial response within a week or so. This is a side project, not a
funded product — response times are best-effort.

## Scope

This app is designed to run on your own machine and store data locally in SQLite.
The security-relevant surfaces are:

- **Public links.** `start-tunnel.cmd` exposes the local app through a Cloudflare
  Quick Tunnel. Anyone with the URL can reach it. Set an organizer password under
  Settings → Security before exposing the admin tunnel.
- **Guest invite tokens.** `/invite/<token>` is deliberately reachable without a
  login so guests can respond. Tokens are random and unguessable, but treat an
  invite URL as a secret.
- **Participant data is personal data.** Home addresses and coordinates for real
  people are exactly the kind of thing that should never end up in a git commit,
  a screenshot, or a bug report. Please scrub before sharing.
