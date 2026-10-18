# AK5 — 사용·운영 매뉴얼 (v2)

> **대상:** AK5 서버를 운영하는 사람, 웹 UI로 요청을 넣고 최종 승인하는 사람, agent harness를 붙이는 개발자
> **정본:** 동작의 정의는 `docs/spec/`의 spec 문서([workflow](spec/workflow.md) · [events](spec/events.md) · [cli](spec/cli.md) · [client](spec/client.md) · [identity](spec/identity.md))이다. 이 매뉴얼은 사용법을 설명하며, 둘이 다르면 spec이 맞다.

---

## 1. 개요

AK5는 **agent 루프가 붙어 일하고, 사람이 최종 승인하는 칸반 플랫폼**이다.

- **서버는 기록하고 강제한다.** stage, claim(lease), role별 허용 전이, 이력을 서버가 관리한다. 클라이언트는 stage를 직접 쓰지 못하고, 정해진 명령(동사)으로만 티켓을 진행시킨다.
- **agent는 일을 끌어간다 (pull).** `ak5 next`로 할 일을 찾고 `ak5 ticket claim`으로 가져간다. 담당자를 미리 지정하는 push 할당은 없다.
- **done은 사람만 만든다.** gatekeeper agent의 승인 뒤에도 사람의 최종 verdict가 있어야 `done`이 된다.
- **client daemon이 실행한다.** agent 머신의 `ak5 daemon`이 credential을 보관하고, 서버 이벤트를 받아 로컬 hook(harness 깨우기)을 실행한다. 서버는 클라이언트 명령을 실행하지 않는다.

```
┌──────────────── agent 머신 (OS 사용자 단위) ────────────────┐
│                                                              │
│  Agent harness ──(skill)──► ak5 CLI ── HTTPS ────────────────┼──► AK5 서버 (ak5 serve)
│  (claude, cursor, 스크립트)     │ ▲                           │     ├ REST API (/api/v1)
│                                ▼ │ access token (UDS)        │     ├ 웹 대시보드
│                           ak5 daemon ── long-poll (profile당 1) ──┤ ├ OAuth device grant / OIDC
│                            ├ credential (profile별)          │     └ SQLite (WAL)
│                            ├ event spool                     │
│                            └ local subscriber → hook exec    │
└──────────────────────────────────────────────────────────────┘
          브라우저(사람): 웹 UI 로그인, agent 등록 승인, 최종 승인
```

| 구성 요소 | 하는 일 |
|---|---|
| `ak5 serve` | API 서버 + 웹 대시보드 (같은 포트). `ak5[server]` extra 필요 |
| 웹 UI | 요청 등록, 보드 보기, 최종 verdict, blocked 처리, workspace·멤버·agent 관리, device 승인 |
| `ak5` CLI | 모든 동사의 단일 진입점. 명령 1회 실행 후 종료 (무상태) |
| `ak5 daemon` | profile별 credential 보관과 토큰 발급, 이벤트 수신·spool·fan-out, hook 실행 |
| skill | agent가 CLI를 올바르게 쓰는 방법 ([skills/ak5/SKILL.md](../skills/ak5/SKILL.md)) |

---

## 2. 설치와 빠른 시작

### 2.1 설치

| 설치 | 포함 | 쓰는 곳 |
|---|---|---|
| `pip install ak5` | CLI + client daemon | agent 머신, 원격 서버 사용자 |
| `pip install 'ak5[server]'` | + 서버(FastAPI, SQLite), 내장 웹 대시보드 | 서버를 띄우는 머신 |
| `pip install 'ak5[mcp]'` | + MCP 어댑터 (선택, §8) | MCP만 쓸 수 있는 호스트 |

`uv tool install 'ak5[server]'`도 같다. 설치 없이 실행하려면 `uvx --from 'ak5[server]' ak5 serve --ui`.

### 2.2 서버 시작

```bash
export AK5_AUTH_PASSWORD='원하는-비밀번호'   # 내장 local owner 계정 (loopback 로그인 전용)
ak5 serve --ui                               # http://127.0.0.1:8000 (API + 대시보드)
```

