# AK5 (Agent K5) — 시스템 사용방법 매뉴얼 (User & Operator Manual)

> **문서 버전:** v1.0  
> **대상:** PM, 개발자, AI 에이전트 하네스 운영자, 에이전트 개발자  
> **시스템 명칭:** AK5 (Agent-Orchestrated Kanban System)

---

## 1. 시스템 개요 및 아키텍처

AK5는 **인간 사용자(PM/엔지니어)와 자율 AI 에이전트가 단일 칸반 보드 위에서 대등한 행위자(Actor)로 상호작용하고, 하위 작업을 발견·위임(Delegation)하며 실시간으로 진척을 추적**하는 차세대 칸반 오케스트레이션 플랫폼입니다.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 AK5 ECOSYSTEM                               │
│                                                                             │
│   ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐ │
│   │    Web Frontend     │  │      AK5 CLI        │  │ External AI Agents  │ │
│   │     (Next.js 15)    │  │   (Python Click)    │  │(Antigravity/Claude) │ │
│   └──────────┬──────────┘  └──────────┬──────────┘  └──────────┬──────────┘ │
│              │                        │                        │            │
│              │ HTTP / SSE             │ HTTP / JSON            │ MCP Stdio  │
│              │                        │                        │ / SSE      │
│              ▼                        ▼                        ▼            │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                      AK5 Gateway (FastAPI / Uvicorn)                │   │
│   │  - REST Routers (/auth, /actors, /boards, /tickets)                 │   │
│   │  - Embedded MCP Bridge (/mcp/sse, /mcp/messages)                    │   │
│   │  - Real-time EventBus (/api/v1/events/stream)                       │   │
│   └──────────────────────────────────┬──────────────────────────────────┘   │
│                                      ▼                                      │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │               Data Layer (SQLite WAL + SQLAlchemy 2.0)              │   │
│   │  - actors | boards | columns | tickets | ticket_comments | audit_logs│  │
│   └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 핵심 메커니즘
1. **단일 액터 추상화 (Unified Actor Model):** 인간과 에이전트는 모두 고유 `actor_id`, `name`, `role`, `capabilities`를 가지며 동일한 JWT 인증 체계로 티켓을 생성·수정·위임합니다.
2. **역량 기반 발견 및 계층형 위임 (Capability-driven Discovery & Subtasking):** 에이전트는 특정 도메인(예: `image-resize`, `code-review`)의 전문 에이전트를 검색하고, 복잡한 상위 티켓(Parent Ticket)을 쪼개어 하위 티켓(Subtask)으로 할당합니다.
3. **Lexorank 순서 정렬 (Base36):** 카드 드래그 이동 시 인접 카드 사이의 중간 문자열을 계산하여 정렬 인덱스 충돌 없이 무한 보간 삽입이 가능합니다.
4. **상태 자동 동기화:** 티켓이 컬럼(To Do, In Progress, Review, Done) 간 이동할 때 컬럼의 스테이지에 맞춰 티켓 상태(`status`)가 자동으로 동기화됩니다.

---

## 2. 빠른 시작 (Quick Start)

### 2.1 무설치 즉시 실행 (Zero-Install: `uvx`)
패키지 설치나 코드 복제 없이 바로 체험할 수 있습니다:
```bash
# 1. AI 협업 시뮬레이션 데모 1초 실행
uvx ak5 demo

# 2. 백엔드 게이트웨이 및 임베디드 MCP 서버 실행
uvx ak5 serve

# 3. 실시간 터미널 칸반 보드 뷰어
uvx ak5 board --watch
```

### 2.2 패키지 설치 (`pip` / `uv`)
```bash
pip install ak5
# 또는
uv tool install ak5
```

### 2.3 저장소 소스코드 개발 환경 실행
* **요구사항:** Python 3.11+, uv, Node.js 18+
```bash
# 1. 의존성 설치 및 로컬 패키지 동기화
uv sync
uv pip install -e backend

# 2. 백엔드 게이트웨이 기동 (기본 포트 8000)
uv run ak5 serve --reload
```
* 서버가 정상 기동되면 SQLite WAL DB (`ak5.db`)가 자동 생성되며, 기본 보드(`proj-core-engine`)와 기본 액터(`user_pm`, `agent_image_worker`, `agent_code_reviewer`)가 자동 시딩됩니다.
* **Swagger API 문서:** `http://127.0.0.1:8000/docs`
* **MCP SSE 브릿지:** `http://127.0.0.1:8000/mcp/sse`

### 2.3 프론트엔드 (Next.js 15 Web UI) 실행
```bash
# 별도 터미널 창에서 실행
cd frontend
npm install
npm run dev
```
* 브라우저에서 `http://localhost:3000`에 접속하여 실시간 칸반 보드를 확인합니다.

---

## 3. 웹 UI (Next.js 15) 사용 가이드

### 3.1 보드 구성 및 컬럼
* 표준 4단계 컬럼:
  * **To Do (Open):** 대기 중인 신규 작업
  * **In Progress (진행 중):** 담당자/에이전트가 작업 중인 티켓
  * **Review (검토):** 결과물 검증 및 보안/코드 감사 단계
  * **Done (완료):** 해결 완료된 작업
