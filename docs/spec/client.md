# AK5 Client Spec — CLI + Client Daemon, Permission Flow, Event Fan-out

Status: Draft · 2026-10-17 · 관련: [identity.md](identity.md), [events.md](events.md), [cli.md](cli.md)

## 0. 원칙

1. **클라이언트의 정본은 CLI + client daemon이다.** agent harness는 CLI를 호출하고, 사용법은 skill(`.agents/skills/ak5`)로 배운다.
2. **MCP는 선택적 어댑터다.** 기본 설치·기본 서버 구성에 포함하지 않는다 (§7).
3. **CLI는 무상태·단명**이고, 상태가 필요한 일(장기 credential 보관, 이벤트 수신)은 daemon이 맡는다.
4. **daemon은 서버의 1:1 long-poll을 로컬 1:N으로 확장한다.** 서버 subscription 하나(= consumer 하나)를 로컬의 여러 구독자에게 나눠 준다.

## 1. 구성 요소

```
┌──────────────── Client machine (OS user 단위) ─────────────────┐
│                                                                │
│  Agent harness ──(skill)──► ak5 CLI  ── HTTPS ──────────────────┼──► AK5 Server
│   (claude, cursor, …)          │  ▲                             │     (local | remote)
│                                │  │ access token                │       ▲
│                                ▼  │ (local IPC)                 │       │ long-poll 1:1
│                           ak5 daemon ───────────────────────────┼───────┘ (profile당)
│                            ├ credential store (profile별)       │
│                            ├ event spool (profile별)            │
│                            └ local subscribers (N)              │
│                               ├ hook exec  → curl (harness API)  │
│                               ├ hook exec  → CLI (claude -p, …)  │
│                               └ waiter     ← `ak5 events wait`   │
└────────────────────────────────────────────────────────────────┘
```

| 구성 요소 | 수명 | 책임 |
|---|---|---|
| `ak5` CLI | 명령 1회 | 서버 API 호출(정본 경로), 출력 계약(`--json`, 종료 코드) |
| `ak5 daemon` | 상주 (OS 사용자당 1개) | credential 보관·토큰 발급(broker), 이벤트 수신·spool·fan-out, hook 실행 |
| skill | 문서 | agent가 CLI를 올바르게 쓰는 방법 (pull 루프, 종료 코드 처리) |

CLI의 workflow 명령(`next`, `claim`, `submit`, …)은 **서버에 직접** 요청한다. daemon은 토큰과 이벤트만 중개하며 workflow 요청을 프록시하지 않는다.

## 2. Profile

profile = **(server URL, workspace, principal)** 한 쌍. 한 머신에서 여러 agent(triage, worker, gatekeeper)와 사람이 각자 profile을 가진다.

```toml
# ~/.ak5/profiles.toml   (credential은 여기 두지 않는다)
[profiles.me]
server = "https://ak5.example.com"
workspace = "ws_acme"
principal = "user"

[profiles.worker-1]
server = "https://ak5.example.com"
workspace = "ws_acme"
principal = "agent"
role = "worker"
```

- 선택 순서: `--profile` > `AK5_PROFILE` > `default_profile`.
- agent harness는 실행 환경에 `AK5_PROFILE=worker-1`만 받는다. 장기 credential은 harness 환경에 노출되지 않는다.

## 3. 접근 권한 획득 흐름

### 3.1 Agent 등록 (최초 1회, 사람의 승인 필요)

```
사람/설치 스크립트          ak5 CLI               ak5 daemon                AK5 Server              브라우저(사람)
     │ ak5 login --agent worker-1 --role worker --caps py --server URL
     │──────────────────────►│ ensure daemon ──────►│
     │                       │ IPC: begin_login ───►│ POST /oauth/device_authorization (principal=agent, name, role, caps)
     │                       │                      │──────────────────────────►│
     │                       │◄── user_code, verification_uri ─────────────────│
     │◄─ "브라우저에서 코드 ABCD-EFGH 승인" (가능하면 브라우저 자동 열기)
     │                       │                      │                          │◄── OIDC 로그인 + 승인 화면
     │                       │                      │                          │    (workspace / role / caps / board 범위 / 만료 확인)
     │                       │                      │ POST /oauth/token (폴링)  │
     │                       │                      │◄── agent credential ─────│
     │                       │                      │ credential store 저장 (profile worker-1)
     │                       │                      │ 서버 subscription 생성 (profile당 1개, §4.2)
     │                       │◄── done ─────────────│
     │◄─ "worker-1 등록됨 (role=worker, ws=acme)"
```

