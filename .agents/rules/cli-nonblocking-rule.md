# Rule: Agent-Facing CLI & Subscription Architecture

## 1. 핵심 원칙: "서버는 기록, daemon이 실행" (Register & Return)

1. **서버는 기록만 한다**:
   - AK5 서버는 상태 머신, claim/lease, 이벤트 log를 기록하고 강제한다. 클라이언트 쪽 명령을 실행하지 않는다 (server-side exec hook 없음, #11).
   - 서버는 profile당 subscription 하나(consumer = client daemon)에 long-poll로 이벤트를 넘긴다 ([client.md](../../docs/spec/client.md) §4).

2. **client daemon이 실행 주체**:
   - 이벤트 수신, spool, 로컬 구독자 fan-out, hook 실행은 OS 사용자당 하나인 `ak5 daemon`이 맡는다.
   - hook은 exec 문자열 하나로 통일한다. daemon이 이벤트마다 셸로 실행하고, 이벤트 데이터는 env(`$AK5_EVENT`, `$AK5_TICKET_ID`, …)와 stdin JSON으로만 넘긴다. 명령 문자열에 보간하지 않는다.
   - 외부 웹훅·HTTP harness는 `curl … -d @-`를 exec로 등록한다 (`--preset curl:<url>`).

3. **CLI는 등록만 하고 즉시 리턴**:
   - `ak5 subscribe add|list|remove`를 비롯해 agent가 호출하는 설정·등록 명령은 **포그라운드에서 블로킹되면 안 된다**. daemon에 규칙을 저장하고 1초 이내에 `exit 0`으로 반환한다.
   - 구독 성공 정의: `ak5 subscribe ls`에 행이 있음 (배경 PID나 stdout 배너가 아님).

4. **대기는 명시적 원시 연산으로만**:
   - 할 일을 기다려야 하면 `ak5 next --wait <sec>` 또는 `ak5 events wait --timeout <sec>`를 쓴다. 둘 다 daemon의 로컬 long-poll을 쓰고 시간 제한이 있으며, 서버 연결을 늘리지 않는다.
   - agent가 직접 poll 루프 스크립트나 배경 SSE 프로세스를 만들지 않는다.

5. **터미널 관측과의 분리**:
   - 사람이 화면에 띄워 두고 보는 도구는 `ak5 events watch`(또는 `ak5 watch`), `ak5 board --watch`이다.
   - agent/harness 런타임(비-TTY, `AK5_AGENT=1` 등)에서 `events watch` / `watch`를 실행하면 **exit 2**로 거부한다.

## 2. 회귀 방지 체크리스트 (Regression Prevention)
- [ ] `ak5 subscribe add` 실행 후 쉘이 종료되지 않고 대기하는가? ➔ **절대 금지 (Defect)**
- [ ] `--dry-run`은 daemon·서버에 연결하지 않고 즉시 hook 계약을 출력하고 리턴하는가?
- [ ] 서버 코드에 클라이언트 명령을 실행하는 경로(subprocess)가 다시 생기지 않았는가?
- [ ] `ak5 next --wait` / `ak5 events wait`는 timeout 후 exit 6으로 끝나는가?
- [ ] 관련 문서: [client.md](../../docs/spec/client.md) §4, [cli.md](../../docs/spec/cli.md) §4.6, `docs/postmortem-20260927-subscribe-architecture.md` (v1 배경)