- 브라우저에서 위 비밀번호로 로그인한다. OIDC를 설정하지 않았다면 이 local owner가 유일한 사람 계정이다.
- 첫 실행 시 `local` workspace, 시스템 **inbox** 보드, `proj-core-engine` 보드(템플릿 `default`)가 만들어진다.
- 데이터는 OS 앱 데이터 디렉터리에 저장된다: Linux `~/.local/share/ak5/`, macOS `~/Library/Application Support/ak5/`, Windows `%LOCALAPPDATA%\ak5\`. `AK5_DATA_DIR`로 바꿀 수 있다.
- `ak5 serve --daemon`은 같은 머신의 client daemon을 함께 띄운다 (서버와 agent를 한 머신에서 돌릴 때).

API 문서는 `http://127.0.0.1:8000/docs`에 있다.

### 2.3 사람과 agent 로그인

한 머신 안의 신원마다 **profile**을 하나씩 만든다. `ak5 login`은 코드와 승인 URL(`http://127.0.0.1:8000/device?code=…`)을 출력한다. 로그인된 브라우저에서 그 URL을 열고 승인하면 CLI가 끝난다.

```bash
ak5 login --profile me                                   # 사람 (user principal)
ak5 login --agent triage-1 --role triage
ak5 login --agent worker-1 --role worker --caps py,web
ak5 login --agent gk-1     --role gatekeeper
ak5 profile list
```

- agent 승인 화면에서 workspace, workflow role, capabilities, board 범위를 확인·수정한다. 승인자는 workspace `admin` 이상이어야 한다.
- 원격 서버라면 `--url https://ak5.example.com/api/v1`을 붙인다 (기본값 `http://127.0.0.1:8000/api/v1`).
- daemon은 필요할 때 자동으로 시작된다 (`ak5 daemon status`). credential은 `~/.ak5/credentials/`에 저장되고 harness 환경에는 노출되지 않는다.

### 2.4 루프 한 번 돌려 보기

```bash
# 사람: 요청 등록 → inbox
AK5_PROFILE=me ak5 ticket create "리포트 페이지에 CSV 내보내기 추가"

# triage: 구체화해서 보드로 보낸다
export AK5_PROFILE=triage-1
ak5 next --inbox --json
ak5 ticket claim TK-001
ak5 ticket route TK-001 --plan @route.json

# worker: 가져가서 일하고 제출
export AK5_PROFILE=worker-1
ak5 next --board proj-core-engine --wait 25
ak5 ticket claim TK-001
ak5 ticket submit TK-001 --deliverable @deliverable.json

# gatekeeper: 검토
export AK5_PROFILE=gk-1
ak5 ticket claim TK-001
ak5 ticket verdict TK-001 --approve --findings @findings.json

# 사람: 최종 승인 (웹 UI의 티켓 상세에서도 가능)
AK5_PROFILE=me ak5 ticket verdict TK-001 --approve --reason "확인"
```

payload JSON의 형식은 §5.4에 있다. 실제 운영에서는 각 agent harness가 자기 부분의 루프를 반복한다.

---

## 3. 워크플로우

### 3.1 Role

| role | 주체 | 할 수 있는 일 |
|---|---|---|
| `human` | 사람 (user principal) | inbox에 요청 등록, 보드에 직접 등록, 최종 verdict, reopen, reroute, `needs_human`/`escalation` 해제 |
| `triage` | agent | inbox 티켓 claim, 구체화 후 보드로 route (필요하면 보드 생성) |
| `worker` | agent | backlog 티켓 claim, heartbeat, submit, release, bounce |
| `gatekeeper` | agent | `agent_review` 검토(verdict), 사람이 반려한 티켓의 postmortem 작성 |

role은 토큰으로 판정한다: 사람은 workspace 멤버면 `human`, agent는 등록 시 승인된 `workflow_role`이다. 권한이 없는 동사는 서버가 거부한다 (exit 4).

### 3.2 Stage와 전이

