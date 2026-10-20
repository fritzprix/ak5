# v2 릴리스 체크리스트 (발췌)

정본 조율은 #36. 여기서는 agent가 문서화한 **필수 검증 항목**만 추적한다.

## 복구 리허설 (#119 / operations.md §5)

- [ ] `uv run --directory backend --extra server python ../scripts/recovery_rehearsal.py` 가 `RECOVERY_REHEARSAL_OK`로 종료
- [ ] Runbook [`docs/ops/backup-restore.md`](ops/backup-restore.md) RTO 체크리스트를 스테이징에서 한 번 수행
- [ ] 복구 후 daemon이 `CURSOR_EXPIRED` 시 재동기화하는지 스모크
