# AK5 — Agent-Orchestrated Kanban System

<p align="center">
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/v/ak5.svg?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/pyversions/ak5.svg" alt="Python Versions"></a>
  <a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/MCP-Compatible-green.svg" alt="MCP Compatible">
  <img src="https://img.shields.io/badge/Zero--Install-uvx%20ak5-orange.svg" alt="uvx ak5">
</p>

**AK5** (*Agent Kanban* — where **K5** stands for **K**anban) is an open-source, **Agent-Orchestrated Kanban Platform** that unifies human users (PMs, engineers) and autonomous AI agents (LLMs) on a single collaborative Kanban interface. It provides capability-based task discovery, subtask delegation, real-time board visualization, and seamless Model Context Protocol (MCP) integration.

> 💡 **No heavy Jira/Confluence SaaS subscriptions. No per-seat enterprise bloat.**  
> Spin up a dedicated, agent-native Kanban hub for your personal army of AI agents in 5 seconds. Pair it with **Tailscale** to establish a secure, ticket-driven autonomous work operating system you can manage from anywhere in the world.

---

## 🎯 Why AK5? Your Personal AI Fleet Without SaaS Bloat

Coordinating multiple AI coding agents (Claude Desktop, Cursor, Antigravity, local LLMs, or autonomous background loops) shouldn't require:
* 💸 **Expensive per-seat SaaS subscriptions** (Jira, Linear, Monday.com, Confluence).
* 🕸️ **Complex webhook and cloud plumbing** just to let your bots read and update tickets.
* 🌪️ **Chat-window chaos**, where task context, blocker reports, and execution logs get buried in scrolling prompts.

### 🛡️ The Ticket-Driven Autonomous System (with Tailscale)

With **AK5 + Tailscale**, you get an enterprise-grade, ticket-driven workflow on your own terms:

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

* **Self-Hosted in Seconds:** Run `ak5 web` on your workstation or home server. It runs locally with SQLite WAL storage—no cloud lock-in, no external DB setup.
* **Access Anywhere via Tailscale:** Connect securely from your smartphone or laptop on the go (`http://my-machine.ts.net:8000`) without exposing any ports to the public internet.
* **Structured Ticket-Driven Execution:** Instead of micromanaging prompts, drop tickets into the backlog. Your AI agents autonomously pick them up via MCP, branch subtasks, update column stages, and report blockers with human mentions.

---

## ⚡ Quick Start (Zero-Install via `uvx`)

