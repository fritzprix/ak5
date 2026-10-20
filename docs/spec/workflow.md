# AK5 Workflow Spec — Agent Orchestration + Human Final Approval

Status: Draft · 2026-10-17

## 0. 범위

AK5는 **agent 루프가 붙어 일하는 칸반 플랫폼**이다. agent 루프(triage / worker / gatekeeper)는 외부 구성요소이며, AK5는 다음만 제공한다.

| 계층 | AK5 책임 |
|---|---|
| 상태 머신 | stage, 전이 규칙, role별 허용 전이를 서버가 강제 |
| 작업 큐 의미론 | claim / lease / heartbeat, `next` 선택, lease 만료 lazy 회수 |
| 구조화된 계약 | route / verdict / postmortem payload 스키마와 검증 |
| 이력 | revision, review, postmortem, audit |
| 관찰 인터페이스 | 이벤트 커서 폴링 (로컬에서는 SSE 추가) |
| 접근 표면 | REST가 정본, CLI·MCP는 얇은 어댑터 |
| 배포 이식성 | DB / blob / event 백엔드 추상화 (local · Vercel · AWS) |

범위 밖: 루프 러너, 모델 선택, 프롬프트, 실행 전략, postmortem 내용의 생성.

## 1. Actor Role

| role | 권한 |
|---|---|
| `triage` | inbox 읽기, 티켓 구체화, board 라우팅 / 생성 |
| `worker` | board backlog claim, 제출, bounce |
| `gatekeeper` | agent_review verdict, postmortem 작성 (겸임) |
| `human` | human_review verdict, inbox 직접 라우팅, 오버라이드, needs_human / escalation 해제 |

## 2. 상태 모델

```
stage    : unclassified | backlog | in_progress | agent_review
           | postmortem_pending | human_review | done
claim    : 파생값 (claim_token, lease_expires_at) — unclaimed | claimed
blocked  : { kind: needs_human | dependency | external | escalation,
             reason, since, by } | null           — 직교 오버레이
status   : API 하위호환용 파생 필드 (저장하지 않음)
```

`status` 파생 규칙: `done` → `done`, blocked → `blocked`, claimed → `in_progress`, 그 외 → `open`.

### 2.1 Inbox Board (workspace당 1개)

- **workspace마다** 시스템 board 하나: `kind = "inbox"`, API 별칭 `inbox`. workspace 생성 시 함께 만들며 삭제 / 이름 변경 불가 ([identity.md](identity.md) §7). 단일 workspace(local) 모드에서는 `board_id = "inbox"`.
- **`unclassified` stage는 inbox에만 존재한다.** 일반 board에는 `unclassified` 컬럼이 없다.
- inbox에는 `unclassified` 이외의 stage 컬럼을 두지 않는다.
- inbox로 들어오는 경로: T1 `create`, T10 `bounce`, T14 `reject(misrouted)`, T21 `reroute`.
- inbox에서 나가는 경로: T4 `route`, T21 `reroute`.
- 폴링 인터페이스 (orchestrator용):
  - `GET /boards/inbox/next` — 가장 오래된 unclaimed·unblocked 티켓 1개 (없으면 204)
  - `GET /events?board_id=inbox&since=<cursor>` — inbox 이벤트만 커서 기반으로 조회

### 2.2 Board Template

일반 board는 `template`을 가진다. template은 다음을 정의한다.

- 컬럼 구성: `backlog, in_progress, agent_review, postmortem_pending, human_review, done` 각 stage마다 최소 1개의 컬럼
- `worker_capabilities`: backlog claim에 필요한 capability (없으면 `next`에서 제외, claim은 `ROLE_FORBIDDEN`)
- `acceptance_criteria_defaults`: worker claim 응답에 포함되는 기본 acceptance criteria
- `review_checklist`: gatekeeper claim 응답에 포함되는 board별 리뷰 기준
- `max_attempts` 기본값 (기본 3, 티켓별 오버라이드 가능)

내장 템플릿(`default`, `coding`, `research`, `inbox`)과 사용자 정의 템플릿(JSON)을 **서버가** 관리하며, 조회는 `GET /templates`, `GET /templates/{name}`로 한다. 클라이언트는 로컬 레지스트리를 정본으로 쓰지 않는다. 사용자 정의 템플릿의 범위는 workspace다 ([identity.md](identity.md) §7).

