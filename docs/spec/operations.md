# Hosted Operations Basics — Spec

> **상태: 확정 (#37, 2026-10-19).** §9의 모든 항목이 확정되었다(권장안 승인, 8번은 #98 §9-7과 동일 결정). 구현은 §8 순서의 이슈로 분할해 위임한다.
> 선행/관련: [deployment.md](./deployment.md) (공유 저장소·다중 인스턴스, #20 초안) · [identity.md](./identity.md) · [events.md](./events.md)

## 0. 범위와 원칙

hosted 다중 사용자 배포 전에 필요한 **운영 기본기**를 정의한다: rate limit, 연결 한도, quota, 관측성, 백업·복구, 악용 방지.

- **local(`ak5 serve`)의 기본 동작은 바뀌지 않는다.** 모든 한도는 설정으로 켜며, local 기본값은 "무제한/비활성"이다(단일 사용자 도구를 스스로 막지 않는다). hosted 프로필에서 기본값을 켠다(§7).
- 한도 상태는 **프로세스 메모리에 두지 않는다**(deployment.md §6). 공유 저장소(DB, 선택적으로 Redis)를 쓴다.
- 한도에 걸리면 **재시도 가능한 오류**로 알린다(`Retry-After`). 데이터는 절대 조용히 버리지 않는다.
- 관측 데이터에 **토큰·비밀·첨부 내용을 남기지 않는다**(§4.4).

## 1. 현황 (코드 기준)

| 영역 | 현재 |
|---|---|
| rate limit | device grant `user_code` 실패만: client key당 10분에 20회, **프로세스 메모리**(`device_grant._rate_failures`). 다른 엔드포인트에는 없음 |
| long-poll | subscription당 consumer 1(`409 CONSUMER_CONFLICT`), 대기 상한 `LONG_POLL_MAX_WAIT`(25초). 인스턴스/사용자별 총 연결 한도는 없음 |
| quota | `max_auto_boards`(workspace 정책)와 첨부 파일당 크기 한도만. board/ticket/첨부 총량·멤버·agent 수 한도 없음 |
| 로그/추적 | 요청 추적 ID, 구조화 로그, 메트릭 없음 |
| 백업 | SQLite 전용 `db_safety` 스냅샷·복원. Postgres·blob 백업은 정의 없음 |
| 이벤트 보존 | `EVENT_RETENTION_DAYS`(기본 7), lazy purge |

## 2. Rate limit

### 2.1 키와 단위

요청은 세 단위로 센다. 하나라도 초과하면 거부한다.

| 단위 | 키 | 용도 |
|---|---|---|
| principal | `usr_…` / `agt_…` | 한 주체의 폭주 |
| workspace | `workspace_id` | 한 tenant가 서버를 독점하는 것 방지 |
| client IP | 신뢰한 프록시 기준의 원 IP | **인증 전** 엔드포인트(login, device grant, 초대 수락) |

IP는 `AK5_TRUSTED_PROXIES`(CIDR 목록)에 속한 hop의 `X-Forwarded-For`만 신뢰한다. 설정이 없으면 소켓 peer 주소를 쓴다.

### 2.2 알고리즘과 저장

- **고정 윈도우 카운터**(윈도우 60초)로 시작한다. 구현이 단순하고 DB 한 번의 조건부 upsert로 원자적이다. 경계에서 최대 2배 버스트가 가능하나 운영 기본기 수준에서는 허용한다.
- 저장소: 테이블 `rate_counters(key, window_start, count)` + 만료분 기회적 삭제. Redis가 있으면 `INCR`+`EXPIRE`로 대체 가능(선택, deployment.md §3과 같은 어댑터 방식).
- 카운터 쓰기가 요청 지연/락 경합을 키우지 않도록, 읽기 전용 GET은 principal 단위만 세고 workspace 단위는 쓰기(POST/PATCH/DELETE)에서만 센다. SQLite는 hosted 대상이 아니므로 local에서는 기본 비활성이다.

### 2.3 기본 한도 (제안, hosted 프로필)

| 구분 | 한도 / 분 | 비고 |
|---|---|---|
| 인증된 읽기 | principal당 300 | board/ticket 조회 |
| 인증된 쓰기 | principal당 120, workspace당 600 | 전이, 댓글, 생성 |
| 첨부 업로드 | principal당 20 | 대역폭은 §3 quota로 별도 제한 |
| 로그인(OIDC start/callback, local login) | IP당 20 | |
| device grant 시작/승인 | IP당 20, `user_code` 실패는 10분에 20(현행 유지) | §6 |
| 초대 생성/수락 | principal당 30 / IP당 20 | §6 |
| agent credential 발급 | principal당 20 | §6 |
| `/events/stream`, long-poll | 제외 | 연결 한도(§2.5)로 제한 |

값은 `AK5_RATE_LIMITS`(JSON)로 덮어쓸 수 있다. `0`은 해당 규칙 비활성.

### 2.4 응답

- `429` + `Retry-After: <초>` + 에러 코드 **`RATE_LIMITED`**(신규, retryable).
- 클라이언트 계약: CLI는 종료 코드 **3**(경합·일시적 용량 부족, 재시도 가능 — cli.md §3.1, `WIP_LIMIT`과 같은 계열)으로 종료하고, daemon/SDK는 `Retry-After`를 존중해 지수 백오프한다.
- 응답 본문 `details`에 `{limit, window_seconds, scope}`를 넣되 **다른 주체의 사용량은 노출하지 않는다**.

### 2.5 Long-poll / SSE 연결 한도

| 한도 | 기본(hosted) | 초과 시 |
|---|---|---|
| subscription당 consumer | 1 (현행, `409 CONSUMER_CONFLICT`) | 유지 |
| principal당 동시 long-poll/SSE | 5 | `429 CONNECTION_LIMIT` |
| workspace당 동시 연결 | 50 | `429 CONNECTION_LIMIT` |
| 인스턴스당 동시 연결 | `AK5_MAX_CONNECTIONS`(기본 500) | `503` + `Retry-After` |

- profile당 연결 1개 원칙(client.md, daemon 1:N fan-out)은 유지된다. 한도는 **비정상 클라이언트 방어**용이며 정상 daemon은 닿지 않아야 한다.
- 연결 수는 인스턴스 로컬 카운터로 세되(연결은 인스턴스에 묶여 있음), principal/workspace 한도는 다중 인스턴스에서 근사치로 충분하다. 정확한 전역 한도가 필요하면 §9-3.
- 연결은 `LONG_POLL_MAX_WAIT`(25초) 이내에 정상 종료되므로 서버리스에서도 동시 연결은 짧게 점유된다.

## 3. Quota (workspace 단위)

`workspaces.policy`(identity.md §6)에 키를 추가한다. `0`/미설정 = 무제한(local 기본).

| 키 | hosted 기본 | 측정 | 초과 시 |
|---|---|---|---|
| `max_boards` | 50 | board 수 | board 생성 거부 |
| `max_open_tickets` | 10,000 | `done`/archived 제외 ticket 수 | T1 create 거부 |
| `max_attachment_bytes_total` | 5 GiB | 첨부 바이트 합계 | 업로드 거부 |
| `max_members` | 50 | membership 수 | 초대/수락 거부 |
| `max_agents` | 50 | 활성 agent 수 | agent 발급 거부 |
| `event_retention_days` | 7 (최대 30) | 이벤트 보존 | 설정값으로 purge |

- 초과 시 `403 QUOTA_EXCEEDED`(신규) + `details {quota, limit, current}`. 이는 retryable이 **아니다**(사람이 정리하거나 한도를 올려야 함).
- 기존 `max_auto_boards`는 `max_boards`의 하위 한도로 유지한다.
- 집계는 쓰기 경로에서 `COUNT(*)` 또는 유지되는 카운터로 한다. 첨부 합계처럼 비싼 값은 `workspace_usage(workspace_id, key, value)` 테이블을 같은 트랜잭션에서 갱신한다(§9-4).
- 동시성 모델: 첨부 파일 용량 카운터는 DB 수준 원자적 upsert(`on_conflict_do_update` + dialect별 `GREATEST`/`MAX`)로 추적된다. 개수 기반 한도(board, open ticket, member, agent)는 workspace 격리 하의 check-then-create(동시성 최선 노력, 별도 비관적 락 미사용)로 동작한다.
- quota 값 변경은 **owner/admin**만(identity.md §5 정책 변경과 동일). 플랫폼 운영자가 올리는 hosted 상한은 서버 설정(`AK5_QUOTA_CEILING`)으로 별도 제한한다.
- 사용량 조회: `GET /workspaces/{id}/usage` → 각 quota의 `{limit, current}`. Web UI 설정 화면에 표시한다.
- **이벤트 보존 정책 (`event_retention_days`) 상세**:
  - `workspaces.policy.event_retention_days`: 워크스페이스별 이벤트 보존 기간(일 단위, 0..30). `0`/미설정은 무제한(local 기본, hosted 기본 7).
  - 보존 기간 경과 이벤트는 `ak5 db maintenance` 실행 시 또는 주기적 lazy purge를 통해 삭제된다.
  - **미-ack 구독 보호 (Safety Rule) 및 활성 구독 판정 (#149)**:
    - 해당 워크스페이스에 **활성 subscription(active subscription)**이 존재하는 경우, 활성 subscription들이 ack 완료한 최소 커서(`seq <= min(ack_cursor)`) 이전의 이벤트만 삭제된다. 아직 ack되지 않은 이벤트(`seq > min(ack_cursor)`)는 보존 기간이 경과했더라도 활성 구독자가 안전하게 처리할 때까지 보존된다.
    - **보존 정책의 활성 구독 조건**: 유효한 consumer lease를 보유하고 있거나(`consumer_lease_expires_at > now`), 임대 이력 없이 생성된 지 유예 시간(`ACTIVE_SUBSCRIPTION_GRACE_SECONDS` 또는 환경 변수 `AK5_ACTIVE_SUBSCRIPTION_GRACE_SECONDS`, 기본 300초/5분) 이내인 subscription. 임대가 만료되었거나 생성 후 5분 초과 미임대 상태인 비활성 subscription은 retention purge 차단 대상에서 즉시 제외되어 이벤트 로그의 영구 보존 팽창을 방지한다. 방치된 구독이 뒤늦게 폴링을 재개할 경우 이미 purge된 커서에 대해 `410 CURSOR_EXPIRED` 오류가 반환되며, 클라이언트는 resync를 통해 안전하게 최신 상태로 복구한다.
    - **Lag 메트릭의 방치 판정 윈도우 (게이지 분리, §4.3)**: `ak5_event_log_lag_seconds`는 장애/중단 컨슈머 감지 알람이 목적이므로, 임대가 만료된 컨슈머라도 즉시(60초 만에) 집계에서 제거되지 않고 방치 윈도우(`ABANDONED_SUBSCRIPTION_HOURS` 또는 환경 변수 `AK5_ABANDONED_SUBSCRIPTION_HOURS`, 기본 24시간) 동안은 계속 lag 게이지에 반영되어 경보를 유도한다. 임대 만료(또는 미임대 생성) 후 24시간을 초과한 영구 방치 구독만 게이지 집계에서 최종 제외된다.

## 4. 관측성

### 4.1 요청 추적 ID

- 모든 요청에 `request_id`를 부여한다: 들어온 `X-Request-ID`가 안전한 형식(`[A-Za-z0-9_-]{8,64}`)이면 수용, 아니면 새로 생성.
- **응답 헤더 `X-Request-ID`로 반환**하고, 로그와 audit 레코드, 이벤트 `data.request_id`(선택)에 남긴다. CLI/SDK는 오류 출력에 request id를 포함해 지원 요청 시 추적할 수 있게 한다.

### 4.2 구조화 로그

- `AK5_LOG_FORMAT=json|text`(local 기본 `text`, hosted 기본 `json`), `AK5_LOG_LEVEL`.
- 필수 필드: `ts, level, msg, request_id, method, path(템플릿화), status, duration_ms, principal_id, workspace_id, instance_id`. path는 라우트 템플릿(`/tickets/{ticket_id}`)을 쓰고 쿼리스트링은 남기지 않는다.
- 접근 로그는 샘플링 없이 남기되, health check 경로는 제외한다.

### 4.3 메트릭

`GET /metrics` (Prometheus 텍스트). 최소 집합:

| 메트릭 | 타입 | 라벨 |
|---|---|---|
| `ak5_http_requests_total` / `ak5_http_request_duration_seconds` | counter / histogram | route, method, status |
| `ak5_workflow_transitions_total` | counter | op, from_stage, to_stage |
| `ak5_claim_conflicts_total` | counter | board_id 제외 (카디널리티) |
| `ak5_lease_expirations_total` | counter | |
| `ak5_escalations_total` | counter | kind |
| `ak5_rate_limited_total` | counter | rule |
| `ak5_quota_rejections_total` | counter | quota |
| `ak5_active_connections` | gauge | kind(longpoll/sse) |
| `ak5_event_log_lag_seconds` | gauge | 가장 오래된 미-ack subscription 기준 |
| `ak5_db_pool_in_use` | gauge | |

- **라벨에 workspace_id/ticket_id/principal을 쓰지 않는다**(카디널리티 폭발·프라이버시). workspace별 사용량은 §3 usage API로 본다.
- `/metrics`는 기본 비활성(`AK5_METRICS_ENABLED=1`로 켬). 켜면 **관리 토큰(`AK5_METRICS_TOKEN`) 또는 내부 bind에서만** 접근 가능하고, 공개 인터페이스에서는 인증 없이 열리지 않는다.
- 서버리스에서는 pull 모델이 맞지 않으므로 로그 기반 메트릭(JSON 로그 → 플랫폼 집계)을 1차로 하고 `/metrics`는 상시 서버용이다.
- **단일 프로세스/컨테이너 수집 모델과 멀티 인스턴스 집계 (#132)**:
  - **컨테이너당 단일 프로세스 원칙 (Scale by Replicas)**: `/metrics`는 인프로세스 메모리 레지스트리를 사용하므로, 단일 포트 뒤에 여러 워커 프로세스를 띄우는 구조(`uvicorn --workers N`, `WEB_CONCURRENCY`, gunicorn)는 스크랩 요청이 임의의 워커로 분산되어 카운터 진동/리셋 착시가 발생하므로 지원하지 않는다(멀티프로세스 공유 디렉터리 스킴이 별도로 도입되지 않는 한 금지). hosted/메트릭 활성화 배포에서는 **컨테이너/포트당 1개 서버 프로세스**를 실행하고, 수평 확장은 **컨테이너 레플리카(인스턴스) 단위**로 수행한다.
  - **인스턴스별 스크랩**: Prometheus는 각 컨테이너 인스턴스의 `/metrics`를 개별 스크랩하며, 인스턴스 식별 라벨(`instance`)은 스크랩 시 Prometheus 서버가 부여하여 내부 라벨 카디널리티 규칙(workspace_id 등 금지)을 유지한다.
  - **클러스터 수준 합산**: 대시보드 및 경보는 PromQL 집계(예: `sum(rate(ak5_http_requests_total[5m])) by (route, status)`)를 통해 전체 레플리카 기준으로 합산한다.
  - **Safe-emit 패턴**: 모든 도메인 로직(전이, 락/클레임, 리스 회수 등)과 미들웨어는 `safe_inc`, `safe_set`, `safe_dec`, `safe_observe`를 통해 안전하게 방출한다. 라벨/인자 오류(`ValueError`)는 경고(`WARNING`) 로그로 남기고 일반 방출 예외는 `DEBUG` 로그로 기록하여 비즈니스 트랜잭션에 영향을 주지 않는다.

### 4.4 로깅 금지 항목

토큰(`ak5_agt_…`, JWT, claim_token, session/csrf), `Authorization`/`Cookie` 헤더, OIDC code/state, WebAuthn credential, 첨부 내용·파일명, ticket 본문. 필요 시 앞 4자 + 길이만 남긴다. 예외 트레이스에서도 요청 본문을 포함하지 않는다.

### 4.5 Health

- `GET /healthz`: 프로세스 생존(의존성 확인 안 함).
- `GET /readyz`: DB 연결 + 마이그레이션 head 일치 + blob 백엔드 접근 확인. 실패 시 503. 둘 다 인증 불필요, 상세 오류는 노출하지 않는다.

## 5. 백업·복구

| 대상 | local / self-hosted | hosted |
|---|---|---|
| DB (SQLite) | 현행 `db_safety` 스냅샷·복원 유지 | — |
| DB (Postgres) | 관리형 PITR(Neon/RDS) **권장**, 보조로 논리 백업 `ak5 db export`(deployment.md §1.4)를 cron | PITR 필수 |
| 첨부(blob) | 디렉터리/버킷 단위 백업. S3는 버전 관리 + lifecycle | 버킷 버전 관리 필수 |
| 비밀(JWT secret, OIDC client secret) | DB 백업과 **분리** 보관 | 시크릿 매니저 |

복구 시 지켜야 할 규칙:

- **DB와 blob의 시점 불일치**를 허용한다: DB에 있는데 blob이 없는 첨부는 다운로드 시 `404 ATTACHMENT_MISSING`로 처리하고 상태를 깨뜨리지 않는다. blob만 있고 DB에 없는 객체는 `blob_gc`가 정리한다(deployment.md §2).
- 이벤트 로그가 과거로 되돌아가면 daemon의 `ack_cursor`가 서버 `seq`보다 커질 수 있다. 서버는 이를 `410 CURSOR_EXPIRED`로 취급해 daemon이 `WORK_AVAILABLE`로 **재동기화**하게 한다(events.md §5와 동일 경로).
- 활성 claim의 lease는 복구 후 만료로 간주되어 정상 경로(T8)로 회수된다.
- 복구 절차는 runbook(`docs/`)에 단계별로 문서화하고, **복구 리허설**(빈 DB에 복원 후 `readyz` + 스모크)을 릴리스 체크리스트(#36)에 넣는다.
- RPO/RTO 목표는 서비스 수준 결정이므로 §9-5.

## 6. 악용 방지

| 위협 | 대책 |
|---|---|
| device grant `user_code` 무차별 대입 | 현행 실패 한도(10분 20회)를 **공유 저장소**로 이전(deployment.md §6). IP 단위 + `device_code` 단위 병행. 코드 엔트로피(≈40bit)와 TTL은 유지 |
| 로그인 시도 폭주 | IP당 §2.3 한도(20/분). local owner 비밀번호 로그인은 loopback 전용(현행)에 더해 5회 초과 연속 실패 시 지수 지연(2s, 4s, 8s… 최대 600s) |
| 초대 남용(스팸·열거) | workspace당 pending 초대 상한(기본 100), 같은 이메일 재초대 쿨다운(10분), 초대 토큰 만료(7일)·1회용, 수락 시 응답은 무효/만료/사용 완료/취소 여부를 구분하지 않고 동일한 오류 반환 |
| agent credential 남발 | `max_agents` quota + 발급 rate limit (principal당 20/분). 폐기 즉시 효력(현행) |
| passkey 등록 악용 | #99(기존 passkey step-up)로 추적. 본 spec은 등록·삭제를 audit/메트릭 대상에 포함 |
| OIDC pending 밀어내기 | 상한 방식 금지, TTL + IP별 상한(deployment.md §6) |
| 대용량 업로드 | 파일당 크기(현행) + workspace 총량 quota + 업로드 rate limit |

모든 차단/한도 이벤트는 `ak5_rate_limited_total` 등 메트릭과 로그(`request_id`, 키 종류)로 남기되 **차단 사유를 공격자에게 구분해 알려주지 않는다**(예: 존재하는 초대/없는 초대를 같은 응답으로).

## 7. 설정 요약과 프로필

| 변수 | local 기본 | hosted 기본 |
|---|---|---|
| `AK5_PROFILE` | `local` | `hosted` (아래 기본값 묶음 선택) |
| `AK5_RATE_LIMITS` | 비활성 | §2.3 표 |
| `AK5_TRUSTED_PROXIES` | 비어 있음 | 배포 환경에 맞게 필수 |
| `AK5_MAX_CONNECTIONS` | 무제한 | 500 |
| `AK5_QUOTA_CEILING` | 없음 | 서버 상한 JSON |
| `AK5_LOG_FORMAT` / `AK5_LOG_LEVEL` | `text` / `INFO` | `json` / `INFO` |
| `AK5_METRICS_ENABLED` / `AK5_METRICS_TOKEN` | 꺼짐 | 선택 |

`AK5_PROFILE=hosted`인데 `AK5_TRUSTED_PROXIES`가 비어 있고 비-loopback bind면 시작을 거부한다(IP 단위 한도가 무의미해지므로).

## 8. 구현 순서 (확정 시 이슈 분할 제안)

1. 요청 ID + 구조화 로그 + `healthz/readyz` (의존 없음, 동작 불변)
2. 공유 카운터 저장소(`rate_counters`) + rate limit 미들웨어 + `RATE_LIMITED` 계약(cli.md/client.md 갱신)
3. 기존 메모리 한도(device grant)를 공유 저장소로 이전 — deployment.md §6과 같은 PR 묶음
4. 연결 한도(§2.5)
5. quota 키 + `QUOTA_EXCEEDED` + usage API + Web UI 표시
6. 메트릭(`/metrics`)
7. 백업 runbook + 복구 리허설 스크립트

1은 #20 구현 전에도 진행 가능하다. 2·3은 deployment.md의 공유 저장소 결정(#20 §9)에 의존한다.

## 9. 결정 사항

| # | 질문 | 결정 |
|---|---|---|
| 1 | rate limit 저장소 | ✅ 확정(2026-10-19): **DB 테이블 기본**, Redis는 선택 어댑터 |
| 2 | 알고리즘 | ✅ 확정(2026-10-19): 고정 윈도우로 시작, 문제가 보이면 슬라이딩/토큰 버킷 |
| 3 | 연결 한도의 전역 정확도 | ✅ 확정(2026-10-19): 인스턴스 로컬 + principal/workspace는 근사치 허용 |
| 4 | quota 집계 방식 | ✅ 확정(2026-10-19): 가벼운 값은 `COUNT`, 첨부 합계는 `workspace_usage` 카운터 |
| 5 | RPO / RTO 목표 | ✅ **확정(2026-10-19): RPO ≤ 5분(PITR), RTO ≤ 1시간** |
| 6 | `/metrics` 노출 | ✅ 확정(2026-10-19): 기본 꺼짐, 켜면 토큰 또는 내부 bind만 |
| 7 | 요청 본문/첨부 내용 로깅 | ✅ 확정(2026-10-19): 영구 금지(§4.4) |
| 8 | hosted가 v2.0.0 범위인가 | ✅ **확정: 포함** (deployment.md §9-7, #36 "wave 5 포함 전체 2.0.0"과 일치). §8의 구현 순서 전체가 2.0.0 대상이며, 1번(요청 ID/로그/healthz)부터 진행 |
