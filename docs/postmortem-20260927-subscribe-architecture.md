# [Postmortem] AK5 'subscribe' 장애 및 아키텍처 결함 분석

- **문서 번호:** PM-20260927-01
- **사건 일시:** 2026-09-26 (Trajectory Session: `sum4n7z4fksfku0he02eoe9m`)
- **영향 컴포넌트:** AK5 CLI (`ak5 subscribe`), AK5 Gateway/EventBus, 자율 AI 에이전트(LibrAgent 등) 워크플로우
- **상태:** 분석 완료 및 아키텍처 개선 결정 (Resolved with Architectural Redesign)

---

## 1. 개요 (Executive Summary)

자율 개발 PM 에이전트(LibrAgent)가 칸반 보드(`proj-libragent-dev`)의 실시간 이벤트 감지 및 티켓 관리를 위해 `ak5 subscribe` 명령어를 실행했으나, 명령어가 즉시 반환되지 않고 영구 대기(blocking) 상태에 빠졌습니다.

에이전트의 쉘 실행 도구(`workspace__runShell`)의 타임아웃(10~15초)으로 인해 "명령어가 멈췄다(hanging)"고 오판하고 강제 종료(`workspace__stopProcess`)를 반복하였고, 결국 실시간 구독 설정을 포기하고 주기적 폴링(cron)으로 우회했습니다. 사용자가 *"subscribe 안되나?"*라고 직접 확인을 요청했을 때도 재시도 끝에 실패했습니다.

본 장애의 근본 원인은 **이미 상주 중인 AK5 서버 프로세스가 존재함에도 불구하고, `ak5 subscribe`가 서버에 훅(Hook)을 '등록하고 즉시 리턴'하는 방식이 아니라, 클라이언트 프로세스가 SSE 스트림을 물고 계속 상주해야 하는 '`tail -f` 방식'으로 잘못 설계·구현**되었기 때문입니다.

---

## 2. 사건 타임라인 (Timeline of Events)

| Step | 행위자 | 수행 내용 및 로그 | 의도 및 해석 |
| :--- | :--- | :--- | :--- |
| **Step 24** | 사용자 | *"개발 PM으로 참여 그리고 ticket을 1시간 주기로 확인"* 요청 | 에이전트에게 PM 역할 부여 및 보드 모니터링 지시 |
| **Step 29** | 에이전트 | `uvx ak5 subscribe proj-libragent-dev --exec '...' --dry-run` 실행 | 이벤트 파이프라인 검증 시도. 10초 동안 아무 이벤트가 없어 동기 타임아웃 발생 (`Sync timeout handoff`) |
| **Step 30** | 에이전트 | `workspace__stopProcess`로 프로세스 강제 kill | **[오판]** 에이전트 추론: *"Actually, the `--dry-run` should have exited."* 명령어가 hang되었다고 판단함 |
| **Step 31** | 에이전트 | `uvx ak5 subscribe ... --once` 실행 | 단발성 실행 시도. 15초 동안 새 이벤트가 없어 또다시 동기 타임아웃 발생 |
| **Step 32** | 에이전트 | `workspace__stopProcess`로 다시 kill | **[오판]** 에이전트 추론: *"--once flag should have made it exit after one event, but it's blocking."* |
| **Step 40** | 에이전트 | `nohup uvx ak5 subscribe ... > /dev/null 2>&1 &` 시도 | 백그라운드 분리를 시도했으나 쉘 도구가 10초 대기 후 타임아웃 |
| **Step 41** | 에이전트 | `workspace__stopProcess`로 재차 kill | 백그라운드 프로세스마저 hang된 것으로 판단하여 정리 |
| **Step 42** | 에이전트 | `timeout 5 uvx ak5 subscribe ... --once` 실행 | 5초 타임아웃으로 `TIMEOUT_OR_ERROR` 출력. 에이전트는 `subscribe` 연동을 완전 포기 |
| **Step 51** | 에이전트 | 사용자에게 *"PM 설정 완료"* 보고 | `subscribe` 실패 사실은 누락하고, 1시간 주기 cron 폴링으로만 세팅 보고 |
| **Step 52** | 사용자 | **"subscribe 안되나?"** 질의 | 사용자가 이벤트 구독 기능의 미동작을 포착하고 질문 |
| **Step 53** | 에이전트 | `timeout 8 uvx ak5 subscribe ... --once` 재시도 | 8초 동안 이벤트 없어 `EXIT_CODE=124` (타임아웃) 발생 |
| **Step 54** | 에이전트 | `nohup timeout 300 uvx ak5 subscribe ...` 백그라운드 런칭 후 중단 | 추론: *"The subscribe command is a long-running process that streams events... The `--once` flag doesn't seem to work as expected."* 세션 종료 |