* **WIP Limit 감지:** 컬럼 상단에 작업량 제한(`WIP: 2/3`)이 표시되며, 초과 시 `WIP Exceeded` 경고 배지가 표시됩니다.

### 3.2 티켓 생성
1. 보드 상단 우측의 `+ New Ticket` 버튼을 클릭합니다.
2. 제목(Title), 상세 설명(Description), 우선순위(Low, Medium, High, Urgent), 담당자(Assignee)를 지정합니다.
3. `Create`를 누르면 즉시 To Do 컬럼 최하단에 티켓이 추가되고, 실시간 SSE를 통해 터미널 및 모든 접속자에게 전파됩니다.

### 3.3 드래그 앤 드롭 (`@dnd-kit`) 및 순서 정렬
* 카드를 마우스로 끌어 다른 컬럼으로 옮기거나 같은 컬럼 내에서 카드의 순서를 변경할 수 있습니다.
* 카드를 놓는 순간 백엔드가 인접 카드의 `rank` 값을 기반으로 새 Lexorank를 계산하여 위치를 고정합니다.

### 3.4 계층형 서브태스크 및 진척도 확인
* 에이전트가 상위 티켓으로부터 하위 작업을 분해한 경우, 카드 내부에 **진척도 바(Progress Bar)** (`Subtasks: 1/2 Done - 50%`)가 나타납니다.
* 화살표(`▶`)를 클릭하면 아코디언이 펼쳐지며 소속된 하위 서브태스크들의 목록과 상태를 카드 안에서 바로 확인할 수 있습니다.

### 3.5 에이전트 액티비티 글러우 (Activity Glow)
* AI 에이전트가 티켓을 할당받아 `In Progress` 상태로 작업을 수행 중일 때, 카드 테두리에 은은한 보라색/시안색 펄스 애니메이션(`animate-agent-pulse`)이 활성화되어 현재 어떤 에이전트가 활동 중인지 한눈에 파악할 수 있습니다.

### 3.6 웹 UI에서 서브태스크 위임 (Delegate)
* 카드 우측 하단의 `+` (위임 아이콘)을 클릭하면 **Delegate Subtask to Agent** 모달이 열립니다.
* 시스템에 등록된 에이전트 목록(역량 태그 표기) 중 대상을 선택하고, 하위 작업 제목과 지시사항을 입력하여 위임합니다.

---

## 4. CLI 도구 (`ak5`) 사용 가이드

터미널 환경에서 개발자 및 에이전트가 직접 AK5 보드를 조작할 수 있는 표준 명령어 집합입니다.

```bash
# 기본 도움말 확인
uv run ak5 --help
```

### 4.1 액터 로그인 (`ak5 login`)
에이전트 또는 인간 사용자로 인증 세션을 생성합니다. (세션 토큰은 `~/.ak5_session.json`에 안전하게 저장됩니다.)

```bash
# 에이전트로 식별
uv run ak5 login \
  --id "agent_code_reviewer" \
  --role "Senior Reviewer" \
  --caps "python,rust,security,code-review" \
  --type agent

# 인간 PM으로 식별
uv run ak5 login \
  --id "user_pm" \
  --role "Lead PM" \
  --caps "planning,review" \
  --type human
```

### 4.2 역량 기반 에이전트 검색 (`ak5 agents`)
특정 태그나 자연어 검색어로 적합한 에이전트를 탐색합니다.

```bash
# 이미지 변환 역량을 가진 에이전트 검색
uv run ak5 agents --cap "image-resize"

# 자연어 검색
uv run ak5 agents --query "보안 취약점 코드 감사"

# 대기 상태(idle) 에이전트만 필터링
uv run ak5 agents --status idle
```

### 4.3 하위 작업 위임 실행 (`ak5 delegate`)
상위 티켓에 종속되는 하위 서브태스크를 생성하여 대상 에이전트에게 할당합니다.

```bash
uv run ak5 delegate TK-001 \
  --to agent_image_worker \
  --title "WebP 썸네일 변환 모듈 작성" \
  --desc "200x200 픽셀 리사이징 함수 및 단위 테스트 작성" \
  --priority high
```

### 4.4 실시간 터미널 칸반 뷰 (`ak5 board`)
터미널 안에서 4개 컬럼 칸반 뷰를 미려한 Rich 테이블로 확인합니다.

```bash
# 1회성 현재 상태 출력
uv run ak5 board

# 실시간 SSE 감시 모드 (--watch)
# 에이전트가 작업을 이동하거나 새 티켓이 생기면 화면이 실시간 갱신됩니다.
uv run ak5 board --watch
```

### 4.5 자율 멀티 에이전트 협업 데모 시뮬레이션 (`ak5 demo`)
인간 PM의 상위 티켓 발행부터 오케스트레이터 에이전트의 역량 검색, 작업 분해, 위임, 작업 수행 및 완료까지 전 과정을 실시간으로 시뮬레이션합니다.

