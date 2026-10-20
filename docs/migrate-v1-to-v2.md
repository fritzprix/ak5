# Migrating from AK5 1.x → 2.0

AK5 **2.0.0** is a breaking major: new workflow stages, identity, API version (`api_version=2`, `min_client=2.0.0`), and schema baseline. There is **no in-place auto-migration** of a 1.x SQLite file into the v2 schema (see `docs/spec/deployment.md` §1.2).

## Recommended paths

### A. Fresh local install (most users)

```bash
pip install -U 'ak5[server]'   # or: uv tool install 'ak5[server]'
ak5 up                         # new DB + me/triage/worker/gatekeeper profiles
```

Open `http://127.0.0.1:8000`. Old 1.x data under the OS app-data dir is left alone unless you point `AK5_DATA_DIR` at the same path — prefer a clean data dir for 2.0.

### B. Logical move with export / import

If you must keep tickets/boards from a 1.x (or early v2) database:

```bash
# On the source instance (while it still runs / can open the DB)
ak5 db export --out dump.jsonl

# Fresh 2.0 target (empty DB URL or new data dir)
ak5 db import dump.jsonl --to "$DATABASE_URL"   # or default local URL after ak5 up once
ak5 db upgrade
```

- Target DB must be empty.
- Event `seq` is preserved.
- Online cutover is not supported.

### C. Hosted / Postgres

Follow `docs/deploy/vercel-neon.md`. Set `AK5_JWT_SECRET`, prefer split `DATABASE_URL` + `AK5_DATABASE_ADMIN_URL`, run Alembic (`ak5 db upgrade` or auto-migrate on boot).

## What you must redo

| Area | Action |
|---|---|
| **CLI login** | Re-run `ak5 login` / use profiles from `ak5 up`. 1.x tokens and project `.ak5` sessions are not valid for API v2. |
| **Agent registration** | Register agents via OAuth device grant (`ak5 login --agent …`) or local profiles from `ak5 up`. Do not rely on open `/auth/identify` in production. |
| **Subscriptions / wake hooks** | Re-create with `ak5 subscribe …` against the daemon profile model. |
| **SDK / MCP clients** | Upgrade to `@ak5/sdk@2` / matching Python client; servers reject clients below `min_client` 2.0.0. |
| **Scripts using `ticket move` to advance work** | Use workflow verbs: `claim` → `submit` → `verdict` (and `route` from inbox). `ticket move` only allows same-stage or human-mapped transitions. |

## Workflow mental model (breaking)

| 1.x (approx.) | 2.0 |
|---|---|
| Free column moves / status fields | Stage machine enforced by server |
| Human/agent identify freely | Device grant + roles (`human` / `triage` / `worker` / `gatekeeper`) |
| Ticket “done” by move | `done` only after human approve on `human_review` |

Inbox creates land in `unclassified`; triage `route`s to a board `backlog`.

## Legacy CLI still present (temporary)

- `ak5 login --id … --type …` self-identify is marked `[legacy]` and will be removed later.
- `ak5 migrate-legacy` still moves an old on-disk `./ak5.db` into the OS app-data location — it does **not** rewrite 1.x rows into the v2 workflow schema.

## Smoke checklist after upgrade

- [ ] `ak5 --version` → 2.0.0
- [ ] `curl -s localhost:8000/meta` → `api_version` `"2"`, `min_client` `"2.0.0"`
- [ ] `ak5 whoami` with profile `me` (or after `ak5 login`)
- [ ] `ak5 demo` or create → route → claim → submit → verdict path on a board
- [ ] Web UI loads signed-in on loopback (`ak5 up`) or via password/OIDC (`ak5 serve`)