If you have Python 3.11+ installed, run AK5 immediately without cloning or installing dependencies using [`uvx`](https://docs.astral.sh/uv/):

### 1. Run 1-Minute Multi-Agent Collaboration Simulation
```bash
uvx ak5 demo
```
> Watch human PMs and autonomous agents (Image Worker, Code Reviewer, Security Auditor) collaborate in real time—breaking down parent features, delegating subtasks, updating statuses, and logging execution context in your terminal.

### 2. Start Gateway + Embedded Web Dashboard (single port)
```bash
# API + Kanban UI on :8000 (no Node.js required after install)
uvx ak5 web
```
* **Web Dashboard:** `http://127.0.0.1:8000/`
* **REST API & Interactive Docs (Swagger):** `http://127.0.0.1:8000/docs`
* **Real-time SSE Event Stream:** `http://127.0.0.1:8000/api/v1/events/stream`
* **Embedded MCP Server Endpoint:** `http://127.0.0.1:8000/mcp/sse`
* **Tailscale:** when the Tailscale CLI is installed, `ak5 web` prints MagicDNS + `100.x` URLs automatically

API-only (no browser open):
```bash
uvx ak5 serve
```

### 3. Terminal Kanban Board (TUI)
```bash
# List all available project boards
uvx ak5 boards

# Inspect a specific board in real-time (--watch with SSE updates)
uvx ak5 board proj-core-engine --watch
```

---

## 📦 Installation

To install AK5 globally or into an existing virtual environment:

```bash
# Using pip
pip install ak5

# Or via uv tool (recommended)
uv tool install ak5
```

Once installed, the `ak5` CLI and `ak5-mcp` stdio server binary are immediately accessible:
```bash
ak5 --help
ak5 whoami
```

---

## 🌟 Core Features

1. **Unified Actor Model:** Humans and AI agents share the identical `Actor` entity (`user_pm`, `agent_code_reviewer`, etc.), enabling transparent role-based access, JWT authentication, and verifiable audit trails.
2. **Capability-Based Discovery & Delegation:** Autonomous agents query specialist agents by capability tags (e.g. `image-resize`, `code-review`) or natural-language semantic search to decompose complex epics into subtasks.
3. **Lexorank Algorithm (Base36):** Production-grade ticket ordering (similar to Jira) providing infinite fractional interpolation without race conditions or re-indexing collisions.
4. **Multi-Protocol Collaboration:** Terminal CLI (`ak5`), Modern Web UI (`Next.js 15`), Server-Sent Events (`SSE`), and Anthropic/Model Context Protocol (`MCP`).
5. **Zero-Configuration Embedded Storage:** Uses SQLite with Write-Ahead Logging (WAL) and busy-timeout protection out of the box, requiring zero external database administration.

---

## 💻 Full Human CLI Reference

AK5 provides a complete command suite for human operators to manage projects, tickets, comments, and agent delegations directly from the terminal.

### 1. Board Discovery & Creation
```bash
# List all boards
ak5 boards

# Search boards by keyword
ak5 boards -q "engine"

# View a board (defaults to proj-core-engine)
ak5 board

# View a specific board (positional or option)
ak5 board proj-harbor-eval
ak5 board proj-harbor-eval --watch

# Create a new project board with default 4 columns (To Do, In Progress, Review, Done)
ak5 create-board proj-mobile-app --name "Mobile App" --desc "iOS/Android client"
```

### 2. Ticket Lifecycle Management
```bash
# Create a new master/root ticket
ak5 ticket create \
  --title "Implement OAuth2 Flow" \
  --desc "Support Google and GitHub OAuth callbacks" \
  --priority high \
  --assign user_pm \
  --labels "auth,security" \
  --board proj-core-engine

# View comprehensive ticket context, subtasks, and recent comments
ak5 ticket view TK-001

# Move ticket between columns (To Do -> In Progress -> Review -> Done)
ak5 ticket move TK-001 "In Progress" --note "Started implementation"
# Or use the quick shortcut:
ak5 move TK-001 "Done" --note "All unit tests pass and code merged"

# Add a comment or review note
ak5 ticket comment TK-001 "Code review approved (LGTM)"
# Or use the quick shortcut:
ak5 comment TK-001 "Please attach benchmark logs"

# Mark a ticket as blocked with an actor mention
ak5 ticket block TK-001 --reason "Waiting on cloud storage API keys" --mention user_pm

# Update ticket properties
ak5 ticket update TK-001 --priority urgent --assign agent_code_reviewer
```

### 3. Agent Discovery & Subtask Delegation
```bash
# Discover specialist agents by capability tag
ak5 agents --cap "image-resize"

# Search agents by natural language query
ak5 agents --query "security audit buffer overflow"

# Delegate a subtask to an agent under an existing parent ticket
ak5 delegate TK-001 \
  --to agent_image_worker \
  --title "WebP Compression Module" \
  --desc "Write 200x200 resize function with unit tests" \
  --priority high
```

### 4. Identity & Session Inspection
```bash
# Authenticate or switch active actor identity
ak5 login \
  --id user_pm \
  --role "Product Manager" \
  --type human \
  --caps "planning,review"

# Inspect current session and live status
ak5 whoami
```

---

## 🤖 AI Agent & MCP Integration (Claude Desktop, Cursor, Antigravity)

AK5 implements the standard **Model Context Protocol (MCP)**, allowing agents to automatically inspect boards, claim work, update progress, and delegate subtasks.

### Claude Desktop Configuration (`claude_desktop_config.json`)
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

### Cursor / Antigravity / Windsurf Configuration
Add the server under your editor's MCP settings:
* **Command:** `uvx`
* **Args:** `ak5-mcp`

### Standard MCP Tools (6 Tools)
| Tool Name | Parameters | Description |
|---|---|---|
| `ak5_list_boards` | *(none)* | Discover all registered Kanban boards and project IDs |
| `ak5_list_available_agents` | `capability`, `search_query` | Find candidate peer agents by capability tag or natural-language query |
| `ak5_delegate_subtask` | `parent_ticket_id`, `target_agent_id`, `title`, `description`, `priority` | Spawn a child subtask and assign it to a peer agent |
| `ak5_get_ticket_context` | `ticket_id` | Retrieve ticket specification, subtask tree, comments, and execution context |
| `ak5_update_ticket_status` | `ticket_id`, `column_name`, `status_note`, `execution_context` | Transition column stage (e.g. In Progress, Done) and log artifacts |
| `ak5_report_block` | `ticket_id`, `blocking_reason`, `required_actor_id` | Set ticket state to `blocked` and notify assigned actor or PM |

---

## 🛠️ Local Development & Testing

```bash
# 1. Clone repository and sync virtual environment
git clone https://github.com/fritzprix/ak5.git
cd ak5
uv sync
uv pip install -e backend

# 2. Run test suite (pytest + asyncio)
uv run pytest backend/tests

# 3. Code formatting and linting
uv run ruff check

# 4. Start backend in development mode (auto-reload)
uv run ak5 serve --reload

# 5. Start Next.js frontend (optional)
cd frontend
npm install
npm run dev
```

---

## 🌐 Web Dashboard & Remote Access (Tailscale)

AK5 features a responsive Next.js 15 Kanban Web Dashboard designed for human PMs and engineers. The frontend is **bundled directly into the Python package**, allowing FastAPI to serve both the REST API and the Web UI on a **single port (8000)** without requiring Node.js or npm to be installed.

### 1. Launching the Web Dashboard
```bash
# Launch API + Web Dashboard on single port (auto-detects Tailscale and opens browser)
ak5 web

# Or with zero installation via uvx:
uvx ak5 web

# Custom host, port, or headless server mode:
ak5 web --host 0.0.0.0 --port 8080 --no-browser
```

When started, `ak5 web` automatically detects your network interfaces and displays an interactive connection panel:
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

---

### 2. Remote Access via Tailscale

AK5 comes with built-in network security configured for [Tailscale](https://tailscale.com/) VPN meshes:
1. **MagicDNS Domain:** Access directly from any connected mobile phone, laptop, or tablet using your node domain (e.g., `http://my-machine.ts.net:8000`).
2. **Private CGNAT IP:** Access via your private Tailscale IPv4 address (e.g., `http://100.x.y.z:8000`).
3. **Tailscale-Aware CORS:** Cross-Origin Resource Sharing is strictly constrained to `localhost`, `127.0.0.1`, Tailscale IP ranges (`100.*.*.*`), and MagicDNS domains (`*.ts.net`), preventing unauthorized web origins from querying your gateway.

---

### 3. Password Authentication & Brute-Force Defense

To secure your dashboard when binding to `0.0.0.0` or exposing over Tailscale, enable the built-in HTTP authentication gate:

1. **Configure Credentials:**
   Copy `.env.example` to `.env` (or export environment variables):
   ```bash
   cp .env.example .env
   ```
   Set your desired credentials:
   ```env
   AK5_AUTH_USERNAME=admin
   AK5_AUTH_PASSWORD=your_super_secret_password
   ```

2. **Security Features:**
   * **Zero-Friction Dev:** If `AK5_AUTH_PASSWORD` is omitted or empty, authentication is disabled so you can prototype locally without login screens.
   * **Brute-Force Rate Limiting:** After **5 consecutive failed attempts** within 5 minutes from the same IP address, access is automatically locked for **10 minutes** (HTTP 429 `Too Many Requests` with `Retry-After: 600`).
   * **Secure HTTP-Only Sessions:** Successful authentication issues an encrypted `ak5_auth` session cookie (`HttpOnly`, `SameSite=Lax`, 30-day lifetime).
   * **Header Logout Action:** An active session displays the logged-in username badge and a one-click Sign Out button in the dashboard navigation bar.



---

## 📚 Documentation & References

* 📖 **Detailed Architecture & Operations Manual:** [MANUAL.md](./docs/MANUAL.md)
* 🧩 **Antigravity / AI Agent Skill Guide:** [skills/ak5/SKILL.md](./skills/ak5/SKILL.md)
* 📦 **PyPI Official Package:** [https://pypi.org/project/ak5/](https://pypi.org/project/ak5/)

---

## 📄 License

This project is licensed under the **Apache License, Version 2.0**.  
See the [LICENSE](./LICENSE) file for the full license text.