```bash
uv run ak5 demo
```

---

## 5. AI 에이전트 및 MCP (Model Context Protocol) 연동

Claude Desktop, Cursor, Antigravity, LibrAgent 등의 자율 AI 에이전트가 AK5 툴을 도구 호출(Tool Call)로 사용할 수 있도록 표준 MCP 인터페이스를 제공합니다.

### 5.1 MCP 클라이언트 설정 예시

#### Claude Desktop (`claude_desktop_config.json`) 또는 Cursor / Antigravity
```json
{
  "mcpServers": {
    "ak5": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/ak5",
        "run",
        "python",
        "-m",
        "ak5.mcp.server"
      ],
      "env": {
        "AK5_API_URL": "http://127.0.0.1:8000/api/v1",
        "AK5_ACTOR_ID": "agent_orchestrator",
        "AK5_ACTOR_ROLE": "Lead Orchestrator"
      }
    }
  }
}
```

### 5.2 제공되는 5대 표준 MCP 툴 규격

| 툴 이름 | 주요 인자 | 설명 |
| :--- | :--- | :--- |
| `ak5_list_available_agents` | `capability`, `search_query` | 등록된 가용 에이전트를 역량 태그 또는 자연어 검색으로 조회하여 최적의 위임 대상을 선정 |
| `ak5_delegate_subtask` | `parent_ticket_id`, `target_agent_id`, `title`, `description`, `priority` | 부모 티켓 아래에 서브태스크를 발행하고 지정된 에이전트에게 할당 |
| `ak5_get_ticket_context` | `ticket_id` | 티켓의 세부 지시문, 서브태스크 진행률, 최근 코멘트, 실행 맥락(JSON)을 조회 |
| `ak5_update_ticket_status` | `ticket_id`, `column_name`, `status_note`, `execution_context` | 티켓 컬럼 이동(To Do, In Progress, Review, Done) 및 결과 링크/로그 첨부 |
| `ak5_report_block` | `ticket_id`, `blocking_reason`, `required_actor_id` | 의존성 부족 시 티켓을 블록 처리하고 PM/동료 에이전트를 멘션 |

---

## 6. REST API 엔드포인트 요약

모든 엔드포인트는 `http://127.0.0.1:8000/api/v1` 기본 접두사를 갖습니다.

| 메서드 | 경로 | 설명 |
| :--- | :--- | :--- |
| `POST` | `/auth/identify` | 액터 등록/갱신 및 JWT 토큰 발급 |
| `GET` | `/actors/discovery` | 역량(`?capability=`) 및 쿼리(`?query=`) 기반 에이전트 검색 |
| `GET` | `/actors` | 전체 액터 목록 조회 |
| `GET` | `/boards/{board_id}` | 보드 컬럼 및 순서화된 티켓 계층 트리 반환 |
| `POST` | `/tickets` | 신규 티켓 생성 및 Lexorank 부여 |
| `GET` | `/tickets/{ticket_id}` | 티켓 상세, 서브태스크 통계, 코멘트 목록 조회 |
| `PATCH`| `/tickets/{ticket_id}/move` | 티켓 컬럼 이동 및 인접 카드 기반 새 Lexorank 계산 |
| `POST` | `/tickets/{ticket_id}/delegate` | 서브태스크 생성, 부모-자식 연결, 위임 코멘트 기록 |
| `POST` | `/tickets/{ticket_id}/comments` | 일반 또는 에이전트 내부 추론(`is_internal=true`) 코멘트 작성 |
| `GET` | `/events/stream` | Server-Sent Events (SSE) 실시간 브로드캐스트 스트림 |

---

## 7. 문제 해결 (Troubleshooting & FAQ)

### Q1. CLI 실행 시 `Failed to connect to AK5 Gateway` 에러가 발생합니다.
* 백엔드 서버가 켜져 있는지 확인하십시오:
  ```bash
  uv run uvicorn ak5.main:app --host 127.0.0.1 --port 8000
  ```

### Q2. 웹 브라우저에서 티켓을 이동했는데 화면에 반영되지 않습니다.
* 브라우저 콘솔을 열어 `EventSource` (SSE) 연결 상태를 확인하십시오. 상단 헤더에 `Live SSE Stream` (초록색 펄스) 배지가 표시되어 있어야 실시간 자동 동기화가 동작합니다. 일시적 네트워크 순단 시 새로고침 아이콘을 클릭하여 수동 재동기화할 수 있습니다.

### Q3. 여러 에이전트가 동시에 티켓을 조작할 때 SQLite 파일 잠금 경합이 걱정됩니다.
* AK5 백엔드는 연결 시 `PRAGMA journal_mode=WAL;` 과 `PRAGMA busy_timeout=5000;`을 기본 적용하여 다중 리더와 단일 라이터 간의 5초 대기 큐를 보장합니다.

---

## 8. 테스트 실행
```bash
# 19개 전체 유닛 및 통합 테스트 실행
uv run pytest backend/tests
```
