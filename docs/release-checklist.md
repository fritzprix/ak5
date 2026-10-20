# v2 릴리스 체크리스트 (발췌)

정본 조율은 [#36](https://github.com/fritzprix/ak5/issues/36) (사람/오케스트레이터; agent 미위임).  
에픽 [#22](https://github.com/fritzprix/ak5/issues/22): Wave 7·8·pipeline MVP는 `feat/workflow-v2`에 착지. 남은 오픈 이슈는 실질적으로 **#36** (+ 에픽 트래킹).

## 릴리스 (#36)

- [ ] 대상 브랜치 결정 (`dev/2.0.x` 등) 후 `feat/workflow-v2` squash
- [ ] 버전 2.0.0 · CHANGELOG · v1→v2 마이그레이션 가이드
- [ ] 보안 리뷰 · 회귀 게이트 · PyPI/npm · GitHub Release

## 복구 리허설 (#119 / operations.md §5)

- [ ] `uv run --directory backend --extra server python ../scripts/recovery_rehearsal.py` 가 `RECOVERY_REHEARSAL_OK`로 종료
- [ ] Runbook [`docs/ops/backup-restore.md`](ops/backup-restore.md) RTO 체크리스트를 스테이징에서 한 번 수행
- [ ] 복구 후 daemon이 `CURSOR_EXPIRED` 시 재동기화하는지 스모크
