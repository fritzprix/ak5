# AK5 — Backend Gateway, MCP Server & CLI

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

## ⚡ Quick Start (Zero-Install via `uvx`)

Run AK5 immediately without cloning or installing dependencies using [`uvx`](https://docs.astral.sh/uv/):

### 1. Run 1-Minute Multi-Agent Collaboration Simulation
```bash
uvx ak5 demo
```

### 2. Start Backend Gateway & Embedded Web UI
```bash
uvx ak5 web
```
* **Web Dashboard:** `http://127.0.0.1:8000/`
* **REST API & Swagger Docs:** `http://127.0.0.1:8000/docs`
* **Real-time SSE Event Stream:** `http://127.0.0.1:8000/api/v1/events/stream`
* **Embedded MCP Server Endpoint:** `http://127.0.0.1:8000/mcp/sse`
* Prints Tailscale MagicDNS / CGNAT URLs when available

### 3. Terminal Kanban Board (TUI)
```bash
# List all project boards
uvx ak5 boards

# Inspect board in real time
uvx ak5 board proj-core-engine --watch
```

---

## 📦 Installation

```bash
# Via pip
pip install ak5

# Or via uv tool
uv tool install ak5
```

---

## 💻 Full Human CLI Reference

```bash
# Boards
ak5 boards
ak5 board [BOARD_ID] [--watch]
ak5 create-board <BOARD_ID> --name <NAME> [--desc <DESC>]

# Tickets
ak5 ticket create --title <TITLE> [--board <ID>] [--priority <PRIORITY>] [--assign <ACTOR_ID>]
ak5 ticket view <TICKET_ID>
ak5 ticket move <TICKET_ID> <TARGET_COLUMN> [--note <NOTE>]
ak5 ticket comment <TICKET_ID> <CONTENT>
ak5 ticket block <TICKET_ID> --reason <REASON> [--mention <ACTOR_ID>]
ak5 ticket update <TICKET_ID> [--priority <P>] [--assign <ACTOR>]

# Quick Shortcuts
ak5 move <TICKET_ID> <TARGET_COLUMN> [--note <NOTE>]
ak5 comment <TICKET_ID> <CONTENT>

# Agents & Delegation
ak5 agents --cap <TAG>
ak5 delegate <TICKET_ID> --to <ACTOR_ID> --title <TITLE>

# Identity
ak5 login --id <ACTOR_ID> --role <ROLE>
ak5 whoami
```

---

## 🌐 Web Dashboard & Remote Access (Tailscale)

```bash
# Launch single-port API + Kanban Web Dashboard
ak5 web

# Bind to all interfaces with custom port (headless)
ak5 web --host 0.0.0.0 --port 8080 --no-browser
```

* **Zero Node.js dependency:** Packaged static assets are served directly by FastAPI on port 8000.
* **Tailscale Auto-Detection:** Automatically displays MagicDNS (`*.ts.net`) and private CGNAT IP (`100.x.y.z`).
* **Authentication Gate:** Optional `.env` / environment variable setup (`AK5_AUTH_USERNAME`, `AK5_AUTH_PASSWORD`) with built-in brute-force rate-limiting (5 failed attempts / 5m → 10m lockout).


---

## 🤖 AI Agent & MCP Integration (Claude Desktop, Cursor, Antigravity)

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

## 📄 License

This project is licensed under the **Apache License, Version 2.0**.  
See the [LICENSE](../LICENSE) file for details.