자동 생성된 board는 `auto_created = true`로 표시하며, 서버 설정 `max_auto_boards`로 생성 수를 제한한다.

## 3. Stage 전이표

전이는 명령(operation) 단위로 정의한다. 클라이언트는 stage를 직접 쓰지 않는다.

| # | 명령 | from | to | role | 필수 payload | 부수 효과 |
|---|---|---|---|---|---|---|
| T1 | `create` | — | unclassified @inbox | human | `original_request` | `original_request` 불변 |
| T2 | `create_direct` | — | backlog | human, triage | spec, acceptance_criteria, board_id | revision r1 |
| T3 | `claim` | unclassified | unclassified (claimed) | triage | — | claim_token, lease 발급 |
| T4 | `route` | unclassified (agent: 본인 claim, human: claim 불요) | backlog @대상 board | triage, human | route 스키마 (§5.1) | board 이동, 필요하면 board 생성 (같은 트랜잭션), revision, claim 해제 |
| T5 | `claim` | backlog | in_progress | worker | capabilities | claim_token 발급, WIP 검사, 최신 spec과 postmortem 이력 반환 |
| T6 | `heartbeat` | in_progress (본인 claim) | 동일 | worker | token | lease 연장 |
| T7 | `release` | in_progress (본인 claim) | backlog | worker | token, reason | claim 해제 |
| T8 | *lease 만료* | in_progress | backlog | system | — | claim 해제, `lease_expirations += 1` |
| T9 | `submit` | in_progress (본인 claim) | agent_review | worker | token, deliverable (§5.2) | claim 해제, `submitted_by` 기록 |
| T10 | `bounce` | in_progress (본인 claim) | unclassified @inbox | worker | token, reason | claim 해제 |
| T11 | `claim` | agent_review | agent_review (claimed) | gatekeeper | — | `submitted_by` 본인은 불가 |
| T12 | `verdict: approve` | agent_review (본인 claim) | human_review | gatekeeper | findings (모두 pass) | review 기록, claim 해제 |
| T13 | `verdict: reject` | agent_review (본인 claim) | backlog | gatekeeper | findings (fail 1개 이상) | review 기록, `attempt += 1`, E1 |
| T14 | `verdict: reject(misrouted)` | agent_review (본인 claim) | unclassified @inbox | gatekeeper | reason | review 기록 |
| T15 | `verdict: approve` | human_review | done | human | — | done으로 가는 유일한 경로 |
| T16 | `verdict: reject` | human_review | postmortem_pending | human | reason (자유 텍스트) | review 기록 |
| T17 | `reopen` | done | postmortem_pending | human | reason | review 기록 |
| T18 | `claim` | postmortem_pending | postmortem_pending (claimed) | gatekeeper | — | 사람의 반려 사유와 review 이력 반환 |
| T19 | `submit_postmortem` | postmortem_pending (본인 claim) | backlog | gatekeeper | postmortem (§5.4) | postmortem 기록, 새 revision, `attempt += 1`, E1 |
| T20 | *lease 만료* | unclassified / agent_review / postmortem_pending | 동일 (unclaimed) | system | — | claim 해제 |
| T21 | `reroute` | done 이외 | unclassified @inbox 또는 다른 board의 backlog | human | reason | claim 해제 |

## 4. Blocked 오버레이

| # | 명령 | 적용 stage | role | 효과 |
|---|---|---|---|---|
| B1 | `block` | unclassified, backlog, in_progress, agent_review, postmortem_pending | claim 보유자, human | claim 즉시 해제. in_progress였다면 stage를 backlog로 이동 |
| B2 | `unblock` (`dependency`, `external`) | blocked인 티켓 | 블록한 actor, human | blocked 해제 |
| B3 | `unblock` (`needs_human`, `escalation`) | blocked인 티켓 | human | `resolution` 필수, revision 입력으로 기록 |
| E1 | *자동 에스컬레이션* | T13·T19 직후 `attempt ≥ max_attempts` | system | backlog + blocked(`escalation`) |

`block` 금지 stage: `human_review`(사람은 반려를 사용), `done`.

## 5. Payload 스키마