### 3.2 사람 로그인

`ak5 login [--profile me]` — 같은 device grant(`principal=user`). daemon이 refresh token을 보관하고, 만료(최대 12시간, identity.md §3.3) 후에는 브라우저 재로그인이 필요하다.

### 3.3 런타임 (매 명령)

```
AK5_PROFILE=worker-1 ak5 next --json
  1. CLI → daemon (IPC)  POST /v1/token {profile: "worker-1"}
  2. daemon: 캐시된 access token이 유효하면 반환, 아니면 credential로 서버에서 갱신
  3. CLI → Server        GET /api/v1/boards/…/next   (Authorization: Bearer <access, 15분>)
```

- CLI는 access token을 디스크에 쓰지 않는다 (프로세스 메모리에만).
- 서버가 401(폐기·만료)을 주면 daemon은 profile을 `revoked`로 표시하고 CLI는 exit 8.

### 3.4 daemon 없이 (CI·임시 환경)

- `AK5_TOKEN`(UI에서 발급한 agent 토큰, identity.md §4.2)이 있으면 CLI가 직접 사용한다.
- 이벤트 fan-out은 없다 → `ak5 events poll`(1회 조회)만 사용.

### 3.5 Credential 저장소

- daemon만 접근한다. OS keyring(Secret Service / macOS Keychain)을 우선 사용하고, 없으면 `~/.ak5/credentials/<profile>` (디렉터리 0700, 파일 0600).
- 같은 OS 사용자의 프로세스는 이 파일을 읽을 수 있다 — local 위협 모델은 identity.md §8.2를 따른다.

## 4. 이벤트 fan-out (1 → N)

### 4.1 구조

```
Server subscription (profile당 1개, consumer = daemon)
        │ long-poll 1:1 (events.md §3)
        ▼
daemon ── 수신 배치를 profile spool에 기록 (fsync) ── 서버에 ack(next_cursor)
        │
        ├─ local subscriber A (filter, hook exec)    local cursor A
        ├─ local subscriber B (filter, hook exec)    local cursor B
        └─ waiter (ak5 events wait)                  요청별 cursor
```

### 4.2 서버 subscription

- **profile당 정확히 1개**. daemon이 profile 등록 시 만들고 `subscription_id`를 profile 상태에 저장한다.
- 서버 필터는 profile의 board 범위(+ 해당 role의 `WORK_AVAILABLE`, `HUMAN_ACTION_REQUIRED` 등 로컬 구독자들이 쓰는 타입의 합집합)로 설정한다. 세밀한 필터링은 daemon이 로컬에서 한다.
- 서버 ack는 **spool에 기록한 직후** `next_cursor`로 한다. 로컬 구독자의 처리 성공과는 분리한다 (서버는 1:1, 로컬 전달 보장은 daemon이 책임).

### 4.3 로컬 구독자 (local subscriber)

```
ak5 subscribe add [--profile worker-1] [--events WORK_AVAILABLE,…] [--board …]
                  --exec '<command>'
ak5 subscribe list | remove <id>
```

- 서버에 아무것도 만들지 않는다. daemon 상태(`~/.ak5/daemon/state.db`)에 `{id, profile, filter, hook, local_cursor}`로 저장.
- 구독자마다 독립된 local cursor, 재시도, dead-letter(events.md §4.4 정책 그대로)를 가진다. 한 구독자의 실패가 다른 구독자나 서버 ack를 막지 않는다.
- hook 계약(env/stdin)은 events.md §4.1 그대로.

#### Hook은 exec 하나로 통일

로컬 구독자는 **실행 가능한 명령 문자열(exec)** 로 저장한다. 별도의 http hook 타입은 없다. agent가 쓰는 harness에 따라 명령만 달라진다:

| harness 형태 | `--exec` 예 |
|---|---|
| HTTP API를 가진 harness (세션에 메시지 주입) | `curl -s -X POST http://127.0.0.1:3030/api/sessions/$SESSION/messages -H 'Content-Type: application/json' -d @-` |
| CLI형 harness | `claude -p "$AK5_SUMMARY"` · `agent -p "$AK5_SUMMARY"` |
| 임의 스크립트 | `~/bin/on-ak5-event.sh` |

