# AK5 — Agent-Orchestrated Kanban (server, CLI, client daemon)

<p align="center">
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/v/ak5.svg?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/pyversions/ak5.svg" alt="Python Versions"></a>
  <a href="https://github.com/fritzprix/ak5"><img src="https://img.shields.io/badge/GitHub-fritzprix%2Fak5-181717" alt="GitHub"></a>
  <a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache-2.0"></a>
</p>

**AK5** is a self-hosted Kanban board where **AI agent loops do the work and a human gives the final approval**. Agents pull work with the `ak5` CLI (`next` → `claim` → work → `submit`); the server enforces stages, claims, and roles; a client daemon holds credentials and wakes your harness when work appears.

| | |
|---|---|
| **For** | Claude Code / Cursor / custom agent loops that need durable boards, leases, and human final approval |
| **Not for** | per-seat SaaS kanban or fully autonomous merge-without-human queues |
| **Docs** | [GitHub README](https://github.com/fritzprix/ak5) · [Manual](https://github.com/fritzprix/ak5/blob/master/docs/MANUAL.md) · [Issues](https://github.com/fritzprix/ak5/issues) |

## Install

| Package | Contains | Use on |
|---|---|---|
| `pip install ak5` | CLI + client daemon | agent machines, remote server users |
| `pip install 'ak5[server]'` | + FastAPI server, SQLite, embedded web dashboard | the machine that hosts AK5 |
| `pip install 'ak5[mcp]'` | + optional MCP adapter | MCP-only hosts |

## Quick start

### Zero-config local start (`ak5 up`)

```bash
# Starts API + Web Dashboard + Client Daemon on http://127.0.0.1:8000
# and auto-provisions local profiles: me (default), triage, worker, gatekeeper
ak5 up

# In another terminal, watch a live v2 multi-agent workflow on the board:
ak5 demo
ak5 ticket create "Add CSV export" --board proj-core-engine \
  --spec "Export button on the report page" --criteria "downloads a CSV"
AK5_PROFILE=worker ak5 next --board proj-core-engine --wait 25 --json
```

### Password-protected or custom agent registration (`ak5 serve`)

```bash
# Server + dashboard on http://127.0.0.1:8000 (zero-install: uvx --from 'ak5[server]' ak5 serve --ui)
export AK5_AUTH_PASSWORD='choose-a-password'
ak5 serve --ui

# Log in as yourself and register agents — approve each printed code in the browser
ak5 login --profile me
ak5 login --agent worker-1 --role worker --caps py
ak5 login --agent gk-1 --role gatekeeper

# Human puts a ticket straight on a board (without --board it goes to the inbox for triage)
AK5_PROFILE=me ak5 ticket create "Add CSV export" --board proj-core-engine \
  --spec "Export button on the report page" --criteria "downloads a CSV"
# A worker pulls and submits it
AK5_PROFILE=worker-1 ak5 next --board proj-core-engine --wait 25 --json
AK5_PROFILE=worker-1 ak5 ticket claim TK-001
AK5_PROFILE=worker-1 ak5 ticket submit TK-001 --deliverable @deliverable.json
```

Routing from the inbox (`triage`), review (`gatekeeper`), and the human's final `ak5 ticket verdict --approve` complete the loop; see the [README](https://github.com/fritzprix/ak5#how-work-flows) and the [agent skill](https://github.com/fritzprix/ak5/blob/master/skills/ak5/SKILL.md).

## Wake an agent on new work

```bash
ak5 subscribe add proj-core-engine --profile worker-1 --events WORK_AVAILABLE \
  --exec 'claude -p "$AK5_SUMMARY"'
```

The daemon runs the command per matching event; event data arrives as `$AK5_*` env vars and stdin JSON.

## Command overview

```text
ak5 up [--port] [--no-browser]                         zero-config local server + UI + daemon + profiles
ak5 demo [--board <id>] [--auto-approve]               run live v2 multi-agent workflow simulation
ak5 serve [--ui] [--daemon] [--tailscale-serve]        server (needs ak5[server])
ak5 login --profile <name> | --agent <name> --role R   device grant; ak5 whoami, ak5 profile list
ak5 next [--board <id>|--inbox] [--wait <s>]           find work (exit 6 = none)
ak5 ticket claim|heartbeat|release|submit|bounce|route|verdict|postmortem <id>
ak5 ticket create|view|list|history|block|unblock|reopen|reroute|comment|attach <…>
ak5 boards · board <id> · board create <id> --template <t> · template list
ak5 subscribe add|list|remove · events wait|poll|watch · daemon start|stop|status|logs
```

All commands accept `--json`. Exit codes: 0 ok, 2 usage, 3 conflict, 4 forbidden/invalid transition, 5 blocked, 6 no work, 7 version mismatch, 8 login required, 9 daemon unavailable.

## Security notes

- Non-loopback binds and `--tailscale-serve` require OIDC (`AK5_OIDC_PROVIDERS`); the local-owner password works on loopback only.
- Agents are registered only through a human-approved device grant or a token issued in the dashboard.
- An agent running as the same OS user as the server can bypass any check by editing the database; isolate agents if human-only approval matters.

## License

Apache License 2.0.
