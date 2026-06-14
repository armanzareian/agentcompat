# Security Policy

## Supported versions

Until the first stable release, security fixes are applied to the latest `0.x` release and
`main`.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability
reporting for this repository. Include the affected version, impact, reproduction, and any
suggested mitigation.

## Data handling

AgentCompat is designed for local, offline analysis. It does not transmit schemas or traces.
Inputs may still contain sensitive data, so use anonymized fixtures in bug reports and avoid
checking production traces into source control.

The parser enforces file-size and record-count limits. It does not execute tool calls or evaluate
trace content as code. Current limitations and privacy controls are documented in
[docs/architecture.md](docs/architecture.md).