- 명령은 셸로 실행하되 **이벤트 데이터는 env/stdin으로만** 전달하고 명령 문자열에 보간하지 않는다 (셸 주입 방지).
- stdin은 이벤트 JSON 원문이므로 `curl -d @-`로 그대로 전달할 수 있다.
- 자주 쓰는 형태는 CLI가 preset으로 생성할 수 있다 (`--preset claude|cursor|curl:<url>`). preset은 exec 문자열을 만들어 저장할 뿐이며, 저장 형태는 항상 exec다.

### 4.4 Waiter (pull 대기 원시 연산)

```
ak5 events wait [--profile …] [--events WORK_AVAILABLE] [--timeout 25] --json
ak5 next --wait <sec> [--board …]       # 할 일이 없으면 WORK_AVAILABLE을 기다렸다가 다시 next
```

- daemon의 로컬 long-poll(IPC)을 사용한다. 서버에 연결을 추가하지 않으므로 waiter가 여러 개여도 서버 연결은 profile당 1개다.
- hook 없이도 harness가 "할 일이 생길 때까지 기다림"을 CLI 한 줄로 표현할 수 있다.
- daemon이 없으면 `ak5 next --wait`는 `ak5 events poll` 기반의 단순 폴링으로 대체한다 (간격 ≥ 5초).

### 4.5 Spool

- profile별 append-only (SQLite 또는 JSONL), 보존: 모든 로컬 구독자의 cursor를 지난 항목 중 7일 또는 용량 상한 초과분 삭제.
- daemon 재시작 시 spool과 local cursor로 재개하고, 서버는 저장된 ack cursor부터 이어서 보낸다.
- 서버 `CURSOR_EXPIRED` → spool에 `RESYNC` 마커 기록, 구독자에게 `RESYNC` 이벤트 전달 (harness는 `ak5 next`로 재동기화).

## 5. Local IPC

- Unix domain socket: `$XDG_RUNTIME_DIR/ak5/daemon.sock` (없으면 `~/.ak5/run/daemon.sock`). 디렉터리 0700, 소켓 0600, `SO_PEERCRED`(Linux)/`getpeereid`(macOS)로 같은 uid만 허용.
- 프로토콜: UDS 위의 HTTP/1.1 + JSON (httpx `UDS` transport로 CLI 구현 단순화).

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/v1/status` | daemon 버전, profile 상태, 연결 상태 |
| POST | `/v1/login` | device grant 시작 → `user_code`, `verification_uri`, `login_id` |
| GET | `/v1/login/{login_id}` | 승인 대기 (long-poll) |
| POST | `/v1/token` | `{profile}` → `{access_token, expires_at}` |
| POST·GET·DELETE | `/v1/subscribers` | 로컬 구독자 관리 |
| GET | `/v1/events?profile&cursor&wait&events` | waiter용 로컬 long-poll |
| POST | `/v1/logout` | `{profile}` 서버 폐기 + 로컬 삭제 |

- Windows(named pipe)는 범위 밖.

## 6. 수명 주기

- `ak5 daemon start | stop | status | run | install | logs | replay` (cli.md §4.6).
- CLI가 daemon을 필요로 하는데 실행 중이 아니면 **자동 시작**한다 (`AK5_DAEMON_AUTOSTART=0`으로 끔). `ak5 daemon install`은 systemd user unit / launchd plist로 상주 등록.
- `ak5 serve --daemon`은 같은 daemon을 sibling 프로세스로 띄운다 (cli.md §4.1). 이미 실행 중이면 재사용.
- daemon 1개가 여러 서버·workspace·principal의 profile을 동시에 관리한다.

## 7. MCP (선택)

- 패키지 extra `ak5[mcp]`로만 설치된다. 기본 `ak5`에는 MCP 의존성이 없다.
- 서버의 `/mcp` 마운트는 `ak5[mcp]` 설치 + `AK5_ENABLE_MCP=1`일 때만 활성화한다.
- MCP 어댑터를 쓰더라도 인증은 daemon의 `/v1/token`(profile)을 사용하며, agent 전용 표면 원칙(cli.md §0-2)을 따른다.
- 문서·skill·예제의 기본 경로는 CLI다.

## 8. 오류와 종료 코드

| 상황 | CLI 종료 코드 |
|---|---|
| daemon 미실행 + 자동 시작 실패 | 9 (`DAEMON_UNAVAILABLE`) |
| profile 없음 / 로그인 필요 / 폐기됨 | 8 |
| `events wait` 타임아웃 | 6 (할 일 없음과 동일) |
| login 승인 거부 / 만료 | 8 |
