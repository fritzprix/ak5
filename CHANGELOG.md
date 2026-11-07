# Changelog

All notable changes to AK5 are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `ak5 onboard`: read-only setup check (server, daemon, profiles, skill) that prints the next steps for an agent, including what to ask the human. URLs that are not an AK5 API report as not ready, and skill copies from another CLI version are flagged
- `ak5 skill show|install`: the agent skill now ships in the wheel; `install` copies it into `.agents/`, `.claude/`, or `.cursor/` skill directories (`--global`, `--dir`, `--force`)
- README: short copy-paste prompt that onboards an agent through `uvx ak5 onboard`
- `AK5_VERCEL_BLOB_ACCESS=private`: the `vercel-blob` backend can use a private Vercel Blob store. Uploads declare the access mode, and reads and existence checks send the token, so attachments are not readable by URL alone. Default stays `public` (#179)
- `scripts/smoke_hosted.py <URL>`: deployment smoke check (`/readyz`, `/meta` API version, dashboard HTML, one `/_next` chunk served as JavaScript); the wheel smoke gate also rejects duplicate paths. Docs cover running local (`ak5 up`) and Vercel from one checkout, and `ak5 serve` against Neon from `.env` (#179)
- `scripts/vercel_deploy.py`: one-command Vercel deploy from a Python-only stage (verified wheel with dashboard assets, no `.git`/`package.json`), followed by the hosted smoke check (#179)

### Changed
- Agent skill describes the adversarial review contract (separation of duties, evidence per criterion, independent gatekeeper verification)
- `ak5 up` always runs the local runtime (SQLite and attachments under the data dir, `fs` blobs, single-instance `inproc` notify). Hosted values from `.env` or the shell (`DATABASE_URL`, admin URL, client-IP / proxy headers, …) are ignored and listed at startup; use `ak5 serve` to run with them (#179)

### Fixed
- Windows: the client daemon now runs over a named pipe instead of a Unix socket, so `ak5 up` profiles and every daemon-backed CLI command work (previously exit 9 `DAEMON_UNAVAILABLE`). The pipe allows only the current user, rejects network clients, refuses a name already taken by another process, and the CLI only talks to the pipe served by the recorded daemon pid. Daemon liveness checks no longer send Ctrl+C to the target on Windows, and stopping `serve --daemon` no longer calls `os.killpg` there (#182)
- Postgres: Neon-style `DATABASE_URL`s (`?sslmode=require&channel_binding=require`) no longer crash startup with `connect() got an unexpected keyword argument`. `sslmode` maps to asyncpg's `ssl`, and `channel_binding` / `sslrootcert` are dropped, for both the server engines and `ak5 db` (#179)
- Postgres: web sign-in no longer fails to store its session. `user_sessions.refresh_token_hash` is widened to 256 characters (migration `0008_widen_session_token_hash`), since web sessions store a 129-character `cookie:csrf` digest pair (#179)

### Removed
- npm publishing: `release-npm.yml`, the `npx ak5` launcher (`frontend/bin/cli.js`), and npm publish config. `frontend` and `packages/sdk` are now `private`; AK5 ships only via PyPI (#180)

## [2.0.0] — 2026-10-20

Major release: agent-orchestrated workflow v2, identity rewrite, dual-DB (SQLite + Postgres), hosted deploy path, and zero-config local launcher.

**Upgrade note:** treat this as a **new major**. Prefer a fresh local DB via `ak5 up`, or move data with `ak5 db export` / `ak5 db import`. See [docs/migrate-v1-to-v2.md](docs/migrate-v1-to-v2.md).

### Added
- Workflow v2 state machine: `unclassified` → `backlog` → `in_progress` → `agent_review` → `human_review` → `done` (plus postmortem path)
- Role verbs: `ak5 next`, `ticket claim|submit|route|verdict|bounce|reroute|…` with claim leases
- Identity: OAuth device grant, OIDC, local-owner password, web session + CSRF
- Client daemon + profiles (`~/.ak5/profiles.toml`, credentials)
- `ak5 up` — zero-config loopback launcher (UI + daemon + default profiles + auto-login)
- Postgres dialect + RLS; Alembic as schema path; `ak5 db upgrade|export|import`
- Hosted deploy entry (Vercel + Neon); backup/restore runbook + recovery rehearsal
- TypeScript SDK `@ak5/sdk` targeting API v2 (`min_client` 2.0.0)
- Pipeline runner scripts for issue→PR flow on `feat/workflow-v2` / `dev/2.0.x`

### Changed
- API negotiation: `GET /meta` reports `api_version: "2"`, `min_client: "2.0.0"`
- Board columns are stage-backed; free-form column moves no longer advance stage
- Agent registration is device-grant approved (no open self-registration)
- Default docs path: `ak5 up` for local; `ak5 serve` for password/OIDC/Tailscale

### Deprecated
- Legacy `ak5 login --id … --type …` self-identify (still present as `[legacy]`; removal tracked separately)
- Legacy project-local session files in favor of daemon profiles

### Removed / breaking
- v1 column-status workflow assumptions (`open` / free `ticket move` as progression)
- Unauthenticated identify as the primary local agent path (loopback `ak5 up` uses provisioned profiles + optional `AK5_DEV_INSECURE_IDENTIFY`)
- Expectation that an existing 1.x SQLite file auto-upgrades in place to v2 (use export/import or fresh start)

### Security
- Hosted startup refuses missing `AK5_JWT_SECRET`
- Loopback-only local password / auto-login; `CLIENT_IP_HEADER` spoofing hardened
- CSRF on mutating web session routes; device approval requires signed-in session

## [1.0.26] — 2026-10

Prior 1.x line on `master` / `dev/1.0.x`. See git tags `v1.0.*` for patch history.
