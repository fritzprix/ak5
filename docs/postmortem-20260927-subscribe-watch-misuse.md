# [Postmortem] Subscribe UX 회귀 — `watch`를 에이전트 구독으로 오판

- **문서 번호:** PM-20260927-02
- **관련 문서:** [PM-20260927-01](postmortem-20260927-subscribe-architecture.md) (Register & Return 아키텍처 전환)
- **사건 일시:** 2026-09-27
- **Trajectory:** `LibrAgent 개발팀_trajectory.json` (ATIF-v1.7, session `sum4n7z4fksfku0he02eoe9m`)
- **에이전트:** LibrAgent 0.9.12 / `openai/Qwen3.6-35B-A3B-Thinking`
- **영향 컴포넌트:** `ak5 subscribe` CLI UX, 에이전트 스킬/룰, LibrAgent 프로세스 도구 연동
- **상태:** 분석 완료 — 조치 항목 Open

---

## 1. 개요 (Executive Summary)

PM-20260927-01 이후 `ak5 subscribe create`(Register & Return)가 구현·문서화되었음에도, LibrAgent PM이 사용자 요청 *"해당 board에 subscribe 하자"*에 대해 **서버 훅 등록이 아니라** `ak5 subscribe watch`를 배경 프로세스로 띄우고 “구독 활성”으로 보고했습니다.

게이트웨이 `subscriptions` 테이블은 **0행**이었고, 실제로는 기본 `--exec`인 `echo "[$AK5_EVENT] $AK5_TICKET_ID: $AK5_TITLE"`가 stdout으로 찍히는 **사람용 SSE 뷰어**만 동작했습니다. 에이전트 하네스(티켓 claim / 작업 실행 / 코멘트)와는 통합되지 않았습니다.

본 사건은 **아키텍처 미구현이 아니라, 구현 이후에도 에이전트가 잘못된 서브커맨드를 고르는 UX/프롬프트 회귀**입니다. help 텍스트를 읽고 create와 watch를 구분한 뒤에도 watch를 선택했습니다.

---

## 2. 사건 타임라인 (Trajectory Steps)

| Step | 행위자 | 수행 내용 | 해석 |
| :--- | :--- | :--- | :--- |
| 1–7 | 사용자/에이전트 | `uvx ak5 help` → CLI 개요 파악 | 온보딩 |
| 8–15 | 사용자/에이전트 | “개발 PM으로 참여” → `@user_pm` 로그인 | 잘못된 actor (이후 교정) |
| 16–20 | 사용자/에이전트 | “`libr-agent-dev-pm`이어야 함” → 재로그인, 보드 확인 | 신원 교정 완료 |
| **21** | 사용자 | **“그럼 해당 board에 subscribe 하자”** | 구독 요청 (의도: 자동화 훅으로 해석되어야 함) |
| **22** | 에이전트 | `ak5 subscribe --help` | help에 **Register & Return**, `create` 즉시 exit, `watch` = blocking 명시 |
| **23** | 에이전트 | create vs watch를 **명시적으로 비교**한 뒤 “PM은 실시간 감시 → **watch**”로 추론. `subscribe list` → 비어 있음 | **[핵심 오판]** API를 알고도 watch 선택 |
| **24** | 에이전트 | `workspace__spawnProcess`: `ak5 subscribe watch proj-libragent-dev` | 서버 등록 없음, 로컬 SSE 상주 |
| 25–26 | 에이전트 | wait / `readProcessOutput` → Event Watcher 배너 + 기본 `echo` hook 확인 | stdout 배너를 “구독 성공” 증거로 사용 |
| **27** | 에이전트 | `ui__reportResult`: “✅ Board 구독 활성 / SSE 연결됨 / 배경 실행 중” | **성공 연극 (success theater)** — harness 미연동 |

검증 (사건 후 수동):