```
                         ┌───────────────── reject (gatekeeper) ─────────────────┐
                         ▼                                                        │
 inbox ───route───► backlog ───claim───► in_progress ───submit───► agent_review ──┴──approve──► human_review ──approve──► done
 (사람이 등록)      (triage)     ▲      (worker)                     (gatekeeper)                   (사람)
                                │                                                                     │ reject
                                └──────── postmortem (gatekeeper) ◄──── postmortem_pending ◄──────────┘
```

| 명령 | 누가 | 결과 |
|---|---|---|
| `ticket create "<요청>"` | 사람 | inbox의 `unclassified` (원래 요청은 바뀌지 않게 보존) |
| `ticket create --board <id> --spec … --criteria …` | 사람, triage | 해당 보드 `backlog` |
| `ticket route --plan` | triage (claim 보유) | 대상 보드 `backlog` |
| `ticket claim` | worker | `backlog` → `in_progress` (claim token, 5분 lease) |
| `ticket heartbeat` / `release` | worker | lease 연장 / `backlog`로 반납 |
| `ticket submit --deliverable` | worker | `agent_review` |
| `ticket bounce` | worker | inbox로 되돌림 (잘못 온 작업) |
| `ticket verdict --approve/--reject --findings` | gatekeeper | `human_review` / `backlog` (자기가 제출한 티켓은 검토 불가) |
| `ticket verdict --reject --misrouted` | gatekeeper | inbox |
| `ticket verdict --approve` | 사람 | `done` (done으로 가는 유일한 경로) |
| `ticket verdict --reject --reason` | 사람 | `postmortem_pending` |
| `ticket postmortem --body` | gatekeeper | 수정된 spec으로 `backlog` |
| `ticket reopen` | 사람 | `done` → `postmortem_pending` |
| `ticket reroute --to inbox\|<board>` | 사람 | inbox 또는 다른 보드 `backlog` |

- lease가 만료되면 서버가 claim을 회수하고 티켓을 다시 큐에 넣는다.
- gatekeeper 반려나 postmortem이 `max_attempts`(기본 3)에 이르면 서버가 `escalation`으로 블록한다. 사람이 해제해야 한다.
- `ak5 ticket move`는 같은 stage 안의 순서 조정이나 사람에게 허용된 이동만 한다. 워크플로우 진행 수단이 아니다.

### 3.3 Blocked

stage와 별개인 오버레이다. 블록되면 claim이 즉시 풀리고(`in_progress`였다면 `backlog`로), `next`에 나오지 않는다.

| kind | 거는 사람 | 푸는 사람 |
|---|---|---|
| `needs_human` | claim 보유 agent, 사람 | 사람 (`--resolution` 필수) |
| `dependency`, `external` | claim 보유 agent, 사람 | 블록한 actor 또는 사람 |
| `escalation` | 서버 (자동) | 사람 (`--resolution` 필수) |

```bash
ak5 ticket block TK-001 --kind needs_human --reason "CSV 구분자를 쉼표로 할까요, 탭으로 할까요?"
AK5_PROFILE=me ak5 ticket unblock TK-001 --resolution "쉼표"
```

`human_review`와 `done`에서는 블록할 수 없다 (사람은 반려를 쓴다).

### 3.4 보드와 템플릿

보드는 템플릿으로 만든다. 템플릿은 stage별 컬럼, worker가 갖춰야 할 capability, 기본 acceptance criteria, gatekeeper 리뷰 체크리스트를 정한다.

| 내장 템플릿 | worker capability | 기본 기준 |
|---|---|---|
| `default` | 없음 | `done` |
| `coding` | `python`, `git` | tests pass, ruff clean, diff scoped to ticket |
| `research` | `research` | sources cited, summary complete |

```bash
ak5 template list
ak5 board create proj-web --template coding --name "Web"
ak5 template apply proj-web coding        # 빠진 stage 컬럼만 추가, 삭제 없음
```

capability가 맞지 않는 worker에게는 `next`가 그 보드 티켓을 주지 않고, claim은 거부된다.

---

## 4. 웹 UI

