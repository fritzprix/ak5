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

## 진입점 · 패키지 import

리포지토리 루트 `api/index.py`가 Vercel Python runtime에 ASGI 앱을 노출한다.

```text
api/index.py  →  create_app()  →  lifespan (prepare / schema check / seed)
```

`create_app()` import는 DB 연결·데이터 디렉터리 생성·시드를 하지 않는다. 초기화는 lifespan(또는 이후 lazy)에서만 한다.

### Vercel이 `ak5`를 찾는 방법

패키지 소스는 `backend/src/ak5`이다. Vercel Python은 루트 `api/`를 진입점으로 쓰므로, 빌드 시 **editable install**과 **PYTHONPATH**를 맞춰야 한다.

최소 설정 예 (`vercel.json` — 리포에 커밋하지 않아도 프로젝트 설정으로 동일하게 가능):

```json
{
  "builds": [{ "src": "api/index.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "api/index.py" }]
}
```

Install Command 예:

```bash
pip install -e "./backend[server,postgres,vercel]"
```

또는 Build 환경 변수:

| 변수 | 값 |
|---|---|
| `PYTHONPATH` | `backend/src` |

`pip install -e ./backend[...]`를 쓰면 wheel에 `ak5`가 들어가므로 `PYTHONPATH`는 선택이다. 소스만 올리는 경우 `PYTHONPATH=backend/src`가 필수다.

의존 extra: hosted Neon에는 최소 `server` + `postgres`; Blob이 Vercel이면 `vercel` 추가.

## 환경 변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `AK5_DEPLOY_PROFILE` | 예 | `hosted` |
| `DATABASE_URL` | 예 | 앱 역할 URL (`NOBYPASSRLS` 권장) |
| `AK5_DATABASE_ADMIN_URL` | 권장 | **BYPASSRLS**(또는 superuser) 역할. seed / purge / retention / blob GC·migrate에 필요. 미설정 시 `DATABASE_URL` + GUC로 동작하나 프로덕션에서는 분리 권장 |
| `AK5_JWT_SECRET` | **예 (hosted)** | JWT 서명 비밀. 미설정 시 인스턴스별 임시 키를 파일에 만들어 쓰므로 서버리스에서 토큰이 인스턴스 간에 검증되지 않음 — 반드시 고정 시크릿을 넣는다 (기동 거부는 #165) |
| `AK5_AUTO_MIGRATE` | 예 | `0` (서버리스). 스키마는 파이프라인에서 `ak5 db upgrade` |
| `AK5_BLOB_BACKEND` | 예 | `vercel-blob` 또는 `s3` |
| `AK5_NOTIFY` | 예 | `poll` |
| `AK5_NOTIFY_POLL_MS` | 선택 | 기본 `1000` |
| `AK5_CLIENT_IP_HEADER` | 예 | `x-vercel-forwarded-for` |
| `BLOB_READ_WRITE_TOKEN` | vercel-blob 시 | Vercel Blob 토큰 |

```bash
AK5_DEPLOY_PROFILE=hosted
DATABASE_URL=postgresql+asyncpg://APP_USER:PASS@HOST/DB?sslmode=require
AK5_DATABASE_ADMIN_URL=postgresql+asyncpg://ADMIN_BYPASSRLS:PASS@HOST/DB?sslmode=require
AK5_JWT_SECRET=<long-random-secret>
AK5_AUTO_MIGRATE=0
AK5_BLOB_BACKEND=vercel-blob
AK5_NOTIFY=poll
AK5_NOTIFY_POLL_MS=1000
AK5_CLIENT_IP_HEADER=x-vercel-forwarded-for
```

스키마는 배포 파이프라인에서:

```bash
uv run --directory backend --extra server --extra postgres ak5 db upgrade
```

유지보수 cron (blob GC / retention):

```bash
uv run --directory backend --extra server --extra postgres ak5 db maintenance --sweep-blob-gc
```

## 로컬 검증

```bash
uv run --directory backend --extra server pytest tests/test_create_app_import_side_effects.py -q
```

로컬 Postgres 스택: 리포 루트 `docker-compose.postgres.yml` (포트는 `127.0.0.1`에만 바인딩).
