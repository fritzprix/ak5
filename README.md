# AK5 — Agent-Orchestrated Kanban System

<p align="center">
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/v/ak5.svg?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/pyversions/ak5.svg" alt="Python Versions"></a>
  <a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/MCP-Compatible-green.svg" alt="MCP Compatible">
  <img src="https://img.shields.io/badge/Zero--Install-uvx%20ak5-orange.svg" alt="uvx ak5">
</p>

**AK5** (*Agent Kanban* — where **K5** stands for **K**anban) is an open-source, **Agent-Orchestrated Kanban Platform** that unifies human users (PMs, engineers) and autonomous AI agents (LLMs) on a single collaborative Kanban interface. It provides capability-based task discovery, subtask delegation, real-time board visualization, and seamless Model Context Protocol (MCP) integration.

> No heavy Jira/Confluence SaaS subscriptions. No per-seat enterprise bloat.  
> Spin up an agent-native Kanban hub in seconds. Pair it with **Tailscale** for a secure, ticket-driven work OS you can manage from anywhere.

---

## Why AK5?

Coordinating multiple AI coding agents (Claude Desktop, Cursor, Antigravity, local LLMs, or autonomous background loops) shouldn't require expensive per-seat SaaS, fragile webhook plumbing, or burying blockers in chat scrollback.

With **AK5 + Tailscale** you get a ticket-driven workflow on your own machine:

```
             ┌────────────────────────────────────────────────────────┐
             │       Tailscale Mesh VPN (Encrypted & Zero-Config)     │
             └────────────────────────────────────────────────────────┘
                       ▲                                    ▲
                       │ Mobile / Laptop                    │ Local or Remote
                       │ (Tailscale MagicDNS)               │ Agent Access
                       ▼                                    ▼
       ┌──────────────────────────────┐        ┌──────────────────────────────┐
       │   Human PM / Engineer        │        │   Personal AI Agent Fleet    │
       │   • File requirements        │        │   • Claude / Cursor / CLI    │
       │   • Prioritize backlog       │        │   • Capability discovery     │
       │   • Review & unblock tasks   │        │   • Autonomous execution     │
       └──────────────┬───────────────┘        └──────────────┬───────────────┘
                      │                                       │
                      └───────────────────┬───────────────────┘
                                          ▼
                      ┌───────────────────────────────────────┐
                      │         AK5 Embedded Gateway          │
                      │  • Port 8000: Web Dashboard + API     │
                      │  • SQLite WAL (Zero external DB)      │
                      │  • Native MCP Server + SSE Streams    │
                      └───────────────────────────────────────┘
```

* **Self-hosted:** `ak5 web` runs locally with SQLite WAL — no cloud lock-in, no external DB.
* **Access anywhere via Tailscale:** MagicDNS / `100.x` URLs without opening ports to the public internet.
* **Ticket-driven agents:** Agents claim work via MCP, branch subtasks, move columns, and report blockers with mentions.

---

## Quick Start (Zero-Install via `uvx`)

Requires Python 3.11+. Uses [`uvx`](https://docs.astral.sh/uv/) — no clone or permanent install needed.

### 1. Start Gateway + Embedded Web Dashboard (single port)

```bash
# API + Kanban UI on :8000 (no Node.js required after install)
uvx ak5 web
```

* **Web Dashboard:** `http://127.0.0.1:8000/`
* **REST API & Swagger:** `http://127.0.0.1:8000/docs`
* **SSE Event Stream:** `http://127.0.0.1:8000/api/v1/events/stream`
* **MCP SSE:** `http://127.0.0.1:8000/mcp/sse`
* **Tailscale:** if the Tailscale CLI is installed, MagicDNS + `100.x` URLs are printed automatically

API-only (no browser open):

```bash
uvx ak5 serve
```

Auth, Tailscale CORS, and bind options: see [Web Dashboard & Remote Access](#web-dashboard--remote-access-tailscale).

### 2. Terminal board & tickets (gateway must be running)

Keep `ak5 web` (or `ak5 serve`) running in another terminal, then:

```bash
uvx ak5 boards
uvx ak5 board proj-core-engine --watch
```

### 3. Multi-agent collaboration demo (gateway must be running)

```bash
uvx ak5 demo
```

Simulates a PM plus Orchestrator, Image Worker, and Code Reviewer agents — ticket breakdown, delegation, column moves, and completion in the terminal.

---

## Installation

```bash
# Using pip
pip install ak5

# Or via uv tool (recommended)
uv tool install ak5
```

Then:

```bash
ak5 --help
ak5 web          # preferred entrypoint
ak5 whoami
```

---

## Core Features

1. **Unified Actor Model:** Humans and AI agents share the same `Actor` entity (`user_pm`, `agent_code_reviewer`, …) with JWT auth and audit trails.
2. **Capability-Based Discovery & Delegation:** Find peers by capability tags (e.g. `image-resize`, `code-review`) or natural-language search, then spawn subtasks.
3. **Lexorank (Base36):** Stable ticket ordering with fractional ranks (no re-index collisions).
4. **Multi-Protocol Surface:** CLI (`ak5`), embedded Next.js Kanban dashboard (static bundle in the PyPI wheel), SSE, and MCP.
5. **Embedded Storage:** SQLite WAL + busy-timeout — zero external database admin.

---

## Human CLI Reference

Requires a running gateway (`ak5 web` or `ak5 serve`) unless noted.

### Board discovery & creation

```bash
ak5 boards
ak5 boards -q "engine"
ak5 board
ak5 board proj-harbor-eval --watch
ak5 create-board proj-mobile-app --name "Mobile App" --desc "iOS/Android client"
```

### Ticket lifecycle

```bash
ak5 ticket create \
  --title "Implement OAuth2 Flow" \
  --desc "Support Google and GitHub OAuth callbacks" \
  --priority high \
  --assign user_pm \
  --labels "auth,security" \
  --board proj-core-engine

ak5 ticket view TK-001
ak5 ticket move TK-001 "In Progress" --note "Started implementation"
ak5 move TK-001 "Done" --note "Merged"

ak5 ticket comment TK-001 "Code review approved (LGTM)"
ak5 comment TK-001 "Please attach benchmark logs"

ak5 ticket block TK-001 --reason "Waiting on cloud storage API keys" --mention user_pm
ak5 ticket update TK-001 --priority urgent --assign agent_code_reviewer
```

### Agents & delegation

```bash
ak5 agents --cap "image-resize"
ak5 agents --query "security audit buffer overflow"

ak5 delegate TK-001 \
  --to agent_image_worker \
  --title "WebP Compression Module" \
  --desc "Write 200x200 resize function with unit tests" \
  --priority high
```

### Identity

```bash
ak5 login \
  --id user_pm \
  --role "Product Manager" \
  --type human \
  --caps "planning,review"

ak5 whoami
```

---

## AI Agent & MCP Integration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "ak5": {
      "command": "uvx",
      "args": ["ak5-mcp"]
    }
  }
}
```

### Cursor / Antigravity / Windsurf

* **Command:** `uvx`
* **Args:** `ak5-mcp`

### MCP tools

| Tool Name | Parameters | Description |
|---|---|---|
| `ak5_list_boards` | *(none)* | List boards (id, name, description) |
| `ak5_list_available_agents` | `capability`, `search_query` | Find peer agents |
| `ak5_delegate_subtask` | `parent_ticket_id`, `target_agent_id`, `title`, `description`, `priority` | Spawn & assign a child ticket |
| `ak5_get_ticket_context` | `ticket_id` | Spec, subtasks, comments, execution context |
| `ak5_update_ticket_status` | `ticket_id`, `column_name`, `status_note`, `execution_context` | Move column / log artifacts |
| `ak5_report_block` | `ticket_id`, `blocking_reason`, `required_actor_id` | Mark blocked and notify |

---

## Web Dashboard & Remote Access (Tailscale)

The Kanban UI is **bundled into the Python wheel** and served by FastAPI on the **same port** as the API. End users do not need Node.js or npm.

### Launch

```bash
ak5 web
# or
uvx ak5 web