| 화면 | 용도 |
|---|---|
| `/login` | local owner 비밀번호 또는 OIDC provider로 로그인 |
| 보드 (`/board/<id>`) | stage별 컬럼, 티켓 카드, blocked 큐. 티켓 상세에서 verdict, reopen, block/unblock |
| `/device` | `ak5 login`이 출력한 코드 승인 (agent의 role·capabilities·board 범위 확인) |
| Settings → Members | workspace 멤버와 초대 (owner/admin) |
| Settings → Agents | agent 목록, 토큰 발급·폐기 (발급 토큰은 한 번만 표시) |

상단 workspace 전환기로 workspace를 바꾼다. 화면은 이벤트 스트림으로 실시간 갱신된다.

---

## 5. CLI

모든 명령은 `--json`을 지원하고 (`AK5_OUTPUT=json`으로 기본값 변경), `ak5 <명령> --help`로 옵션을 볼 수 있다.

### 5.1 신원과 profile

```bash
ak5 login --profile me [--workspace ws_…] [--url …]
ak5 login --agent <이름> --role triage|worker|gatekeeper [--caps a,b] [--profile <이름>]
ak5 whoami [--profile <이름>]
ak5 profile list | show [<이름>] | use <이름>
ak5 logout --profile <이름>          # 서버에서 credential 폐기 + 로컬 삭제
```

- profile 선택 순서: `--profile` > `AK5_PROFILE` > 기본 profile (`ak5 profile use`).
- harness에는 `AK5_PROFILE=<이름>`만 넘긴다.
- daemon 없는 환경(CI 등)에서는 웹 UI에서 발급한 agent 토큰을 `AK5_TOKEN`으로 넣는다.
- `ak5 login --id …` 형식은 v1 호환용이며 서버가 `--dev-insecure-identify`로 떠 있을 때만 동작한다.

### 5.2 사람이 쓰는 명령

```bash
ak5 ticket create "<요청>"                                         # inbox
ak5 ticket create "<제목>" --board <id> --spec "…" --criteria "a,b" # 보드에 직접
ak5 ticket verdict <id> --approve [--reason "…"]                   # human_review → done
ak5 ticket verdict <id> --reject --reason "…"                      # → postmortem_pending
ak5 ticket unblock <id> --resolution "…"
ak5 ticket reopen <id> --reason "…"
ak5 ticket reroute <id> --to inbox|<board> --reason "…"
ak5 ticket view|list|history|update|comment|attach|download-attachment|archive|unarchive …
ak5 delegate <부모 id> --title "…" [--desc "…"] [--board <id>]      # 하위 티켓을 backlog에 생성
ak5 boards · ak5 board <id> [--watch] · ak5 board create <id> --template <t> · ak5 board adopt <id>
```

### 5.3 agent 루프 명령

```bash
ak5 next [--board <id> | --inbox] [--caps …] [--wait <초>]   # claim하지 않음. 없으면 exit 6
ak5 ticket claim <id> [--caps …]                              # claim token은 ~/.ak5/claims/<id>
ak5 ticket heartbeat <id>
ak5 ticket release <id> --reason "…"
ak5 ticket submit <id> --deliverable @file.json|-
ak5 ticket bounce <id> --reason "…"
ak5 ticket route <id> --plan @route.json|-
ak5 ticket verdict <id> --approve|--reject [--misrouted] --findings @f.json|- [--reason "…"]
ak5 ticket postmortem <id> --body @pm.json|-
```

`--token`을 생략하면 claim 때 저장한 로컬 token을 쓴다.

### 5.4 Payload 형식

```jsonc
// route --plan
{ "refined_title": "…", "refined_spec": "…", "acceptance_criteria": ["…"],
  "target": { "board_id": "proj-web" },          // 또는 { "create_board": { "name": "…", "template": "default", "description": "…" } }
  "confidence": 0.8 }

// submit --deliverable  (artifacts[].kind: url | attachment | commit | text)
{ "summary": "…", "artifacts": [{ "kind": "commit", "ref": "a1b2c3d" }],
  "criteria_self_check": [{ "criterion": "…", "pass": true, "note": "" }] }

// verdict --findings  (approve는 모두 pass, reject는 fail 1개 이상)
[{ "criterion": "…", "pass": true, "note": "…" }]

// postmortem --body
{ "what_went_wrong": "…", "root_cause": "…", "revised_spec": "…", "revised_acceptance_criteria": ["…"] }
```

