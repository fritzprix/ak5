# v2 릴리스 체크리스트 (발췌)

정본 조율은 [#36](https://github.com/fritzprix/ak5/issues/36) (사람/오케스트레이터).  
에픽 [#22](https://github.com/fritzprix/ak5/issues/22): Wave 7·8·pipeline runner + `ak5 up`는 `feat/workflow-v2`에 착지 → [`dev/2.0.x`](https://github.com/fritzprix/ak5/tree/dev/2.0.x).

## 사전 게이트

- [x] [#174](https://github.com/fritzprix/ak5/pull/174) launch harden 머지
- [x] [#177](https://github.com/fritzprix/ak5/pull/177) zero-config `ak5 up` 머지 → `feat/workflow-v2`
- [x] [#178](https://github.com/fritzprix/ak5/pull/178) squash `feat/workflow-v2` → `dev/2.0.x`

## 릴리스 (#36)

- [x] 대상 브랜치: [`dev/2.0.x`](https://github.com/fritzprix/ak5/tree/dev/2.0.x) (from `master` @ v1.0.26)
- [x] 범위: **2.0.0 = full v2 stack** (identity + hosted/Postgres 포함)
- [x] 버전 `2.0.0` 정렬 (`backend` / `frontend` / SDK) + `MIN_CLIENT_VERSION=2.0.0`
- [x] [CHANGELOG.md](../CHANGELOG.md) · [v1→v2 마이그레이션 가이드](migrate-v1-to-v2.md) · README 공지
- [ ] 보안 리뷰 · 회귀 게이트(#29) 통과
- [ ] `dev/2.0.x` → `master` 후 `python3 scripts/release.py major --push` (또는 동등 태그) · PyPI / npm · GitHub Release

## 복구 리허설 (#119 / operations.md §5)

- [ ] `uv run --directory backend --extra server python ../scripts/recovery_rehearsal.py` 가 `RECOVERY_REHEARSAL_OK`로 종료
- [ ] Runbook [`docs/ops/backup-restore.md`](ops/backup-restore.md) RTO 체크리스트를 스테이징에서 한 번 수행
- [ ] 복구 후 daemon이 `CURSOR_EXPIRED` 시 재동기화하는지 스모크