---

## 3. 근본 원인 분석 (Root Cause Analysis)

### Root Cause 1: 아키텍처 설계 결함 (클라이언트 상주형 vs 서버 사이드 훅 등록)

- **정상 설계 모델 (Server-Side Hook Registration)**:
  - 이미 시스템에 상시 실행 중인 **AK5 Gateway 서버 프로세스**가 존재합니다.
  - 사용자와 에이전트가 기대하는 `subscribe`의 본질은 다음과 같습니다:
    1. CLI가 서버에 `(board_id, event_filter, exec_command_text)`를 등록(POST)
    2. 서버는 DB/메모리에 해당 명령어 텍스트를 저장
    3. CLI는 "구독 규칙 등록 완료"와 함께 **즉시 `exit 0`으로 반환 (Non-blocking)**
    4. 이후 보드 이벤트 발생 시, **살아있는 서버 프로세스가 저장된 명령어 텍스트를 쉘로 직접 비동기 실행**
- **기존 구현의 오류 (Client-Side Streaming Listener)**:
  - 기존 `subscribe.py`는 서버에 아무것도 저장하지 않고, CLI 프로세스가 직접 `/events/stream` (SSE) 엔드포인트를 열고 대기하는 `tail -f` 방식으로 작성되었습니다.
  - 이로 인해 CLI 명령어를 친 프로세스가 종료되지 않고 계속 터미널을 점유하는 구조가 되었습니다.

### Root Cause 2: CLI 옵션 시맨틱의 오해 및 비직관성

1. **`--dry-run`의 비직관성**:
   - 일반적인 CLI 유저나 LLM은 `--dry-run`을 주면 "파라미터/템플릿의 문법 유효성을 검증하고 즉시 0으로 종료"할 것으로 기대합니다.
   - 하지만 기존 구현은 "새 이벤트가 올 때까지 대기하다가, 이벤트가 도착하면 실제 실행만 건너뛰는" 방식이어서 이벤트가 올 때까지 무한 블로킹되었습니다.
2. **`--once`의 비직관성**:
   - 에이전트는 `--once`가 "과거 이벤트 1개를 읽거나 즉시 종료"하는 것으로 오해했습니다.
   - 실제로는 "미래의 새 이벤트가 1개 발생할 때까지 대기"하는 동작이어서, 보드에 변경이 없자 무한 대기했습니다.

### Root Cause 3: 자율 에이전트 런타임(`workspace__runShell`)과의 상호작용 충돌

- AI 에이전트는 배치 형태(수 초 내에 결과를 stdout으로 리턴)로 쉘 도구를 실행합니다.
- 동기 타임아웃(10~15초)을 초과하면 백그라운드 전환 안내 메시지(`Sync timeout handoff`)가 발생하는데, 에이전트의 LLM 프롬프트/추론 엔진은 이를 "명령어 멈춤(Hanging)"으로 오인하고 즉시 kill 하는 악순환을 유발했습니다.

---

## 4. 목표 아키텍처 및 원칙 (Target Architecture & Principles)

### 📌 에이전트 인터랙션 대원칙 (Architecture Rule)
> **"에이전트가 호출하는 모든 설정/구독/위임 CLI 명령어는 절대 포그라운드에서 블로킹되지 않아야 하며, 반드시 1초 이내에 등록 결과를 반환하고 정상 종료(Exit 0)되어야 한다."**

### 시퀀스 다이어그램

```text
[에이전트 / CLI]                                [AK5 Gateway 서버]
      │                                                │
      ├──── 1. ak5 subscribe --exec "echo ..." ───────►│
      │        (POST /api/v1/subscriptions)            │
      │                                                ├─ 2. subscriptions 테이블에 등록
      ◄──── 3. {"status": "registered", "id": "sub-1"} ┤
      │        (0.1초 만에 exit 0 리턴)                 │
      │                                                │
      │                                    [티켓 변경 이벤트 발생]
      │                                                │
      │                                                ▼
      │                                    3. 살아있는 서버 프로세스가
      │                                       저장된 명령어를 쉘로 직접 실행
      │                                       (asyncio.create_subprocess_shell)
```

