# AK5 CLI Spec — Single Entry Point, Agent Contract

Status: Draft · 2026-10-17 · 관련: [workflow.md](workflow.md), [events.md](events.md)

## 0. 원칙

1. **단일 entry point `ak5`**: server, client, daemon을 한 명령으로 제공한다. 묶이는 범위는 사용자 경험까지이며, 코드·의존성·프로세스는 분리한다.
2. **REST가 정본**: CLI와 MCP는 같은 동사 집합의 얇은 어댑터다. 단 **MCP는 agent 전용 표면**이다 — LLM이 조작하는 인터페이스이므로 사람 전용 동사(human verdict T15/T16, `reopen`, `reroute`, `needs_human`/`escalation` 해제, T1 `create`)는 MCP tool로 노출하지 않는다. 사람의 최종 승인은 CLI(user principal) 또는 Web UI로만 한다.
3. **루프는 외부**: CLI는 외부 루프가 호출하는 원자적 명령 집합이다. 루프 러너를 제공하지 않는다.
4. **평평한 동사**: role별 명령 그룹을 만들지 않는다. 권한은 서버가 role로 강제한다.

## 1. 패키징과 계층

| 설치 | 포함 | 용도 |
|---|---|---|
| `ak5` | contracts, client, daemon | agent 머신, 원격 서버 사용자 |
| `ak5[mcp]` | + MCP 어댑터 (선택, [client.md](client.md) §7) | MCP 호스트를 꼭 써야 하는 경우 |
| `ak5[server]` | + server (FastAPI, SQLAlchemy, uvicorn, SQLite) | 로컬 통합 모드, 자체 호스팅 |
| `ak5[postgres]` | + Postgres 드라이버 | Vercel / AWS |

```
ak5.contracts   스키마, 에러 코드, API 버전        (의존성 없음에 가깝게)
   ▲      ▲
ak5.client  ak5.daemon                          (httpx, click)
   ▲
ak5.cli         명령 정의 (server 명령은 지연 import)
ak5.server      FastAPI app, DB, 상태 머신       (contracts만 import)
```

- client / daemon은 server를 import할 수 없다 (import-linter로 CI에서 강제).
- 원격 배포의 정본 진입점은 ASGI factory `ak5.server.app:create_app()`. `ak5 serve`는 그 래퍼다.

## 2. 버전 협상

- `GET /meta` → `{ "api_version": "2", "min_client": "2.0.0", "capabilities": ["long_poll", "local_exec"] }`
- CLI는 서버와 처음 통신할 때 확인하고, 호환되지 않으면 `exit 7 VERSION_MISMATCH`.

## 3. 공통 출력 계약

- 모든 명령은 `--json`을 지원한다. `--json`일 때 stdout에는 JSON만 출력한다.
- 에러 출력: `{ "error": { "code": "INVALID_TRANSITION", "message": "...", "details": {} } }`
- 환경 변수 `AK5_OUTPUT=json`으로 기본값을 바꿀 수 있다.

### 3.1 종료 코드

| exit | 의미 | 대응하는 서버 코드 |
|---|---|---|
| 0 | 성공 | — |
| 1 | 기타 오류 (네트워크 등) | — |
| 2 | 사용법 오류 | `PAYLOAD_INVALID` |
| 3 | 경합 · 일시적 용량 부족 (재시도 가능) | `CLAIM_CONFLICT`, `STALE_CLAIM`, `CONSUMER_CONFLICT`, `WIP_LIMIT` |
| 4 | 전이 / 권한 거부 / 없음 | `INVALID_TRANSITION`, `ROLE_FORBIDDEN`, `SEPARATION_OF_DUTIES`, `SYSTEM_BOARD`, `NOT_FOUND` |
| 5 | blocked | `TICKET_BLOCKED` |
| 6 | 할 일 없음 | `next` 결과 없음 |
| 7 | 버전 불일치 | `VERSION_MISMATCH` |
| 8 | 인증 필요 / 만료 | 401 |

## 4. 명령 트리

### 4.1 Server

```
ak5 serve [--host 127.0.0.1] [--port 8000] [--ui] [--daemon]
          [--tailscale-serve] [--ssl-keyfile ..] [--ssl-certfile ..] [--reload]
```

- 기본 bind는 `127.0.0.1`. `0.0.0.0`은 명시적으로 지정해야 하며, 인증이 꺼져 있으면 시작을 거부한다.
- `--daemon`: launcher가 daemon을 sibling 프로세스로 함께 실행한다 (로컬 통합 모드).
- `ak5 web`은 `ak5 serve --ui`의 deprecated alias.

### 4.2 접속 / 신원

```
ak5 login [<url>] --id .. --type human|agent --role triage|worker|gatekeeper|human --caps ..
ak5 logout | whoami
```

`--role`은 위 네 값으로 제한한다.

### 4.3 작업 루프용 동사 (외부 루프가 호출)

