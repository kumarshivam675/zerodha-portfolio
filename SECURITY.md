# Security

This app is designed to run **on your own machine, for one person**. It has no login: every port binds to
`127.0.0.1`, and the API refuses state-changing requests that browsers mark as cross-site. Don't expose
it to a network without putting authentication in front of it.

What it holds: your Kite access token (in Postgres, unreadable by the agent's read-only database role),
your holdings and trades, and your Anthropic API key and Slack webhook (in `.env`, git-ignored).

The AI analyst reads web pages, which may contain prompt-injection attempts. See
[Security and privacy](README.md#security-and-privacy) for how the app limits what an injected
instruction could do.

To report a vulnerability, please open a GitHub security advisory ("Report a vulnerability" under the
Security tab) rather than a public issue.
