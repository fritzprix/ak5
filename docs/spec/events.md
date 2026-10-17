# AK5 Events Spec — Server Event Log, Long-Poll, Client Daemon

Status: Draft · 2026-10-17 · 관련: [workflow.md](workflow.md), [cli.md](cli.md)

## 0. 원칙

1. **서버는 실행하지 않는다.** 서버는 이벤트를 기록하고 long-poll로 제공할 뿐이다. 셸 명령 실행(hook)은 클라이언트 daemon의 책임이다.
2. **이벤트는 힌트, claim이 진실이다.** 이벤트는 작업을 할당하지 않는다. 깨어난 harness는 반드시 `next` → `claim`으로 작업을 확보한다.
3. **local / remote 동일 경로.** 로컬 통합 모드도 loopback HTTP long-poll을 사용한다.

```
Server (local | Vercel | AWS)                     Client machine
┌──────────────────────────────┐   long poll    ┌───────────────────────────────┐
│ 상태 전이 ─► events (DB, seq)│ ◄───────────── │ ak5 daemon                    │
│ GET  /subscriptions/{id}/... │                │   필터 → hook 실행 → ack       │
│ POST /subscriptions/{id}/ack │ ─────────────► │   ├ exec: harness 깨우기       │
└──────────────────────────────┘                │   └ http: 로컬 webhook         │
                                                │        ▼                      │
                                                │ Agent harness ─► ak5 next ... │
                                                └───────────────────────────────┘
```

## 1. Event Log

- 테이블 `events(seq BIGINT 단조 증가, event_id, type, board_id, ticket_id, actor_id, data JSON, created_at)`.
- 상태 전이와 **같은 트랜잭션**에서 기록한다.
- 기존 in-memory event bus는 로컬 즉시 깨움(notify) 용도로만 남긴다.
- 보존 기간: 서버 설정 `EVENT_RETENTION` (기본 7일). 만료된 cursor 요청은 `410 CURSOR_EXPIRED`.

### 1.1 Event Types

| type | 발생 시점 | 주 소비자 |
|---|---|---|
| `WORK_AVAILABLE` | 티켓이 claim 가능 상태가 됨: inbox 진입, backlog 복귀, agent_review 진입, postmortem_pending 진입, unblock, lease 만료 | triage / worker / gatekeeper |
| `TICKET_TRANSITIONED` | 모든 stage 전이 | 관찰, 대시보드 |
| `TICKET_BLOCKED` / `TICKET_UNBLOCKED` | blocked 오버레이 변경 | 관찰 |
| `HUMAN_ACTION_REQUIRED` | human_review 진입, `needs_human` / `escalation` 블록 | 사람 알림 hook |
| `TICKET_CREATED`, `TICKET_UPDATED`, `COMMENT_ADDED` | 기존 이벤트 유지 | 하위호환 |

`WORK_AVAILABLE.data`:
```json
{ "board_id": "string", "stage": "string", "required_role": "triage|worker|gatekeeper",
  "capabilities": ["string"] }
```

전역 orchestrator는 `board=inbox, events=WORK_AVAILABLE`만 구독하면 된다.

## 2. Subscription (서버 리소스)

서버는 **필터와 ack cursor**만 저장한다. 셸 명령은 저장하지 않는다.

```json
{
  "subscription_id": "sub_…",
  "owner": "actor_id",
  "filter": { "board_id": "string|null", "events": ["…"], "role": "…", "capabilities": ["…"], "for_actor": "…" },
  "ack_cursor": 0,
  "consumer": { "consumer_id": "…", "lease_expires_at": "…" }
}
```

## 3. Long-Poll 계약

```
GET /subscriptions/{id}/events?cursor=<seq>&wait=<sec>&limit=<n>
Header: X-AK5-Consumer: <consumer_id>
```

- `cursor` 생략 시 서버의 `ack_cursor`부터.
- 이벤트가 있으면 즉시 반환, 없으면 `wait` 초까지 대기 후 빈 배열.
- `wait` 상한은 서버 설정 `LONG_POLL_MAX_WAIT` (기본 25초, 서버리스 실행 제한보다 짧게).
- 서버 구현: 로컬은 in-process notify로 즉시 깨움, 서버리스는 요청 내부에서 DB를 약 1초 간격으로 조회.