### 5.5 종료 코드

| exit | 의미 | 대응 |
|---|---|---|
| 0 | 성공 | — |
| 1 | 기타 오류 (네트워크 등) | 서버 주소·상태 확인 |
| 2 | 사용법·payload 오류 | 명령과 JSON 수정 |
| 3 | 경합 (다른 claim, stale claim, WIP 한도) | `next`부터 다시 |
| 4 | 권한 없음, 허용되지 않는 전이, 없음 | 재시도하지 않음 |
| 5 | 블록된 티켓 | 건너뜀 |
| 6 | 할 일 없음 / `events wait` 타임아웃 | 기다렸다 다시 |
| 7 | CLI·서버 버전 불일치 | 업그레이드 |
| 8 | 로그인 필요, 토큰 만료·폐기 | `ak5 login` 다시 |
| 9 | daemon 사용 불가 | `ak5 daemon start` 또는 §10 참고 |

`--json`일 때 오류는 `{"error": {"code": "…", "message": "…", "details": {}}}`로 나온다.

---

## 6. client daemon과 이벤트

### 6.1 daemon

```bash
ak5 daemon status | start | stop | run
ak5 daemon install [--enable]          # Linux systemd user unit
ak5 daemon logs --dead-letter          # 실패한 hook
ak5 daemon replay <event_id>           # dead-letter 이벤트 재전달
```

- CLI가 daemon을 필요로 하면 자동으로 띄운다. 끄려면 `AK5_DAEMON_AUTOSTART=0`.
- daemon 하나가 여러 서버·workspace·profile을 동시에 관리한다. 서버 연결은 profile당 하나다.
- 소켓은 `$XDG_RUNTIME_DIR/ak5/daemon.sock` (없으면 `~/.ak5/run/daemon.sock`). 같은 OS 사용자만 접근할 수 있다.

### 6.2 기다리기

```bash
ak5 next --board <id> --wait 25 --json          # 할 일이 생길 때까지 최대 25초 대기 후 next
ak5 events wait --events WORK_AVAILABLE --timeout 25 --json
ak5 events poll                                  # 1회 조회
```

대기는 daemon의 로컬 long-poll을 쓰므로 여러 개를 동시에 돌려도 서버 연결은 늘지 않는다.

### 6.3 로컬 구독자 (hook)

session을 열어 두는 대신, 이벤트가 오면 harness를 깨우는 명령을 등록한다.

```bash
ak5 subscribe add proj-core-engine --profile worker-1 --events WORK_AVAILABLE \
  --exec 'AK5_PROFILE=worker-1 claude -p "AK5: $AK5_SUMMARY. ak5 worker 루프를 한 번 실행해."'
ak5 subscribe ls
ak5 subscribe remove <ID>
```

| 형태 | `--exec` 예 |
|---|---|
| CLI형 harness | `claude -p "$AK5_SUMMARY"` · `agent -p "$AK5_SUMMARY"` (`--preset claude`, `--preset cursor`) |
| HTTP API가 있는 harness | `curl -s -X POST http://127.0.0.1:3030/api/sessions/$SESSION/messages -H 'Content-Type: application/json' -d @-` (`--preset curl:<url>`) |
| 임의 스크립트 | `~/bin/on-ak5-event.sh` |

**hook 계약**

- daemon이 `--exec`를 셸로 그대로 실행한다. 등록 명령은 즉시 끝난다.
- 이벤트 데이터는 env와 stdin(JSON 원문)으로만 받는다. `{ticket_id}` 같은 자리표시자는 치환하지 않는다.
  env: `$AK5_EVENT`, `$AK5_EVENT_TYPE`, `$AK5_EVENT_ID`, `$AK5_CURSOR`, `$AK5_BOARD_ID`, `$AK5_TICKET_ID`, `$AK5_TITLE`, `$AK5_STATUS`, `$AK5_SUMMARY`, `$AK5_ACTOR_ID`(이벤트를 일으킨 주체), `$AK5_DATA_JSON`.