---

## 5. 핵심 설계 교훈 및 안티패턴 (Key Lessons & Anti-Patterns)

### 1. 범용 셸 커맨드 실행 위임 (유닉스 철학 및 무한 확장성)
- **안티패턴**: 특정 도구나 하네스를 지원하기 위해 `--to-agy`, `--to-cursor`, `--to-claude` 같은 전용 플래그나 `notify_session.py` 같은 임시 땜질 스크립트를 서버/CLI에 계속 추가하는 행위.
  - 이 방식은 수십, 수백 개의 에이전트 하네스/도구가 등장할 때마다 코드가 기하급수적으로 오염되고 유지보수가 불가능해집니다.
- **정답 (Unix Philosophy)**:
  - AK5는 특정 하네스에 종속되지 않습니다.
  - 구독 시 오직 **표준 셸 커맨드 문자열(`--exec '<CMD>'`)**만을 등록받고, 이벤트 발생 시 명령을 **literal로** 실행하며 페이로드는 env(`$AK5_*`)와 stdin JSON으로 전달합니다.
  - 이로써 `agy --conversation <ID> -p "$AK5_SUMMARY"`, `cursor agent -p "$AK5_SUMMARY"`, `curl … -d @-`, 로컬 훅 스크립트 등 현존하는 모든 도구와 상호운용됩니다.

### 2. 일관된 CLI 서브커맨드 네임스페이스
- **안티패턴**: `subscriptions`(목록), `unsubscribe`(삭제), `subscribe`(등록)처럼 최상위 명령어를 파편화하는 것.
- **정답**:
  - `ak5 subscribe` 단일 그룹 하위에 일관된 서브커맨드(`create`/`add`, `list`/`ls`, `remove`/`rm`, `watch`)를 배치합니다.
  - CLI `--help`만으로도 "이 명령어는 상주형이 아니라 서버에 훅을 등록하고 즉시 반환(Register & Return)하는 비차단(Non-blocking) 아키텍처"임을 명확히 인지할 수 있도록 문서화합니다.

---

## 6. 회귀 방지 조치 및 결과 (Action Items & Final Resolution)

| 우선순위 | 영역 | 과제 내용 | 상태 | 완료 증적 |
| :--- | :--- | :--- | :--- | :--- |
| **P0** | **Backend API** | `POST /api/v1/subscriptions` (등록), `GET /api/v1/subscriptions` (목록), `DELETE /api/v1/subscriptions/{id}` (삭제) 엔드포인트 및 SQLite 영속화 | **Completed** | `backend/src/ak5/routers/subscriptions.py` |
| **P0** | **Event Engine** | `event_bus.publish()` 시 등록된 훅을 비동기 조회하여 매칭되는 셸 명령어를 서버 백그라운드 태스크로 자동 실행 | **Completed** | `backend/src/ak5/services/subscription_service.py` |
| **P0** | **CLI 통일** | `ak5 subscribe`를 통합 그룹으로 개편 (`create`/`add`, `list`/`ls`, `remove`/`rm`, `watch`). 등록 즉시 0 리턴 | **Completed** | `backend/src/ak5/cli/commands/subscribe.py` |
| **P1** | **E2E 실측 검증** | 서브에이전트가 티켓 생성/이동 시 서버가 등록된 셸 명령(`agy --conversation ...`)을 백그라운드 비동기 트리거함을 실시간 확인 | **Completed** | 0.28초 등록, 백그라운드 프로세스 정상 트리거 완료 |
| **P1** | **Regression Tests** | 등록 즉시 비차단 리턴, env/stdin 훅 계약, 라이프사이클 전체 검증 | **Completed** | `backend/tests/` 전체 통과 |
| **P1** | **Rules/Docs** | `.agents/rules/cli-nonblocking-rule.md` 제정 및 `docs/MANUAL.md` 최신화 | **Completed** | 룰셋 및 매뉴얼 반영 완료 |
