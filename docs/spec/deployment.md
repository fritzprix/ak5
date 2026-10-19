# Deployment Abstraction — Spec

> **상태: 확정 (#20, 2026-10-19).** §9의 모든 항목이 확정되었다(1·7은 PR 코멘트 결정, 2~6은 권장안 승인). 구현은 §8 순서의 이슈로 분할해 위임한다.
> 관련: [identity.md](./identity.md) §8 (배포 모드) · [events.md](./events.md) §1 · #37 (hosted 운영 기본기)

## 0. 목표와 비목표

**목표**

- 같은 코드로 **local(SQLite + 파일) / self-hosted(단일·다중 인스턴스) / hosted(서버리스, Postgres + Blob)** 를 지원한다.
- 로컬 사용 경험(설치 없이 `ak5 serve`)은 바뀌지 않는다. 새 의존성은 extra(`ak5[postgres]` 등)로만 들어온다.
- 서버 프로세스는 **상태를 메모리에 두지 않는다**(캐시 제외). 어떤 요청이 어떤 인스턴스에 가도 같은 결과여야 한다.

**비목표**

- 자동 스케일링, 멀티 리전, 서비스 메시. 배포 템플릿(Dockerfile/compose/Vercel 설정)은 예시 수준으로만 제공한다.
- SQLite ↔ Postgres 간 **온라인** 데이터 이동. 이행은 export/import 한 번(§1.4)으로 한정한다.

## 1. 데이터베이스

### 1.1 지원 대상

| 환경 | 드라이버 | URL 예 |
|---|---|---|
| local | `aiosqlite` | `sqlite+aiosqlite:///…/ak5.db` (기본) |
| self-hosted / hosted | `asyncpg` (`ak5[postgres]`) | `postgresql+asyncpg://…` (Neon, RDS, Aurora, 자체 호스팅) |

`DATABASE_URL`(기존 설정, `AK5_` 접두사 없음)의 scheme으로 방언을 고른다. 그 외 방언은 지원하지 않는다. 이 문서가 새로 도입하는 변수만 `AK5_` 접두사를 쓴다.

### 1.2 스키마 이행: Alembic

- 현재 스키마 변경은 `database.py`의 SQLite 전용 함수(`migrate_sqlite_*`)와 `create_all`에 흩어져 있다. 이는 Postgres에서 동작하지 않는다.
- **Alembic을 단일 이행 경로로 도입한다.**
  1. v2.0.0 스키마를 **baseline revision** 하나로 고정한다. 신규 DB는 baseline부터 `upgrade head`.
  2. 기존 `migrate_sqlite_*`는 **v1 → v2 이행 전용**으로 동결한다(새 변경 금지). v2.0.0 이후의 모든 변경은 Alembic revision으로만 한다.
    - **결정 (2026-10-19)**: 현재는 초기 단계이고 사용자가 많지 않아 **v1→v2 데이터 마이그레이션은 필요 없다**. baseline revision은 *신규 DB 생성용*이며, 기존 SQLite 파일은 `ak5 db export/import`(§1.4)로 한 번에 v2 스키마로 옮긴다. (기존 `migrate_sqlite_*`와 v1 회귀 게이트를 언제 제거할지는 #36에서 정한다.)
  3. 서버 시작 시 `upgrade head`를 자동 실행하되, 다중 인스턴스에서는 **advisory lock**(Postgres) / 파일 락(SQLite)으로 한 인스턴스만 실행한다. `AK5_AUTO_MIGRATE=0`이면 자동 실행을 끄고 `ak5 db upgrade`로만 실행한다(서버리스 권장).
  4. SQLite의 ALTER 한계(컬럼 삭제/제약 변경)는 Alembic `batch_alter_table`로 처리한다.
- `db_safety`의 스냅샷/dual-DB 보호는 **SQLite 전용**으로 유지한다. Postgres의 백업·복구는 운영 책임이며 #37에서 다룬다.

### 1.3 방언 차이로 코드가 지켜야 할 것

| 항목 | 규칙 |
|---|---|
| claim 경쟁 | 조건부 `UPDATE … WHERE claim 없음`(원자적)으로 통일한다. Postgres에서는 필요 시 `SELECT … FOR UPDATE SKIP LOCKED`. SQLite 전용 `BEGIN IMMEDIATE`에 의존하지 않는다 |
| 시간 | DB에는 **naive UTC**를 저장한다(현재와 동일). 비교는 앱에서 만든 값을 바인딩한다 |
| JSON 컬럼 | `Text`에 JSON 문자열(현재와 동일). Postgres 전용 `JSONB` 최적화는 비목표 |
| 외래키/인덱스 | SQLite의 `PRAGMA foreign_keys=ON`과 동일하게 Postgres도 FK 강제 |
| 동시 쓰기 | SQLite는 WAL + busy_timeout, Postgres는 풀(기본 5, `AK5_DB_POOL_SIZE`). 서버리스는 풀 크기 1 + 외부 풀러(PgBouncer/Neon pooler) 권장 |

### 1.4 SQLite → Postgres 이행

`ak5 db export --out dump.jsonl` / `ak5 db import dump.jsonl --to $DATABASE_URL`(테이블 단위 JSON Lines, 스키마 버전 헤더 포함). 대상 DB는 비어 있어야 하며, 이벤트 로그 `seq`는 보존한다. 온라인 이행은 지원하지 않는다.

### 1.5 Row-Level Security (이중 방어)

identity.md §7에 따라 Postgres에서는 `workspace_id`를 가진 테이블에 RLS를 건다.

- 요청마다 트랜잭션 시작 시 `SET LOCAL ak5.workspace_id = '<ws>'`.
- 정책: `USING (workspace_id = current_setting('ak5.workspace_id', true))`.
- 애플리케이션 쿼리 범위 강제가 **1차**, RLS는 **2차**(버그가 있어도 누수되지 않도록)다. 마이그레이션·관리 작업용 역할은 RLS를 우회한다.
- SQLite에는 적용하지 않는다(단일 사용자 가정).

## 2. 첨부 파일 (Blob)

현재 `ATTACHMENTS_DIR`(파일시스템)에 직접 읽고 쓴다. 이를 **`BlobStore` 인터페이스**로 추상화한다.

```python
class BlobStore(Protocol):
    async def put(self, key: str, data: AsyncIterator[bytes], *, content_type: str, max_bytes: int) -> int: ...
    async def open(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
```

| 백엔드 | 선택 | 비고 |
|---|---|---|
| `fs` (기본) | `AK5_BLOB_BACKEND=fs`, `ATTACHMENTS_DIR` | local/self-hosted 단일 인스턴스 |
| `s3` | `AK5_BLOB_BACKEND=s3`, `AK5_S3_BUCKET`, 선택 `AK5_S3_ENDPOINT`(MinIO/R2) | extra `ak5[s3]` |
| `vercel-blob` | `AK5_BLOB_BACKEND=vercel-blob`, 토큰 | extra `ak5[vercel]` |

- **키 규칙**: `ws/<workspace_id>/<ticket_id>/<attachment_id>`. 키에 사용자 입력(파일명)을 넣지 않는다(경로 조작 방지). 원본 파일명은 DB에만 둔다.
- **권한은 항상 서버가 검사**한다(#75: 다운로드 시 workspace 확인). 백엔드가 서명 URL을 지원해도 **기본은 서버 프록시 스트리밍**이다. 서명 URL 직접 다운로드는 §9의 결정 사항이다.
- 크기 한도 `MAX_ATTACHMENT_SIZE_BYTES`는 스트리밍 중 강제한다(현재 동작 유지). 서버리스의 요청 본문 한도(Vercel 4.5MB 등)보다 큰 업로드는 **서명 업로드 URL 2단계**(init → PUT → complete)가 필요하다 — 초기 범위에서는 제외하고 한도를 문서화한다.
- ticket/workspace 삭제 시 blob 삭제는 DB 커밋 **후** 최선 노력으로 수행하고, 실패분은 `blob_gc` 대상 테이블에 남겨 지연 정리한다.

## 3. 이벤트 알림 (wake-up)

이벤트의 정본은 DB의 `events` 테이블이며, `EventBus`는 **깨우기 신호만** 전달한다(현재 구조). 다중 인스턴스에서는 인스턴스 간 신호가 필요하다.

| 모드 | 선택 | 동작 |
|---|---|---|
| `inproc` (기본) | `AK5_NOTIFY=inproc` | 현재 구현. 단일 프로세스 |
| `poll` | `AK5_NOTIFY=poll` | waiter가 `AK5_NOTIFY_POLL_MS`(기본 1000)마다 `max(seq)`만 조회. 추가 인프라 없음, 지연 ≤ 주기 |
| `pg` | `AK5_NOTIFY=pg` | Postgres `LISTEN/NOTIFY`로 즉시 wake-up. 연결 1개를 인스턴스가 점유(서버리스 부적합) |
| `redis` (선택) | `AK5_NOTIFY=redis` | pub/sub. 이미 Redis가 있을 때만 |

- 어떤 모드든 **정확성은 `seq` 재조회로 보장**한다. 신호를 놓쳐도 다음 poll/타임아웃에서 복구되므로 신호 유실은 지연일 뿐 누락이 아니다.
- 서버리스는 장기 연결(SSE/long-poll)이 제한되므로 `poll` 모드 + 짧은 long-poll 타임아웃(§5)을 권장한다. 한도는 #37에서 정한다.

## 4. 진입점

| 환경 | 진입점 |
|---|---|
| local / self-hosted | `ak5 serve` (uvicorn). 컨테이너는 `Dockerfile` + `docker-compose.yml` 예시 |
| Vercel | `api/index.py`에서 `ak5.server.main:create_app()`의 ASGI 앱 노출 |
| AWS | Lambda: `Mangum(create_app())` 어댑터(extra `ak5[lambda]`), 또는 ECS/Fargate에서 uvicorn |

`create_app()`은 **import 시 부작용이 없어야 한다**(DB 연결·디렉터리 생성·시드 금지). 초기화는 lifespan(상시 서버) 또는 첫 요청의 lazy 초기화(서버리스)로만 한다.

## 5. 서버리스 제약과 lazy 처리

백그라운드 루프(`create_task`)는 서버리스에서 신뢰할 수 없다. 주기 작업은 **요청 경로에서 lazy하게** 또는 **외부 스케줄러(cron)** 로 실행한다.

| 작업 | 상시 서버 | 서버리스 |
|---|---|---|
| lease 만료 회수 | 요청 경로에서 `reclaim_expired_leases` 호출(현재 동작) + 선택적 주기 sweep | 동일(claim·next·board 조회 시 회수). 별도 스케줄 불필요 |
| 이벤트 retention purge | 워터마크(`event_log_meta`)를 보고 최소 간격으로 lazy purge(현재) | 동일. 추가로 `ak5 db maintenance`를 cron으로 호출 가능 |
| 만료 challenge/세션/device code 정리 | 쓰기 경로에서 기회적으로 삭제(WebAuthn challenge와 동일) | 동일 |
| blob GC | 주기 sweep | `ak5 db maintenance` (cron) |

- 모든 lazy 작업은 **멱등**이고, 다중 인스턴스에서 동시에 실행되어도 안전해야 한다(워터마크 갱신은 조건부 UPDATE).
- long-poll 최대 시간은 플랫폼 제한 이하로 설정한다(기존 설정 `LONG_POLL_MAX_WAIT`, 기본 25초 — events.md §3과 동일하며 서버리스 실행 제한보다 짧게 둔다). daemon은 타임아웃을 정상 종료로 처리한다(events.md §3).

## 6. 다중 인스턴스에서 옮겨야 하는 프로세스 메모리 상태

현재 프로세스 메모리에 있어 다중 인스턴스에서 깨지는 상태 목록이다(#20 리뷰 노트 포함).

| 상태 | 위치 | 문제 | 이행안 |
|---|---|---|---|
| OIDC pending (state/nonce/PKCE) | `auth/sessions._oidc_pending` (상한 256) | callback이 다른 인스턴스로 가면 실패, 상한 밀어내기로 로그인 방해 가능 | **DB 테이블** `oidc_pending`(TTL 10분, 1회용). 대안: 서명·암호화 cookie(상태 전부를 cookie에 담음) — §9 |
| upstream device flow pending | `auth/oidc_device._pending` | 동일 | DB 테이블 |
| device grant user_code 실패 rate limit | `device_grant._rate_failures` | 인스턴스별로 분산되어 무차별 대입 한도가 N배 | DB 카운터(`device_grant_failures`, 윈도우 단위) 또는 Redis. 상세 한도는 #37 |
| EventBus waiters | `event_bus` | 인스턴스 간 신호 없음 | §3 |
| WebAuthn challenge | DB (#28에서 이미 해결) | — | 유지 |


**결정 (2026-10-19)** — 위 표의 세 항목은 모두 **DB로 이전**한다.

- `oidc_pending(state PK, provider_id, nonce, code_verifier, client_key, expires_at)` — expires_at은 naive UTC, TTL 10분, 1회용(콜백에서 조회 즉시 삭제). 만료분은 INSERT 시 기회적으로 삭제하고 선택적으로 cron(`ak5 db maintenance`)이 정리한다. `auth/sessions._oidc_pending`의 상한 256 방식은 폐기하고 **TTL + IP(및 가능하면 principal)별 상한**으로 대체한다.
- upstream device flow pending(`oidc_device._pending`)은 위 테이블에 `kind` 구분으로 통합하거나 `device_pending` 테이블로 분리한다 — 구현 시 둘 중 단순한 쪽을 택한다(동작 차이 없음).
- `device_grant_failures(id PK, client_key, created_at)` + 인덱스 `(client_key, created_at)` (같은 시각의 실패가 둘일 수 있어 복합 PK 대신 대리키) — 윈도우(현행 10분)보다 오래된 행은 조회 시 `created_at > now - window`로 걸러내고 기회적으로 삭제한다. 한도(현행 20회)는 유지하며 다중 인스턴스에서 합산된다. 상세 한도·키 정책은 operations.md(#37)와 맞춘다.
- 위 테이블은 baseline 이후에 추가되므로 **Alembic revision `0002_auth_state`** 로 들어갔다(#107, baseline은 동결). upstream device pending은 `device_pending`(키: IdP device_code의 해시)으로 **분리**했다. pending 상한은 전역 256이 아니라 **client key(IP)당 20**이며, 넘으면 그 호출자만 429(`Retry-After`)를 받는다. 구현: #107.
원칙: **상한으로 밀어내는 방식은 금지**한다(공격자가 정상 사용자의 pending을 쫓아낼 수 있음). 만료(TTL)와 principal/IP별 상한으로만 제한한다.

## 7. 설정 요약

| 변수 | 기본 | 의미 |
|---|---|---|
| `DATABASE_URL` (기존) | SQLite 파일 | §1.1 |
| `AK5_AUTO_MIGRATE` | `1` | 시작 시 `upgrade head` (서버리스는 `0` 권장) |
| `AK5_DB_POOL_SIZE` | `5` | Postgres 풀 크기 |
| `AK5_BLOB_BACKEND` | `fs` | `fs` / `s3` / `vercel-blob` |
| `AK5_NOTIFY` | `inproc` | `inproc` / `poll` / `pg` / `redis` |
| `AK5_NOTIFY_POLL_MS` | `1000` | `poll` 주기 |
| `LONG_POLL_MAX_WAIT` (기존) | `25` | long-poll 상한(초), 플랫폼 실행 제한보다 짧게 |

잘못된 조합은 **시작 시 거부**한다(예: `AK5_NOTIFY=inproc` + 비-loopback 다중 인스턴스 선언, Postgres인데 `fs` blob + `AK5_INSTANCES>1`).

## 8. 구현 순서 (확정 시 이슈 분할 제안)

1. `BlobStore` 추상화 + `fs` 구현 (동작 불변 리팩터)
2. Alembic baseline + `ak5 db upgrade`, SQLite 이행 함수 동결
3. Postgres 지원: 방언 차이 정리(§1.3), CI에 Postgres job 추가, RLS(§1.5)
4. 프로세스 메모리 상태의 DB 이전(§6)
5. 알림 모드 `poll` / `pg` (§3)
6. `s3` / `vercel-blob` 백엔드, 진입점 예시(§4), `ak5 db export/import`

각 단계는 독립 PR이며, 1·2는 동작 변화가 없으므로 먼저 진행할 수 있다.

## 9. 결정 필요 (권장안 포함)

| # | 질문 | 권장 |
|---|---|---|
| 1 | OIDC pending 저장 방식 | ✅ **확정: DB 테이블** (§6) |
| 2 | 첨부 다운로드 | ✅ 확정(2026-10-19): **서버 프록시 스트리밍**(권한 검사 단순). 서명 URL은 대용량 필요가 생길 때 |
| 3 | 서버리스 대용량 업로드 | ✅ 확정(2026-10-19): 초기 범위 제외, 한도 문서화 |
| 4 | hosted 1차 대상 | ✅ 확정(2026-10-19): **Vercel + Neon 하나만** 먼저 검증, AWS는 같은 인터페이스로 후속 |
| 5 | Redis | ✅ 확정(2026-10-19): **기본 의존성에서 제외**, `redis` 알림 모드만 선택 지원 |
| 6 | RLS 범위 | ✅ 확정(2026-10-19): workspace_id 보유 테이블 전부, 2차 방어로만 사용 |
| 7 | v2.0.0 포함 여부 | ✅ **확정: hosted 배포 추상화는 v2.0.0 범위에 포함**. #36의 "wave 5 포함 전체 2.0.0"과 일치. #100 §9-8도 같은 결정 |

