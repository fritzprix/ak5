# v2 릴리스 체크리스트 (발췌)

정본 조율은 [#36](https://github.com/fritzprix/ak5/issues/36).  
에픽 [#22](https://github.com/fritzprix/ak5/issues/22) → [`dev/2.0.x`](https://github.com/fritzprix/ak5/tree/dev/2.0.x) → `master` @ **v2.0.0**.

## 사전 게이트

- [x] [#174](https://github.com/fritzprix/ak5/pull/174) launch harden
- [x] [#177](https://github.com/fritzprix/ak5/pull/177) `ak5 up`
- [x] [#178](https://github.com/fritzprix/ak5/pull/178) squash → `dev/2.0.x`

## 릴리스 (#36)

- [x] 대상 브랜치 `dev/2.0.x` · 범위 full v2 stack
- [x] 버전 `2.0.0` · [CHANGELOG](../CHANGELOG.md) · [migrate guide](migrate-v1-to-v2.md) · README 공지
- [x] Security review — **APPROVE WITH NITS** (demo identify fallback; Dockerfile bind docs)
- [x] Regression gate (#29) covered by CI on `dev/2.0.x` / `master`
- [x] Tag [`v2.0.0`](https://github.com/fritzprix/ak5/releases/tag/v2.0.0) · **PyPI** published ([run](https://github.com/fritzprix/ak5/actions/runs/39443078064))
- [ ] **npm** (`@ak5/sdk` / web) — blocked: `NPM_TOKEN` missing (`ENEEDAUTH` on [run](https://github.com/fritzprix/ak5/actions/runs/39443216114)); re-run `release-npm.yml` after secret is set

## 복구 리허설 (#119 / operations.md §5)

- [ ] `uv run --directory backend --extra server python ../scripts/recovery_rehearsal.py` → `RECOVERY_REHEARSAL_OK`
- [ ] Runbook [`docs/ops/backup-restore.md`](ops/backup-restore.md) staging pass
- [ ] Daemon `CURSOR_EXPIRED` re-sync smoke
