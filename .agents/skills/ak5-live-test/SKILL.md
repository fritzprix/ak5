---
name: ak5-live-test
description: >-
  Spin up a live, isolated AK5 server instance and execute end-to-end functional and
  integration verification tests. Use after introducing new backend features, CLI updates,
  auth/security changes, or SDK modifications to verify real server behavior, API endpoints,
  CLI session flows, and security gates without polluting project state.
---

# AK5 Live Server Integration & E2E Test Skill

Use this skill to spin up an ephemeral, sandboxed **AK5** gateway server and execute end-to-end integration tests across the REST API, security gates, CLI commands, and TypeScript SDK.

---

## 1. When to Use

* **After modifying backend routes, models, or schemas**: Verifies that live HTTP serialization, validation, and database operations execute cleanly.
* **After updating security / auth gates**: Verifies JWT secret generation, file permissions (`0o600`), `AK5_IDENTIFY_SECRET` enforcement, and web-auth cookie bypass.
* **After updating CLI or MCP commands**: Ensures real CLI subcommands (`ak5 login`, etc.) successfully communicate with the running server.
* **Before releases**: Supplements unit tests (`pytest`) with real multi-process HTTP and socket verification.

---

## 2. Quick Start

Run the automated live test suite via `uv`:

```bash
# Run the complete live verification matrix (Security, REST Core, TypeScript SDK)
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py

# Run only specific test suites
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --mode security
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --mode core
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --mode sdk

# Verbose output with server logs
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --verbose

# Specify a fixed port or retain the sandbox for debugging
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --port 8055 --keep-artifacts
```

---

## 3. Test Suites & Verification Matrix

The test runner tests the system across three distinct phases in clean sandboxes:

| Suite | Focus Area | Scenarios Verified |
| :--- | :--- | :--- |
| **Suite 1: Security & Identity Gates** | Protected Gateway (`AK5_IDENTIFY_SECRET` + `AK5_AUTH_PASSWORD`) | • Unauthenticated `/auth/identify` rejection (`401`)<br>• Invalid secret rejection (`401`)<br>• Secret in header (`X-AK5-Identify-Secret`) verification & JWT issue (`200`)<br>• Secret in JSON request body verification (`200`)<br>• JWT secret file auto-creation with `0o600` permissions (`-rw-------`)<br>• Dashboard web login (`/api/auth/login`) & cookie bypass (`ak5_auth`)<br>• CLI `ak5 login` automatic secret propagation |
| **Suite 2: Core REST Operations** | Standard Gateway (Open Identity Gate) | • Unauthenticated identify acceptance in open mode (`200`)<br>• `GET /boards` retrieval of seeded boards<br>• `POST /boards` creation with default column generation<br>• `POST /tickets` ticket creation within columns<br>• `PATCH /tickets/{id}/move` status & column transitions<br>• `POST /tickets/{id}/comments` ticket commentary |
| **Suite 3: TypeScript SDK** | Node.js `@ak5/sdk` Client | • Client creation with explicit `identifySecret` option<br>• Client creation with `process.env.AK5_IDENTIFY_SECRET`<br>• Missing secret rejection against protected gateway |

---

## 4. Sandbox & Safety Guarantees

The test runner operates with zero side-effects on the host workspace:

1. **Ephemeral Database**: Sets `AK5_DATA_DIR` to a sandbox and opens that dir's `ak5.db` (aligned `DATABASE_URL`). Never touches the host global store.
2. **Isolated JWT Key**: Uses `AK5_JWT_SECRET_FILE=/tmp/ak5_live_test_.../jwt_secret`. Never overwrites `.ak5/jwt_secret`.
3. **Sandboxed CLI Environment**: Sets `HOME` to the temporary directory so CLI credentials (`.ak5/sessions/*.json`, `.ak5/identity/*.json`) are created in the sandbox and removed immediately after the test.
4. **Dynamic Port Allocation**: Binds to a randomly assigned available OS port (`127.0.0.1:0`), preventing conflicts with currently running development servers.
5. **Guaranteed Cleanup**: Uses Python `try ... finally` blocks to terminate backend subprocesses and prune temporary sandbox directories.

---

## 5. Adding New Feature Verifications

To add tests for a newly introduced endpoint or workflow:

1. Open [`.agents/skills/ak5-live-test/scripts/run_live_test.py`](file:///home/fritzprix/my_works/ak5/.agents/skills/ak5-live-test/scripts/run_live_test.py).
2. Add a new test method to `LiveTestRunner` (e.g., `test_new_feature(self)`).
3. Use `self.spawn_server(...)` to start the backend with desired environment variables.
4. Issue requests using `httpx` or subprocess CLI commands.
5. Use `self.report_pass("Description")` or `self.report_fail("Description", "Reason")`.
6. Include the new method in `run_all(self, mode)`.
