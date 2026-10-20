# Security Policy

## Supported versions

Security fixes land on the latest release published to [PyPI](https://pypi.org/project/ak5/) and on the `master` / active `dev/*` integration branches. Older patch lines may not receive backports.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security-sensitive reports.

1. Use [GitHub Security Advisories](https://github.com/fritzprix/ak5/security/advisories/new) for this repository, **or**
2. Email the maintainer via the address on the [GitHub profile](https://github.com/fritzprix) with subject `[ak5 security]`.

Include: affected version, impact, reproduction steps (or a PoC), and whether you plan a coordinated disclosure.

We aim to acknowledge within **7 days** and to publish a fix or mitigation advisory when a release is ready.

## Scope notes

- Agents that share the **same OS user** as the server can read the local database and signing material; that is outside the network authz model. See [docs/spec/identity.md](docs/spec/identity.md) §8.2.
- `AK5_DEV_INSECURE_IDENTIFY` and `ak5 serve --insecure` are development-only escapes and must not be enabled on exposed hosts.
