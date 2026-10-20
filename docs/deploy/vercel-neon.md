# Vercel + Neon (hosted 1차)

deployment.md §4 · §9-4 · issue #110.

## 전제

| 항목 | 권장 |
|---|---|
| DB | Neon Postgres (`DATABASE_URL` / `AK5_DATABASE_ADMIN_URL`) |
| Blob | `AK5_BLOB_BACKEND=vercel-blob` (또는 `s3`) — **multi-instance에서 `fs` 금지** |
| Notify | `AK5_NOTIFY=poll` (서버리스; `pg` LISTEN 연결 부적합) |
| Migrate | `AK5_AUTO_MIGRATE=0` + CI/cron에서 `ak5 db upgrade` |
| Client IP | `AK5_CLIENT_IP_HEADER=x-vercel-forwarded-for` (#125) |
| Deploy profile | `AK5_DEPLOY_PROFILE=hosted` |

## 진입점

리포지토리 루트 `api/index.py`가 Vercel Python runtime에 ASGI 앱을 노출한다.

```text
api/index.py  →  create_app()  →  lifespan (prepare / schema check / seed)
```

`create_app()` import는 DB 연결·데이터 디렉터리 생성·시드를 하지 않는다. 초기화는 lifespan(또는 이후 lazy)에서만 한다.

## 환경 변수 예시

```bash
AK5_DEPLOY_PROFILE=hosted
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST/DB?sslmode=require
# optional BYPASSRLS role for seed/purge:
# AK5_DATABASE_ADMIN_URL=postgresql+asyncpg://ADMIN:...
AK5_AUTO_MIGRATE=0
AK5_BLOB_BACKEND=vercel-blob
AK5_NOTIFY=poll
AK5_NOTIFY_POLL_MS=1000
AK5_CLIENT_IP_HEADER=x-vercel-forwarded-for
AK5_JWT_SECRET=...   # required in hosted
```

스키마는 배포 파이프라인에서:

```bash
uv run --directory backend --extra server ak5 db upgrade
```

유지보수 cron (blob GC / retention):

```bash
uv run --directory backend --extra server ak5 db maintenance --sweep-blob-gc
```

## 로컬 검증

```bash
uv run --directory backend --extra server pytest tests/test_create_app_import_side_effects.py -q
```
