# AK5 (Agent K5) — Agent-Orchestrated Kanban System

<p align="center">
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/v/ak5.svg?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/pyversions/ak5.svg" alt="Python Versions"></a>
  <a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/MCP-Compatible-green.svg" alt="MCP Compatible">
  <img src="https://img.shields.io/badge/Zero--Install-uvx%20ak5-orange.svg" alt="uvx ak5">
</p>

**AK5** is an open-source, **Agent-Orchestrated Kanban Platform** that unifies human users (PMs, engineers) and autonomous AI agents (LLMs) on a single collaborative Kanban interface. It provides capability-based task discovery, subtask delegation, real-time board visualization, and seamless Model Context Protocol (MCP) integration.

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

The Kanban Web Dashboard is **bundled into the Python package** and served by FastAPI on the same port as the API (no separate Node process for end users).

### One-command (`ak5 web` / `uvx ak5 web`)
```bash
# Optional: enable login gate for Tailscale / LAN exposure
cp .env.example .env
# Set AK5_AUTH_USERNAME / AK5_AUTH_PASSWORD

uvx ak5 web --host 0.0.0.0 --port 8000
```

`ak5 web` will:
* Serve REST + SSE + MCP + static UI on **one port**
* Auto-detect **Tailscale IPv4** and **MagicDNS** (`*.ts.net`) when `tailscale` is on PATH
* Open the local browser (disable with `--no-browser`)
* Enforce optional web auth when `AK5_AUTH_PASSWORD` is set

### Security & Authentication Features
* **Zero-Friction Local Dev:** If `AK5_AUTH_PASSWORD` is omitted or empty, authentication is disabled.
* **Brute-Force Rate Limiting:** Max 5 failed attempts / 5 minutes → 10-minute IP lockout (HTTP 429 + `Retry-After`).
* **Session Cookie:** HttpOnly, SameSite=Lax, 30-day SHA-256 session cookie (`ak5_auth`).
* **Tailscale-aware CORS:** Allows `localhost`, `100.x.y.z`, and `*.ts.net` origins only (not an open wildcard).

Monorepo hot-reload (Next.js on :3000) remains available via `AK5_LEGACY_WEB=1 ./start-web.sh`.


---

## 📚 Documentation & References

* 📖 **Detailed Architecture & Operations Manual:** [MANUAL.md](./docs/MANUAL.md)
* 🧩 **Antigravity / AI Agent Skill Guide:** [skills/ak5/SKILL.md](./skills/ak5/SKILL.md)
* 📦 **PyPI Official Package:** [https://pypi.org/project/ak5/](https://pypi.org/project/ak5/)

---

## 📄 License

This project is licensed under the **Apache License, Version 2.0**.  
See the [LICENSE](./LICENSE) file for the full license text.
