# 백업 · 복구 Runbook

operations.md §5 · issue #119. **RPO ≤ 5분** (관리형 PITR), **RTO ≤ 1시간**.

## 무엇을 백업하는가

| 대상 | local / self-hosted | hosted |
|---|---|---|
| DB (SQLite) | `AK5_DATA_DIR/ak5.db` + `db_safety` 스냅샷(`backups/`) | — |
| DB (Postgres) | 관리형 PITR(Neon/RDS) **1차**, 보조 `ak5 db export` cron | PITR 필수 |
| Blob | `ATTACHMENTS_DIR` 또는 버킷 전체 | 버킷 버전 관리 + lifecycle |
| 비밀 | `jwt_secret` 파일, OIDC client secret 등 — **DB와 분리** | 시크릿 매니저 |

비밀을 DB dump와 같은 경로에 두지 않는다.

## 일상 백업

### SQLite (local)

- 서버가 티켓이 있는 DB에서 주기적으로 `backups/`에 스냅샷을 남긴다 (`db_safety`).
- 추가 오프사이트 복사:

```bash
# 일관된 온라인 스냅샷: `.backup` (쓰기가 있어도 찢어지지 않음). 단순 `cp`는 쓰지 않는다.
OUT="/offsite/ak5-$(date -u +%Y%m%dT%H%M%SZ).db"
sqlite3 "$AK5_DATA_DIR/ak5.db" ".backup '$OUT'"
# 대안: sqlite3 "$AK5_DATA_DIR/ak5.db" "VACUUM INTO '$OUT';"
rsync -a "$AK5_DATA_DIR/attachments/" "/offsite/attachments/"
```

### Postgres + 논리 export (보조)

```bash
# cron 예: 매 15분 (PITR이 RPO를 담당; export는 이식·감사 보조)
# export 파일에 사용자 데이터가 들어가므로 디렉터리 권한 제한 (umask 077 / mode 0700)
0,15,30,45 * * * * umask 077; mkdir -p -m 700 /backup && cd /opt/ak5 && \
  uv run --directory backend --extra server --extra postgres \
  ak5 db export "/backup/ak5-$(date -u +\%Y\%m\%dT\%H\%M\%SZ).jsonl"
```

스키마는 배포 파이프라인에서:

```bash
uv run --directory backend --extra server --extra postgres ak5 db upgrade
```

### Lazy 유지보수

```bash
# 이벤트 retention · (선택) blob GC · usage 정합
uv run --directory backend --extra server ak5 db maintenance --sweep-blob-gc --reconcile-usage
```

서버리스에서는 lifespan에 장기 작업을 두지 말고 위 cron을 쓴다.

### Blob / 비밀

- S3/R2: 버킷 버전 관리 ON, lifecycle로 이전 버전 보존 ≥ RPO 여유.
- Vercel Blob: 플랫폼 백업 정책 + 필요 시 키 목록 스냅샷.
- `AK5_JWT_SECRET` / OIDC secrets: Vault·플랫폼 env — DB 백업 볼륨과 물리적으로 분리.

## 복구 절차

### A. SQLite — `db_safety` 자동 복원

wipe로 보이는 live DB + 더 풍부한 `backups/`가 있으면 기동 시 복원을 시도한다. 의도적 reset이면 skip marker를 둔다 (`AK5_ALLOW_DESTRUCTIVE_RESET` / reset 플로우).

수동:

```bash
# 서비스 중지 후
cp /offsite/ak5-TIMESTAMP.db "$AK5_DATA_DIR/ak5.db"
rsync -a /offsite/attachments/ "$AK5_DATA_DIR/attachments/"
# JWT 등 비밀은 별도 경로에서 복원
uv run ak5 serve   # 또는 기존 유닛 재시작
curl -sf "$BASE/readyz"
```

### B. Postgres — PITR (권장)

클라우드 콘솔에서 RPO 목표 시각으로 restore → 앱 `DATABASE_URL`을 새 엔드포인트로 전환 → `readyz`.

### C. 논리 import (빈 DB만)

```bash
# 대상 URL은 비어 있어야 한다 (deployment.md §1.4)
export DATABASE_URL='postgresql+asyncpg://…'   # 또는 새 sqlite 파일
uv run --directory backend --extra server ak5 db import /backup/dump.jsonl --to "$DATABASE_URL"
# blob은 같은 시점 스냅샷을 ATTACHMENTS_DIR / 버킷에 맞춤
```

## 복구 후 동작 규칙 (구현 확인)

| 상황 | 기대 |
|---|---|
| DB에 첨부 행이 있는데 blob 없음 | 다운로드 `404` + `error.code=ATTACHMENT_MISSING` — 티켓/보드는 유지 |
| blob만 있고 DB 행 없음 | `ak5 db maintenance --sweep-blob-gc`가 정리 |
| daemon `ack_cursor` > 서버 `seq` (로그가 과거로 감) | `410 CURSOR_EXPIRED` → daemon `WORK_AVAILABLE` 재동기화 |
| 복구 직후 활성 claim | lease 만료로 간주 → `reclaim_expired_leases` (T8/T20) 정상 회수 |

리허설: [`scripts/recovery_rehearsal.py`](../../scripts/recovery_rehearsal.py) (빈 DB ← export → `readyz` + 스모크). 릴리스 체크리스트(#36)에 포함.

## RTO 체크리스트 (≤ 1시간)

1. [ ] 백업/PITR 지점 선택 (RPO ≤ 5분)
2. [ ] DB 복원 또는 `db import`
3. [ ] Blob·비밀 동일 세대 맞춤 (불일치는 ATTACHMENT_MISSING 허용)
4. [ ] `GET /readyz` → `database` / `schema` / `blob` 통과
5. [ ] 스모크: `GET /meta`, owner `whoami` 또는 보드 목록
6. [ ] daemon/구독 재기동 — `CURSOR_EXPIRED` 시 재동기화 확인
7. [ ] (선택) `ak5 db maintenance --sweep-blob-gc`