```json
200 { "events": [ { "seq": 1, "event_id": "…", "type": "…", "data": {} } ], "next_cursor": 1 }
```

### 3.1 Consumer Lease

- subscription 하나에 consumer 하나. long-poll 호출이 consumer lease를 갱신한다.
- 다른 consumer가 유효한 lease를 가진 상태로 접근하면 `409 CONSUMER_CONFLICT`.

### 3.2 Ack

```
POST /subscriptions/{id}/ack   { "cursor": <seq> }
```

- **ack cursor는 서버에 저장한다.** daemon은 무상태이며 재시작·이전 시 서버 cursor부터 재개한다.
- cursor는 단조 증가만 허용한다 (뒤로 가는 ack는 무시).

### 3.3 전달 보장

- **at-least-once.** 같은 `event_id`가 두 번 이상 전달될 수 있으며 hook은 멱등이어야 한다.
- 정합성은 claim의 원자성이 보장한다.

## 4. Daemon

### 4.1 설정 (`~/.ak5/daemon.toml`)

```toml
[[subscription]]
id = "sub_123"
hook = { exec = 'agent -p "$AK5_SUMMARY"' }   # 또는 { http = "http://localhost:7777/hook" }
concurrency = 1                               # 기본 순차
retry = { max = 3, backoff = "exp" }
```

hook 바인딩은 클라이언트에만 존재한다.

### 4.2 처리 루프

```
loop:
  batch = long_poll(sub, cursor=server_ack_cursor, wait=25)
  for event in batch:
      run hook (재시도 정책 적용)
      if 최종 실패: dead-letter 기록
      ack(event.seq)
```

### 4.3 Hook 계약

환경 변수 (기존 계약 유지 + 추가):

| 변수 | 의미 |
|---|---|
| `AK5_EVENT`, `AK5_EVENT_TYPE` | 이벤트 이름 / 타입 |
| `AK5_BOARD_ID`, `AK5_TICKET_ID`, `AK5_TITLE`, `AK5_STATUS`, `AK5_SUMMARY` | 티켓 요약 |
| `AK5_ACTOR_ID` | 깨워지는 세션의 actor (subscription 소유자) |
| `AK5_EVENT_ACTOR_ID` | 이벤트를 일으킨 actor |
| `AK5_DATA_JSON` | 이벤트 data |
| `AK5_EVENT_ID` *(신규)* | 중복 제거용 |
| `AK5_CURSOR` *(신규)* | 이벤트 seq |

stdin: `{"event": "...", "data": {...}}`. `http` hook은 같은 JSON을 POST body로 받는다.

### 4.4 실패 정책

- 재시도 소진 후 **dead-letter에 기록하고 ack하여 진행한다.** 실패한 hook 하나가 큐 전체를 막지 않는다.
- dead-letter: `~/.ak5/daemon/dead-letter.jsonl` (이벤트 원문, hook, 종료 코드, stderr 일부, 시각).
- 재처리: `ak5 daemon replay <event_id>`.

### 4.5 프로세스 모델

- daemon은 서버 앱 lifespan 안에서 실행하지 않는다 (uvicorn worker 수만큼 중복되는 문제).
- `ak5 serve --daemon`은 launcher 프로세스가 daemon을 **sibling 프로세스**로 실행한다.
- 서비스 등록: `ak5 daemon install` → systemd user unit / launchd plist.

## 5. 에러 코드

| 코드 | HTTP | 상황 |
|---|---|---|
| `CURSOR_EXPIRED` | 410 | 보존 기간이 지난 cursor. daemon은 `WORK_AVAILABLE` 재동기화로 복구 |
| `CONSUMER_CONFLICT` | 409 | 다른 consumer가 subscription lease 보유 |
| `SUBSCRIPTION_NOT_FOUND` | 404 | 삭제되었거나 소유자가 아님 |

## 6. 기존 구현에서의 이행

- 서버의 `--exec` 실행 코드(`subscription_service`의 실행 경로, CLI `run_subscription_loop`, `execute_subscriber_command`)는 daemon 패키지로 이동한다.
- 기존 `subscribe create --exec`는 `subscribe add --hook-exec`로 대체: 서버에는 필터를, 로컬 `daemon.toml`에는 hook을 저장한다.
- SSE(`events watch`)는 사람 터미널용 관찰 기능으로만 유지한다.