```text
ak5 subscribe ls  →  No active subscriptions found. / API []
DB subscriptions  →  0 rows
ps                →  ak5 subscribe watch proj-libragent-dev (PID 상주)
```

---

## 3. 근본 원인 분석 (Root Cause Analysis)

### Root Cause 1: “구독” 의미의 프레임 충돌 (Intent Misread)

| 사용자/제품 의도 (Register & Return) | 에이전트 해석 |
| :--- | :--- |
| 서버에 hook 등록 → 이벤트 시 harness/`--exec` 실행 | PM이 보드를 **실시간으로 지켜보는** 모니터 |
| `subscribe list`에 행이 생겨야 성공 | 배경 프로세스가 살아 있고 stdout 배너가 보이면 성공 |

에이전트 reasoning (step 23):

> Given the context of a PM wanting to stay on top of the board, I think they want the **watch** mode for real-time updates.

help에 create가 “Register a server-side hook and return immediately”로 나와 있어도, **역할 모델(PM = 관찰자)** 이 제품 원칙을 이겼습니다.

### Root Cause 2: 하네스 성공 신호가 stdout 프로세스에 편향

LibrAgent 도구 체인이 다음을 강하게 affordance합니다:

1. `workspace__spawnProcess` — blocking 명령을 “비차단”처럼 보이게 함  
2. Suggested follow-up: `readProcessOutput` / `waitForProcess`  
3. 에이전트는 **관측 가능한 stdout**을 성공 조건으로 삼음  

반면 `subscribe create`는:

- 즉시 exit 0  
- `--exec`에 **의미 있는 harness 명령**을 설계해야 함  
- 이후 이벤트가 오기 전에는 “살아 있는 구독”이 시각적으로 안 보임  

→ 에이전트 입장에서 create는 더 어렵고, watch+echo는 즉시 “연결됨” 증거를 줍니다.

### Root Cause 3: CLI 표면이 잘못된 선택을 여전히 초대함

PM-01 이후에도:

- `subscribe watch`가 **같은 그룹**에 남아 있고 help 예시에 포함됨  
- `watch` 기본 `--exec`가 `echo "[$AK5_EVENT] $AK5_TICKET_ID: $AK5_TITLE"` → “구독하면 출력이 나온다”는 착각을 강화  
- `subscribe list`가 비어 있어도 에이전트가 create로 경로 수정하지 않음 (빈 목록을 “아직 watch 안 켬”으로 해석)

### Root Cause 4: 문서/스킬의 이중 메시지 (회귀 요인)

- `.agents/skills/ak5/SKILL.md` Event-driven 섹션에 `create`와 `watch`/`board --watch`가 **나란히** preferred로 보임  
- `frontend/src/lib/agentSetup.ts`는 여전히 `board --watch` / `curl -N` SSE를 preferred로 안내  
- `.agents/rules/cli-nonblocking-rule.md`는 “subscribe = action trigger, watch는 분리”를 말하지만, **에이전트가 `--help`만 보고 결정**한 이번 경로에는 스킬/룰이 주입되지 않았음

### Root Cause 5: 성공 보고 카피의 허위 동등성

보고 문구(“Board 구독 활성”, “SSE 연결됨”, “모든 보드 이벤트”)는 **서버 등록 구독과 구분되지 않아** 사용자/후속 에이전트가 검증 없이 통과시킵니다.

---

## 4. PM-01과의 관계

| | PM-20260927-01 | PM-20260927-02 (본 문서) |
| :--- | :--- | :--- |
| 시점 | Register & Return **이전** | Register & Return **이후** |
| 증상 | blocking subscribe hang → kill 루프 → 폴링 포기 | help를 읽고도 **watch를 의도적으로 선택** |
| 주원인 | 아키텍처 결함 (클라이언트 SSE만 존재) | UX/의도 프레임 + 하네스 stdout 편향 |
| 교훈 | 서버 훅 + 즉시 리턴 필요 | **구현만으로는 부족** — 에이전트 기본 경로에서 watch를 제거/격리해야 함 |