- hook 프로세스는 구독한 profile을 env로 받지 않는다. 깨운 harness가 CLI를 쓰려면 `--exec` 안에서 `AK5_PROFILE=…`을 지정한다.
- `--exec`는 harness를 **깨우는** 일만 한다. claim·작업·submit은 깨어난 세션에서 한다.
- 주요 이벤트: `WORK_AVAILABLE`(claim할 일이 생김 — inbox 진입, backlog 복귀, agent_review·postmortem_pending 진입, unblock, lease 만료), `HUMAN_ACTION_REQUIRED`(human_review 진입, needs_human·escalation 블록). 전체 목록은 [events.md](spec/events.md).

사람에게 알림을 보내는 예:

```bash
ak5 subscribe add --profile me --events HUMAN_ACTION_REQUIRED --exec 'notify-send "AK5" "$AK5_SUMMARY"'
```

### 6.4 터미널에서 지켜보기 (사람 전용)

```bash
ak5 events watch proj-core-engine --demo-echo
ak5 board proj-core-engine --watch
```

blocking 명령이다. agent 런타임(`AK5_AGENT=1` 등)에서 `events watch`·`watch`는 exit 2로 거부된다.

---

## 7. 배포와 보안

### 7.1 배포 모드

| 모드 | 사람 로그인 | 비고 |
|---|---|---|
| local (`ak5 serve`, `127.0.0.1`) | local owner (`AK5_AUTH_PASSWORD`, loopback 전용). OIDC 선택 | `local` workspace 하나 |
| Tailscale (`ak5 serve --ui --tailscale-serve`) | OIDC 필수 | `https://<machine>.<tailnet>.ts.net` |
| self-hosted (비-loopback bind) | OIDC 필수. 없으면 시작 거부 (`--insecure`로 강제 가능, 비권장) | workspace 여러 개 |

### 7.2 OIDC

```bash
export AK5_OIDC_PROVIDERS='[
  {"id": "google", "issuer": "https://accounts.google.com", "client_id": "…", "client_secret": "…"},
  {"id": "github", "issuer": "https://github.com", "client_id": "…", "client_secret": "…"}
]'
export AK5_PUBLIC_URL=https://ak5.example.ts.net     # /auth/callback의 기준 URL (없으면 bind 주소)
```

- OIDC discovery를 지원하는 provider는 모두 쓸 수 있다 (Google, Microsoft Entra, Okta, Auth0, Keycloak 등). GitHub는 userinfo 방식으로 지원한다.
- redirect URI: 같은 머신이면 `http://127.0.0.1:<port>/auth/callback`, Tailscale이면 `https://<machine>.<tailnet>.ts.net/auth/callback`.
- 서버가 인터넷에 공개될 필요는 없다. 브라우저와 서버가 IdP로 나가는 연결만 있으면 된다.

### 7.3 토큰

- CLI의 user access token은 15분이며 daemon이 갱신한다. CLI 로그인 세션은 최대 12시간이고, 그 뒤에는 다시 로그인해야 한다.
- agent 토큰은 opaque(`ak5_agt_…`)이고 DB에는 해시만 저장된다. 웹 UI(Settings → Agents) 또는 `ak5 logout --profile <이름>`으로 즉시 폐기된다.
- JWT 서명 키는 `AK5_JWT_SECRET`이 있으면 그것을, 없으면 데이터 디렉터리에 `0600`으로 생성한 파일을 쓴다.

### 7.4 local 위협 모델

agent가 **서버와 같은 OS 사용자**로 돌면 DB 파일과 서명 키를 직접 읽고 쓸 수 있으므로 어떤 인증도 우회된다. "done은 사람만"이 중요하다면:

1. agent를 다른 OS 사용자, 컨테이너, sandbox에서 돌리거나 서버를 다른 머신(원격, Tailscale)에 둔다.
2. 사람에게서 토큰을 받아 agent에게 넘기지 않는다. 사람 토큰은 짧게 유지되고 audit에 승인자가 남는다.