```
ak5 next [--board <id>|--inbox] [--caps ...]
ak5 ticket claim      <id>
ak5 ticket heartbeat  <id> [--token T]
ak5 ticket release    <id> [--token T] --reason ..
ak5 ticket submit     <id> [--token T] --deliverable @file.json|-
ak5 ticket bounce     <id> [--token T] --reason ..
ak5 ticket route      <id> [--token T] --plan @route.json|-
ak5 ticket verdict    <id> [--token T] --approve|--reject [--misrouted] --findings @f.json|- [--reason ..]
ak5 ticket postmortem <id> [--token T] --body @pm.json|-
```

- `next`는 해당 티켓을 반환할 뿐 claim하지 않는다. 결과가 없으면 exit 6.
- `--token` 생략 시 `claim` 때 저장한 로컬 token(`~/.ak5/claims/<ticket_id>`)을 사용한다.
- 구조화된 payload는 `@file.json` 또는 `-`(stdin). 스키마는 workflow.md §5.

### 4.4 사람 / 공통 동사

```
ak5 ticket create  [--board <id> --spec .. --criteria ..] <title|request>
ak5 ticket block   <id> --kind needs_human|dependency|external --reason ..
ak5 ticket unblock <id> [--resolution ..]
ak5 ticket reopen  <id> --reason ..
ak5 ticket reroute <id> --to inbox|<board> --reason ..
ak5 ticket view | list | history | update | comment | attach | download-attachment | archive | unarchive
ak5 ticket move    <id> <column>        # 같은 stage 안의 이동, 또는 human 전이 명령에 대응되는 이동만
```

- `create`: `--board` 없으면 inbox로 (T1), 있으면 `create_direct` (T2).
- `history`: revision, review, postmortem, audit 이력.

### 4.5 Board

```
ak5 boards [--query ..]
ak5 board <id> [--watch]
ak5 board create <id> --template <name> ..
ak5 board adopt  <id>              # 기존 claim-board (ticket claim과의 이름 충돌 해소)
ak5 template list | show <name>    # 서버 GET /templates (workflow.md §2.2)
ak5 template apply <board> <name>  # 누락 stage 컬럼만 보강, 삭제 없음
```

### 4.6 Subscription / Daemon

```
ak5 login [--profile P] | ak5 login --agent <name> --role R [--caps ..] [--profile P]   # client.md §3
ak5 profile list | use <P>

ak5 subscribe add [--profile P] [--board ..] [--events ..]                  # 로컬 구독자, hook=exec (client.md §4.3)
                  --exec '<command>' [--preset claude|cursor|curl:<url>]
ak5 subscribe list | remove <id>

ak5 events wait [--profile P] [--events ..] [--timeout 25]                  # daemon 로컬 long-poll (client.md §4.4)
ak5 next --wait <sec> [--board ..]

ak5 daemon run | start | stop | status
ak5 daemon install
ak5 daemon logs [--dead-letter]
ak5 daemon replay <event_id>

ak5 events poll [--board <id>] --since <cursor>   # 1회 조회, 서버리스 호환
ak5 events watch <board>                          # 사람 터미널용 SSE
```

### 4.7 기타 (유지)

```
ak5 agents | demo | reset | migrate-legacy
ak5 mcp                                  # ak5[mcp] 설치 시에만 (선택)
```

## 5. 기존 명령 이행표

| 현재 | 변경 |
|---|---|
| `ticket create` | 기본 도착지 inbox |
| `ticket move` | 워크플로우 진행 수단이 아님. stage 변경은 전이 명령으로 |
| `ticket block` | `--kind` 추가, claim 해제 효과 |
| `delegate --to` | 부모가 있는 `create_direct`로 backlog에 생성. `--to` 제거 (pull 모델) |
| `login --role` | 4개 값으로 제한 |
| `claim-board` | `board adopt` |
| `create-board` | `board create` |
| `subscribe create --exec` | `subscribe add --exec` (daemon 로컬 구독자, 서버에는 profile당 subscription 1개) |
| `web` | `serve --ui` alias |
| `move`, `comment` (top-level shortcut) | 유지 |

## 6. 도움말 / 스킬

- `ak5 --help`의 agent happy path를 pull 모델로 교체:
  ```
  ak5 login --agent worker-1 --role worker --caps ..      # 사람이 브라우저에서 승인 (client.md §3.1)
  export AK5_PROFILE=worker-1
  # 루프: ak5 next --wait 25 --json → ak5 ticket claim → 작업 → ak5 ticket submit
  # 또는 깨우기 hook: ak5 subscribe add --events WORK_AVAILABLE --exec '<curl 또는 harness CLI>' 
  ```
- "poll loop 금지" 문구 제거. `.agents/skills/ak5/SKILL.md`도 같은 흐름으로 재작성.
- **CLI + skill이 1순위 통합 경로**이고 MCP는 선택 부록으로만 문서화한다.
