# AK5 프로젝트 분석 보고서 (historical)

> **Historical snapshot only.** Written 2026-09-20 against an early v1 tree (`/home/fritzprix/my_works/ak5`).  
> Current AK5 is **v2.x**, **Apache-2.0**, Postgres-capable, workflow-v2, and PyPI-shipped (`ak5` on 2.0.x).  
> For up-to-date orientation use [README](../README.md), [MANUAL](MANUAL.md), and [docs/spec/](spec/).

The original report body below is kept for archaeology; do not treat version, license, or architecture bullets as current.

---

## 1. 프로젝트 개요 (2026-09 snapshot)

**AK5 (Agent K5)** — 인간(PM/개발자)과 자율 AI 에이전트가 단일 칸반(K5) 인터페이스 위에서 실시간으로 협업하고 작업을 위임(Delegation)·추적하는 **Agent-Orchestrated Kanban 시스템**으로 기술되었습니다.

| 항목 | 2026-09 snapshot (stale) | Current (2.0.x) |
|---|---|---|
| **버전** | 1.0.0 | 2.0.2+ on `dev/2.0.x` |
| **라이선스** | MIT | Apache-2.0 |
| **DB** | SQLite only | SQLite + PostgreSQL (RLS) |
| **로컬 UX** | `ak5 serve` / `ak5 web` | `ak5 up` (recommended local) |
| **배포** | — | Hosted: Vercel + Neon (`AK5_DEPLOY_PROFILE=hosted`) |

For the full 2026-09 text, see git history of this file before the historical header was added.