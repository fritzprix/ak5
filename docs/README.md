# AK5 Documentation

## Primary

- **[User & Operator Manual](./MANUAL.md)** — v2 workflow, install, web UI, CLI, client daemon, deployment, troubleshooting (한국어)
- **Specs (source of truth):** [workflow](./spec/workflow.md) · [events](./spec/events.md) · [cli](./spec/cli.md) · [client](./spec/client.md) · [identity](./spec/identity.md) · [deployment (draft)](./spec/deployment.md)
- **Development pipeline:** [pipeline](./pipeline.md) — issue labels, worker/reviewer roles, review checklist
- **Agent skill:** [skills/ak5/SKILL.md](../skills/ak5/SKILL.md)

## Postmortems & decisions (v1 background)

These predate v2: server-side exec hooks were replaced by client-daemon subscribers (client.md §4).


- [Server-Side Subscribe Architecture (2026-09-27)](./postmortem-20260927-subscribe-architecture.md)
- [Agent Watch Misuse & Fail-Fast Guard (2026-09-27)](./postmortem-20260927-subscribe-watch-misuse.md)
- [`--exec` must wake the harness (2026-09-27)](./postmortem-20260927-subscribe-exec-wake.md)
- [Architecture Analysis Report](./analysis-report.md)