자세한 내용은 [identity.md §8.2](spec/identity.md).

### 7.5 환경 변수

| 변수 | 쓰는 곳 | 의미 |
|---|---|---|
| `AK5_AUTH_PASSWORD` | 서버 | local owner 비밀번호 (loopback 로그인) |
| `AK5_OIDC_PROVIDERS`, `AK5_PUBLIC_URL` | 서버 | OIDC provider 목록, callback 기준 URL |
| `AK5_JWT_SECRET` / `AK5_JWT_SECRET_FILE` | 서버 | JWT 서명 키 / 키 파일 경로 |
| `AK5_DATA_DIR` | 서버 | DB와 첨부 파일 위치 |
| `AK5_ENABLE_MCP` | 서버 | `1`이면 `/mcp` 마운트 (`ak5[mcp]` 필요) |
| `AK5_DEV_INSECURE_IDENTIFY` | 서버 | `1`이면 loopback에서 v1 self-identify 허용 (개발 전용) |
| `AK5_PROFILE` | CLI | 사용할 profile |
| `AK5_TOKEN` | CLI | daemon 없이 쓸 agent 토큰 |
| `AK5_API_URL` | CLI | 서버 API 주소 (profile이 없을 때) |
| `AK5_OUTPUT=json` | CLI | `--json` 기본값 |
| `AK5_DAEMON_AUTOSTART=0` | CLI | daemon 자동 시작 끔 |
| `XDG_RUNTIME_DIR`, `AK5_DAEMON_SOCK` | CLI·daemon | daemon 소켓 위치 |
| `AK5_AGENT=1` | CLI | agent 런타임 표시 (blocking watch 거부) |

---

## 8. MCP (선택)

CLI가 1순위 통합 경로다. MCP만 쓸 수 있는 호스트를 위해 어댑터를 제공한다.

```bash
pip install 'ak5[mcp]'
```

```json
{
  "mcpServers": {
    "ak5": {
      "command": "uvx",
      "args": ["--from", "ak5[mcp]", "ak5", "mcp"],
      "env": { "AK5_API_URL": "http://127.0.0.1:8000/api/v1", "AK5_ACTOR_TOKEN": "ak5_agt_…" }
    }
  }
}
```

- `AK5_ACTOR_TOKEN`에는 웹 UI에서 발급한 agent 토큰을 넣는다.
- agent 동사만 tool로 노출한다 (`ak5_next`, `ak5_ticket_claim`, `ak5_ticket_submit`, `ak5_ticket_route`, `ak5_ticket_verdict`, `ak5_ticket_postmortem`, `ak5_ticket_block` 등). 사람의 최종 승인은 CLI나 웹 UI로만 한다.

---

## 9. v1에서 옮기기

v2 서버는 시작할 때 v1 DB를 자동으로 변환한다.

- `local` workspace가 생기고, v1 human actor는 user + membership으로, agent actor는 agent로 바뀐다.
- **agent credential은 옮겨지지 않는다.** 각 agent를 `ak5 login --agent …`로 다시 등록해야 한다. 기존 JWT와 프로젝트 디렉터리의 `.ak5/sessions/`는 더 이상 쓰이지 않는다.
- 예전에 작업 디렉터리에 있던 `./ak5.db`는 `ak5 migrate-legacy`(`--force`로 덮어쓰기)로 전역 저장소로 옮긴다.

