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

* **After modifying backend routes, models, or schemas**: verifies live HTTP serialization, validation, and database operations.
* **After changing identity / auth**: local-owner login, web session → JWT exchange (CSRF), OAuth device grant for users and agents, JWT secret file permissions (`0600`), self-identify disabled by default.
* **After changing the CLI, client daemon, or workflow verbs**: drives the real `ak5` CLI through the daemon across four profiles (human, triage, worker, gatekeeper) from inbox to `done`.
* **Before releases**: supplements unit tests (`pytest`) and `backend/tests/regression` with real multi-process HTTP, UDS, and subprocess verification.

---

## 2. Quick Start

```bash
# Full matrix (identity, workflow, SDK)
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py

# One suite
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --mode identity
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --mode workflow
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --mode sdk   # needs: cd packages/sdk && npm run build

# Server logs on the terminal, fixed port, keep the sandbox for debugging
uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py --verbose --port 8055 --keep-artifacts
```

`--mode security` and `--mode core` still work as aliases of `identity` and `workflow`.

---

## 3. Test Suites & Verification Matrix

| Suite | Focus | Scenarios |
| :--- | :--- | :--- |
| **1. Identity** | identity.md §2, §3, §8 | • `GET /meta` reports `api_version` 2<br>• `POST /auth/identify` refused without `AK5_DEV_INSECURE_IDENTIFY`<br>• Loopback local-owner login: wrong password `401`, correct password → session + CSRF<br>• `/auth/session/token` rejects a bad CSRF header (`403`) and mints a user JWT<br>• Device grant (user) → access token; device grant (agent) → opaque `ak5_agt_…` token that authorizes the API<br>• JWT secret file created with `0600` |
| **2. Workflow** | workflow.md §3, client.md §3–4 | • `ak5 login` (real CLI, code approved over HTTP) for `me`, `triage-1`, `worker-1`, `gk-1`<br>• `whoami` resolves the token through the daemon<br>• `board create` → T1 `ticket create` (inbox) → triage `next --inbox` / `claim` / `route`<br>• `events wait --events WORK_AVAILABLE` sees the routed ticket<br>• worker `next` / `claim` / `heartbeat` / `submit`<br>• gatekeeper `claim` / `verdict --approve --findings`<br>• T15 by an agent exits 4; human `verdict --approve` → `done`<br>• `next --wait 1` with an empty board exits 6 |
| **3. SDK** | `@ak5/sdk` | • Client with a device-grant agent token: `ensureCompatible()`, `listBoards()`<br>• An invalid agent token is rejected |

---

## 4. Sandbox & Safety Guarantees

The runner has no side effects on the host workspace:

1. **Ephemeral database**: `AK5_DATA_DIR` and `DATABASE_URL` point into `/tmp/ak5lt_*`. The host store is never opened.
2. **Isolated JWT key**: `AK5_JWT_SECRET_FILE` lives in the sandbox.
3. **Sandboxed client**: `HOME`, `AK5_HOME`, and `XDG_RUNTIME_DIR` are sandbox paths, so profiles, credentials, claim tokens, and the daemon socket never touch `~/.ak5`. The short `/tmp/ak5lt_` prefix keeps the socket path under the AF_UNIX limit.
4. **Clean environment**: `AK5_TOKEN`, `AK5_PROFILE`, `AK5_DEV_INSECURE_IDENTIFY`, `AK5_OIDC_PROVIDERS`, and related variables are removed from child processes.
5. **Dynamic port**: a free `127.0.0.1` port unless `--port` is given.
6. **Cleanup**: servers and the daemon are stopped in `finally` blocks and the sandbox is removed (unless `--keep-artifacts`).

---

## 5. Adding New Feature Verifications

1. Open `.agents/skills/ak5-live-test/scripts/run_live_test.py`.
2. Add a `test_<feature>(self)` method to `LiveTestRunner`, or extend an existing suite.
3. Start the server with `self.spawn_server(extra_env)`; it already sets the local-owner password.
4. Use the helpers: `self.ak5(*args, profile=…)` runs the CLI, `self.cli_login(...)` runs `ak5 login` and approves it, `self.device_grant_token(principal=…)` mints a token over HTTP, and `self.owner_client()` returns a logged-in browser-like client.
5. Record results with `self.check(name, ok, reason)`.
6. Wire the method into `run_all()` and the `--mode` choices.