### 5.1 route
```json
{
  "refined_title": "string",
  "refined_spec": "string",
  "acceptance_criteria": ["string"],
  "target": { "board_id": "string" }
          | { "create_board": { "name": "string", "template": "string", "description": "string" } },
  "confidence": 0.0
}
```
요청이 모호하면 route 대신 `block(kind=needs_human, reason=<질문>)`을 사용한다.
사람의 직접 라우팅(human route) 시 `refined_title`, `refined_spec`, `acceptance_criteria`는 선택 사항이며(생략 시 티켓 기본 필드 유지), `confidence`는 기본값 1.0으로 간주된다.

### 5.2 deliverable
```json
{
  "summary": "string",
  "artifacts": [{ "kind": "url|attachment|commit|text", "ref": "string" }],
  "criteria_self_check": [{ "criterion": "string", "pass": true, "note": "string" }]
}
```

### 5.3 verdict
```json
{
  "decision": "approve|reject",
  "reason": "string",
  "misrouted": false,
  "findings": [{ "criterion": "string", "pass": true, "note": "string" }]
}
```

### 5.4 postmortem
```json
{
  "what_went_wrong": "string",
  "root_cause": "string",
  "revised_spec": "string",
  "revised_acceptance_criteria": ["string"]
}
```

## 6. 불변 규칙

1. `done`은 T15로만 진입한다.
2. `in_progress` ⇔ 유효한 claim이 있음.
3. blocked 티켓은 `next`에 나오지 않고 claim할 수 없다.
4. `done`이면 blocked와 claim이 모두 없다.
5. claim이 필요한 명령은 `claim_token`이 일치하고 lease가 유효해야 한다.
6. 같은 attempt 안에서 submitter ≠ agent_review reviewer.
7. `unclassified` stage 티켓은 항상 inbox board에 있고, inbox board에는 `unclassified` 티켓만 있다.
8. 모든 전이는 audit에 `(from, to, op, actor, reason, attempt)`로 기록한다.

## 7. 에러 계약

| 코드 | HTTP | 상황 |
|---|---|---|
| `INVALID_TRANSITION` | 409 | 현재 stage에서 허용되지 않는 명령 |
| `ROLE_FORBIDDEN` | 403 | 해당 명령을 쓸 수 없는 role |
| `SEPARATION_OF_DUTIES` | 403 | 자신이 제출한 작업을 review |
| `CLAIM_CONFLICT` | 409 | 다른 actor가 이미 claim함 |
| `STALE_CLAIM` | 409 | token 불일치 또는 lease 만료 |
| `TICKET_BLOCKED` | 423 | blocked 티켓에 claim이나 전이 시도 |
| `WIP_LIMIT` | 409 | 대상 컬럼 WIP 초과 |
| `PAYLOAD_INVALID` | 422 | payload 스키마 위반 |
| `SYSTEM_BOARD` | 403 | inbox board 삭제·수정 또는 inbox로의 잘못된 이동 |
| `NOT_FOUND` | 404 | ticket / board가 존재하지 않음 (CLI exit 4) |

claim 경합: 이미 유효한 claim이 있는 티켓에 대한 claim은 stage와 무관하게 `CLAIM_CONFLICT`(CLI exit 3)다 — 외부 루프는 이를 "다음 티켓으로"로 처리한다.

모든 쓰기 명령은 `Idempotency-Key` 헤더를 받는다.

## 8. Column과의 관계

- 전이는 stage 단위이고 column은 표현 단위다. 도착 column은 해당 stage의 최소 position column이다.
- 같은 stage 안의 column 이동은 자유다. stage가 바뀌는 이동은 §3의 human 명령에 대응될 때만 허용한다.

## 9. 데이터 모델 추가

- `boards`: `kind` (`inbox|standard`), `template`, `auto_created`
- `tickets`: `original_request`, `attempt`, `max_attempts`, `claim_token`, `claimed_by`, `lease_expires_at`, `lease_expirations`, `submitted_by`, `blocked` (kind/reason/since/by)
- `ticket_revisions`: spec, acceptance_criteria, author, reason, source (`route|postmortem|unblock|direct`)
- `ticket_reviews`: stage, decision, findings, reviewer, attempt
- `ticket_postmortems`: what_went_wrong, root_cause, revision_id, human_review_id

## 10. 미결 사항

- CLI 명령 / 종료 코드 계약
- 배포 추상화 (Postgres + Alembic, Blob/S3, 이벤트 폴링 백엔드)
- agent 전용 장기 토큰과 role 스코프
