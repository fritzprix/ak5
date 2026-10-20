# Changelog

All notable changes to AK5 are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