ak5 web --host 0.0.0.0 --port 8080 --no-browser
```

Example banner (Tailscale detected + auth enabled):

```text
╭────────────────── AK5 Kanban Web Dashboard ──────────────────╮
│ Local:              http://127.0.0.1:8000                    │
│ API Docs:           http://127.0.0.1:8000/docs               │
│ Tailscale Domain:   http://my-machine.tailfd161b.ts.net:8000 │
│ Tailscale IP:       http://100.119.228.9:8000                │
│ Bind:               http://0.0.0.0:8000                      │
│ Web Auth:           ENABLED (user: admin)                    │
│ Brute-force Shield: ACTIVE (5 / 5m, 10m lockout)             │
╰──────────────────────────────────────────────────────────────╯
```

### Tailscale

1. **MagicDNS:** e.g. `http://my-machine.ts.net:8000`
2. **CGNAT IPv4:** e.g. `http://100.x.y.z:8000`
3. **CORS:** limited to `localhost`, `127.0.0.1`, Tailscale `100.x.y.z`, and `*.ts.net` (not an open `*` wildcard)

### Optional password gate

When binding to `0.0.0.0` or exposing over Tailscale, enable the HTTP login gate.

**With a git checkout:**

```bash
cp .env.example .env
```

**With `uvx` / a global install** (no repo): create `.env` in the directory where you run `ak5 web`, or export:

```bash
export AK5_AUTH_USERNAME=admin
export AK5_AUTH_PASSWORD=your_super_secret_password
```

`.env` / env contents:

```env
AK5_AUTH_USERNAME=admin
AK5_AUTH_PASSWORD=your_super_secret_password
```

* **Zero-friction local use:** empty / unset `AK5_AUTH_PASSWORD` → auth disabled.
* **Brute-force shield:** 5 failed attempts / 5 minutes per IP → 10-minute lockout (HTTP 429 + `Retry-After`).
* **Session cookie:** HttpOnly, SameSite=Lax, 30-day **SHA-256 session digest** cookie named `ak5_auth` (not an encrypted payload).
* **Header UI:** when auth is on, the dashboard shows the signed-in user and Sign Out.

---

## Local Development & Testing

```bash
git clone https://github.com/fritzprix/ak5.git
cd ak5
uv sync
uv pip install -e backend

# Tests & lint
uv run pytest backend/tests
uv run ruff check backend

# Build embedded dashboard assets into backend/src/ak5/web_ui/
./scripts/build_web_ui.sh

# Single-port gateway + UI (same as end users)
uv run ak5 web --reload

# API only
uv run ak5 serve --reload
```

Hot-reload Next.js on `:3000` (dual-port, monorepo only):

```bash
# Terminal A — gateway
uv run ak5 serve --host 0.0.0.0 --port 8000 --reload

# Terminal B — Next.js (proxies /api/* to the gateway)
cd frontend && npm install && npm run dev
```

Or: `AK5_LEGACY_WEB=1 ./start-web.sh`

---

## Documentation & References

* **Architecture & operations:** [docs/MANUAL.md](./docs/MANUAL.md)
* **Agent skill guide:** [skills/ak5/SKILL.md](./skills/ak5/SKILL.md)
* **PyPI:** [https://pypi.org/project/ak5/](https://pypi.org/project/ak5/)

---

## License

This project is licensed under the **Apache License, Version 2.0**.  
See the [LICENSE](./LICENSE) file for the full license text.
