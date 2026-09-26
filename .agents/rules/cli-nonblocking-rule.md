# Rule: Agent-Facing CLI & Subscription Architecture

## 1. 핵심 원칙: "Register & Return" (Non-blocking Principle)

1. **상주 프로세스(Server)가 실행 주체**:
   - 시스템에는 이미 백그라운드에 상시 구동 중인 AK5 Gateway(FastAPI 서버 프로세스)가 존재한다.
   - 따라서 이벤트 감지 및 후속 작업 실행의 주체는 **상시 살아있는 서버 프로세스**여야 한다.

2. **CLI는 오직 등록(Registration)만 수행하고 즉시 리턴**:
   - `ak5 subscribe`를 비롯하여 에이전트가 호출하는 모든 설정, 구독, 훅 등록 CLI 명령어는 **결코 포그라운드에서 블로킹(무한 대기)되어서는 안 된다**.
   - 명령어는 서버 API에 실행할 규칙(명령어 텍스트, 보드 ID, 필터 등)을 등록하고 1초 이내에 `exit 0`으로 즉시 반환되어야 한다.

3. **이벤트 트리거 시의 실행**:
   - 이벤트 발생 시 등록된 명령어 텍스트는 서버 프로세스가 로컬 쉘(`asyncio.create_subprocess_shell`)로 비동기 실행한다.
   - 외부 웹훅 연동이 필요한 경우 `curl` 명령어 텍스트를 등록한다.

4. **터미널 관측(Observation)과의 분리**:
   - 개발자가 터미널에서 화면을 띄워두고 실시간으로 이벤트를 지켜보는 도구(스트리밍 뷰어)는 `ak5 watch` 또는 `ak5 tail`과 같이 명시적으로 분리한다.
   - `ak5 subscribe`는 **자동화 액션(Action Trigger)** 등록 용도로만 사용한다.

## 2. 회귀 방지 체크리스트 (Regression Prevention)
- [ ] `ak5 subscribe` 실행 시 터미널/쉘 프로세스가 종료되지 않고 대기하는가? ➔ **절대 금지 (Defect)**
- [ ] `--dry-run` 플래그는 SSE 스트림 연결 없이 즉시 문법 유효성을 검증하고 리턴하는가?
- [ ] 에이전트 도구(`workspace__runShell`) 호출 시 타임아웃 없이 즉시 결과 JSON/텍스트를 수신하는가?
- [ ] 관련 Postmortem 참조: `docs/postmortem-20260927-subscribe-architecture.md`