| v1 | v2 |
|---|---|
| `ak5 web` | `ak5 serve --ui` (alias 유지) |
| `ak5 login --id … --type agent --role "…"` | `ak5 login --agent <이름> --role triage\|worker\|gatekeeper` |
| `ak5 create-board <id> --name …` | `ak5 board create <id> --template <t> --name …` |
| `ak5 claim-board <id>` | `ak5 board adopt <id>` |
| `ak5 ticket create --assign …` | 담당자 지정 없음. agent가 `next` / `claim`으로 가져감 |
| `ak5 delegate <id> --to <agent>` | `ak5 delegate <id> --title …` (backlog에 하위 티켓 생성) |
| `ak5 move <id> "In Progress"` / `"Done"` | `ak5 ticket claim` / `submit` / `verdict` |
| `ak5 ticket block --mention …` | `ak5 ticket block --kind needs_human\|dependency\|external --reason …` |
| `ak5 subscribe create --for-agent … --exec …` (서버 hook) | `ak5 subscribe add --events … --exec …` (daemon 로컬 구독자) |
| `/auth/identify`, `AK5_IDENTIFY_SECRET` | device grant, OIDC, local owner |
| 보드 컬럼 To Do / In Progress / Review / Done | stage `backlog` / `in_progress` / `agent_review` / `human_review` / `done` (+ inbox, postmortem_pending) |

예전 이슈 #4(Column stage ↔ Ticket status 정합)는 v2 stage 모델(`status`는 저장하지 않는 파생값)로 대체되었다.

---

## 10. 문제 해결

**`Failed to connect to AK5 Gateway`** — 서버가 떠 있는지(`curl http://127.0.0.1:8000/meta`), profile의 server 주소가 맞는지(`ak5 profile show`) 확인한다.

**exit 8 `Authentication required`** — profile이 없거나 토큰이 폐기·만료됐다. `ak5 whoami`로 profile을 확인하고 `ak5 login`을 다시 한다. harness에 `AK5_PROFILE`이 전달됐는지도 확인한다.

**exit 9 `DAEMON_UNAVAILABLE`** — daemon을 띄우지 못했다. `~/.ak5/daemon/daemon.log`를 본다. 흔한 원인은 소켓 경로가 AF_UNIX 한도(약 104바이트)를 넘는 것이다. 이 경우 `XDG_RUNTIME_DIR`을 짧은 경로로 두거나 `AK5_DAEMON_SOCK=/tmp/ak5.sock`처럼 지정한다. `AK5_DAEMON_AUTOSTART=0`이면 직접 `ak5 daemon start`를 해야 한다.

**`next`가 계속 exit 6** — 대상 stage에 claim할 티켓이 없다. 보드 템플릿의 worker capability와 `--caps`가 맞는지, 티켓이 blocked는 아닌지(`ak5 ticket list --status blocked`) 확인한다.

**claim이 exit 3** — 다른 agent가 먼저 가져갔거나 WIP 한도다. `next`부터 다시 한다.

**claim·verdict가 exit 4 `ROLE_FORBIDDEN`** — profile의 role로 할 수 없는 전이다 (예: worker가 `agent_review` 티켓 claim, agent가 최종 승인, 자기가 제출한 티켓 검토).

**hook이 실행되지 않는다** — `ak5 subscribe ls`에 행이 있는지, `--events` 필터가 맞는지, `ak5 daemon logs --dead-letter`에 실패가 쌓였는지 본다. hook 안에서 CLI를 쓴다면 `AK5_PROFILE`을 `--exec`에 넣었는지 확인한다.

**여러 agent가 동시에 써도 SQLite가 괜찮은가** — WAL 모드와 busy timeout을 쓰고, 대기는 daemon의 long-poll로 모으므로 local·소규모 팀 규모에서는 충분하다.

---

## 11. 개발과 테스트

```bash
git clone https://github.com/fritzprix/ak5.git && cd ak5
uv sync --all-packages --all-extras

uv run pytest -m "not live_server"                 # 단위·통합
uv run pytest backend/tests/regression              # 회귀 게이트 (실서버를 띄움)
uvx ruff check backend
cd frontend && npm test && npm run lint             # 웹 UI

./scripts/build_web_ui.sh                           # 대시보드를 backend/src/ak5/web_ui/로 빌드
uv run ak5 serve --ui --reload
```

- 웹 UI hot reload: 터미널 A `uv run ak5 serve --reload`, 터미널 B `cd frontend && npm run dev` (`http://localhost:3000`, `/api/*`를 서버로 프록시).
- 실서버·CLI·daemon 전체 확인: `uv run python .agents/skills/ak5-live-test/scripts/run_live_test.py` ([skill](../.agents/skills/ak5-live-test/SKILL.md)).
