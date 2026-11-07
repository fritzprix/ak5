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

## 배포 명령

체크아웃을 그대로 올리지 않는다. `scripts/vercel_deploy.py`가 Python 전용 스테이지를 만들어 그것만 배포한다.

```bash
./scripts/build_web_ui.sh            # 대시보드 자산 (_next) — 없으면 배포 스크립트가 거부
vercel link                          # 최초 1회, 리포 루트에서 (.vercel/는 gitignore)
uv run --directory backend --extra server python ../scripts/vercel_deploy.py
```

스크립트가 하는 일:

1. `uv build backend --wheel`로 wheel을 만들고, `ak5/server/web_ui/_next/static/chunks/*.js`가 들어 있는지와 중복 경로가 없는지 확인한다.
2. 임시 디렉터리에 `api/index.py`, wheel, `requirements.txt`(`./ak5-<ver>-py3-none-any.whl[server,postgres,vercel]`), `vercel.json`, `.vercel/project.json`만 둔다.
3. 그 디렉터리에서 `vercel deploy --prod --yes`를 실행하고, 출력된 배포 URL로 아래 스모크 체크를 돌린다(콜드 스타트 대비 3회 재시도).

| 옵션 | 뜻 |
|---|---|
| `--preview` | 프로덕션 대신 프리뷰 배포 |
| `--stage-only --stage-dir DIR` | 배포하지 않고 스테이지만 만든다 (내용 확인용) |
| `--smoke-url URL` | 스모크 대상. 배포 URL이 Deployment Protection 뒤에 있으면(401) 공개 프로덕션 alias를 준다 |
| `--no-smoke` | 스모크 생략 |

CI에서는 `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`를 환경 변수로 주면 `vercel link` 없이 동작한다.

이렇게 하는 이유 (issue #179 재현 항목):

- `web_ui/_next`는 gitignore 대상이라 체크아웃을 올리면 Vercel CLI가 빼 버린다. 스테이지에는 gitignore가 없고 자산은 wheel 안에 있다.
- `.git`이 없으므로 커밋 작성자가 팀 멤버가 아닐 때의 `BLOCKED` 상태가 생기지 않는다.
- 루트 `package.json`이 없으므로 Node 빌드가 `@vercel/python` 앞에 끼지 않는다.
- hatchling/uv editable 설치 대신 이미 검증된 wheel을 설치한다.

## 환경 변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `AK5_DEPLOY_PROFILE` | 예 | `hosted` |
| `DATABASE_URL` | 예 | 앱 역할 URL (`NOBYPASSRLS` 권장) |
| `AK5_DATABASE_ADMIN_URL` | 권장 | **BYPASSRLS**(또는 superuser) 역할. seed / purge / retention / blob GC·migrate에 필요. 미설정 시 `DATABASE_URL` + GUC로 동작하나 프로덕션에서는 분리 권장 |
| `AK5_JWT_SECRET` | **예 (hosted)** | JWT 서명 비밀. hosted에서 비우면 **기동 거부** (#165). `AK5_JWT_SECRET_FILE`로 마운트해도 됨 |
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

## 한 체크아웃에서 로컬과 Vercel

같은 체크아웃과 같은 `.env`로 두 경로를 모두 쓸 수 있다. `ak5 up`은 hosted 값을 무시하고 로컬 런타임을 고정한다. Vercel은 체크아웃의 `.env`가 아니라 Vercel 프로젝트 환경 변수를 쓴다.

| 항목 | 로컬 (`ak5 up`) | Vercel |
|---|---|---|
| DB | SQLite (고정) | Neon |
| Blob | 데이터 디렉터리 아래 `fs` | `vercel-blob` (권장) |
| Notify | `inproc` | `poll` |
| Profile | `local` | `hosted` |
| Web UI | wheel에 든 `web_ui`, 또는 `scripts/build_web_ui.sh`로 빌드한 것 | 같은 빌드 결과물이 배포에 포함돼야 함 |
| `.env` | 공유해도 됨. `ak5 up`이 로컬 값으로 덮어씀 | 쓰지 않음 (프로젝트 env) |

- `ak5 up`은 `.env`나 셸에 무엇이 있든 다음 키를 로컬 값으로 고정하고, 바꾼 키는 시작할 때 `ignoring …` 메시지로 알려 준다: `DATABASE_URL`, `AK5_DATABASE_ADMIN_URL`, `DATABASE_ADMIN_URL`, `ATTACHMENTS_DIR`, `AK5_DEPLOY_PROFILE`, `AK5_BLOB_BACKEND`, `AK5_NOTIFY`, `AK5_INSTANCES`, `LONG_POLL_USE_INPROCESS_NOTIFY`, `AK5_CLIENT_IP_HEADER`, `AK5_TRUSTED_PROXIES` ([MANUAL §2.2](../MANUAL.md)). `.env`의 Neon 값을 로컬에서 일부러 쓰려면 `ak5 serve`를 쓴다 ([MANUAL §2.3](../MANUAL.md)).
- `ak5 up`에서도 인증 비밀(`AK5_AUTH_PASSWORD` 등)은 `.env` 값을 그대로 쓴다.
- `backend/src/ak5/server/web_ui/`의 빌드 결과물(`_next/`)은 gitignore 대상이다. 배포 전에 `scripts/build_web_ui.sh`를 돌리고, 업로드에 `_next/`가 빠지지 않았는지 아래 스모크 체크로 확인한다. HTML만 있고 chunk가 404면 대시보드가 깨진다.
- `/tmp` 위의 `fs` blob은 임시 우회일 뿐이다(인스턴스마다 따로이고 사라진다). 상시 배포는 `vercel-blob`을 쓴다.

## 배포 스모크 체크

배포 URL마다 돌린다. `/readyz`가 `ready`인지, `/meta`의 `api_version`이 `2`인지, 대시보드 HTML이 오는지, 그 HTML이 참조하는 `/_next/static/chunks/*.js` 하나가 JavaScript로 200인지 본다. 하나라도 실패하면 exit 1.

```bash
uv run python scripts/smoke_hosted.py https://<your-deployment>.vercel.app
uv run python scripts/smoke_hosted.py http://127.0.0.1:8000 --json   # 로컬 ak5 up / serve --ui
```

## 로컬 검증

```bash
uv run --directory backend --extra server pytest tests/test_create_app_import_side_effects.py -q
```

로컬 Postgres 스택: 리포 루트 `docker-compose.postgres.yml` (포트는 `127.0.0.1`에만 바인딩).