PM-01의 P0 구현은 유효합니다. 본 사건은 **그 위에 얹힌 에이전트 오용 회귀**입니다.

---

## 5. 교훈 (Lessons)

1. **Help 문장 ≠ 에이전트 정책.** 올바른 설명이 있어도, 역할 서사(“PM은 실시간 감시”)와 도구 affordance가 이기면 잘못된 서브커맨드가 선택된다.  
2. **성공 메트릭을 서버 상태에 둘 것.** 구독 성공 = `subscribe ls`에 행 존재 + (가능하면) dry-run/probe. 배경 PID/stdout 배너는 성공 조건이 될 수 없다.  
3. **에이전트 경로와 인간 관측 경로를 물리적으로 분리할 것.** `watch`를 `subscribe` 그룹에 두면 이름이 구독을 훔친다.  
4. **기본 `echo` exec는 데모용 함정이다.** 에이전트 대면 기본값은 harness 연동 예시이거나, watch에 기본 exec를 두지 않아야 한다.

---

## 6. 조치 항목 (Action Items)

| 우선순위 | 영역 | 과제 | 상태 |
| :--- | :--- | :--- | :--- |
| **P0** | CLI | `subscribe watch`를 `subscribe` 그룹에서 분리 (`ak5 events watch` / `ak5 tail` 등)하거나, help/에필로그에 **“Agents: do not use watch; use create”** 경고 | **Completed** | `ak5 events watch` + deprecated `subscribe watch` |
| **P0** | CLI | 에이전트 환경 감지 시(`AK5_ACTOR_ID` / non-TTY) `watch` 실행을 거부하거나 강한 confirm 요구 | **Completed** | `ak5.cli.agent_env` exit 2 (`AK5_AGENT` / non-TTY) |
| **P0** | Skills | `.agents/skills/ak5/SKILL.md`에서 Event-driven preferred를 **`subscribe create`만** 남기고 watch는 “human terminal only”로 격하 | **Completed** | `skills/ak5/SKILL.md` |
| **P0** | Frontend | `agentSetup.ts`의 preferred 경로를 Register & Return(`subscribe create`)으로 교체 | **Completed** | `frontend/src/lib/agentSetup.ts` |
| **P1** | CLI UX | `subscribe create` 성공 출력에 `ak5 subscribe ls` 검증 한 줄 + “hook will run on gateway, no local process” 명시 | **Completed** | `do_subscribe` Panel |
| **P1** | CLI UX | `watch` 기본 `--exec` echo 제거 또는 `--demo-echo` 플래그 뒤로 숨김 | **Completed** | `--exec` required / `--demo-echo` |
| **P1** | Harness contract | LibrAgent/스킬에 “subscribe 성공 정의 = `subscribe ls` non-empty” 체크리스트 추가 | **Completed** | skill + MANUAL + rule |
| **P2** | Tests/Eval | Trajectory 회귀 시나리오: 사용자 “subscribe 하자” → 기대 도구 호출이 `subscribe create`인지 평가 | Open | — |

---

## 7. 증거 요약 (Evidence)

- Trajectory step 22 help 출력: Register & Return / create immediate exit / watch blocking — **모두 노출됨**  
- Trajectory step 23: create/watch 비교 후 **명시적 watch 선택**  
- Trajectory step 24–27: `spawnProcess` + stdout 배너 → “구독 활성” 보고  
- 런타임 검증: `subscriptions` API/DB empty, `subscribe watch` 프로세스만 생존  

---

## 8. 한 줄 결론

> Register & Return을 만든 뒤에도, 에이전트는 “구독 = 내가 SSE stdout을 붙잡는 것”으로 성공을 정의했다. **API 수정만으로는 부족하고, 에이전트 기본 경로에서 `watch`를 제거하고 성공 정의를 `subscribe ls`로 고정해야 회귀가 막힌다.**
