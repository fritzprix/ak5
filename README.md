# AK5 (Agent K5) — Agent-Orchestrated Kanban System

AK5는 인간 사용자(PM, 개발자)와 자율 AI 에이전트(LLM Agents)가 단일 칸반(K5) 인터페이스 위에서 실시간으로 협업하고 작업을 위임(Delegation) 및 추적하는 **Agent-Orchestrated Kanban 시스템**입니다.

---

## 1. 주요 특징

* **Actor 추상화 일원화 (Unified Actor Model):** 인간과 AI 에이전트를 동일한 `Actor` 인터페이스로 취급 (`user_pm`, `agent_image_worker` 등).
* **역량 기반 발견 및 위임 (Capability-driven Discovery & Delegation):** 에이전트 역량 태그(`image-resize`, `code-review`, `security` 등) 및 자연어 검색 기반으로 작업 위임.
* **듀얼 프로토콜 (REST + SSE + MCP):**
  * **Web UI (Next.js 15):** 드래그 앤 드롭(`@dnd-kit`), SSE 실시간 보드 자동 동기화, 계층형 서브태스크 진행률 바 및 아코디언 토글, 에이전트 활동 펄스(Activity Glow).
  * **AI Agents (MCP):** Model Context Protocol 표준 툴셋(`ak5_list_available_agents`, `ak5_delegate_subtask`, `ak5_get_ticket_context`, `ak5_update_ticket_status`, `ak5_report_block`).
  * **CLI (`ak5`):** 터미널 환경에서 액터 로그인, 에이전트 검색, 서브태스크 위임, 실시간 터미널 칸반 뷰(`ak5 board --watch`), 협업 시뮬레이션 데모(`ak5 demo`).
* **동시성 및 순서 정렬 보장:** Lexorank 알고리즘(Base36)을 통한 충돌 없는 임의 순서 삽입, SQLite WAL 모드 + Busy Timeout 설정.

---

## 2. 프로젝트 디렉토리 레이아웃

```
ak5/
├── backend/                     # FastAPI + MCP Server
│   ├── src/ak5/
│   │   ├── main.py              # FastAPI 진입점 & Lifespan & MCP 마운트
│   │   ├── config.py            # Pydantic Settings
│   │   ├── database.py          # SQLite WAL 비동기 세션
│   │   ├── models/              # SQLAlchemy 2.0 모델 (Actor, Board, Column, Ticket, Audit)
│   │   ├── schemas/             # Pydantic v2 DTO 스키마
│   │   ├── services/            # Lexorank, Discovery, EventBus (SSE)
│   │   ├── routers/             # REST API (auth, actors, boards, tickets, events)
│   │   └── mcp/                 # MCP Server (MCPServer, tools, client)
│   └── tests/                   # Pytest 종합 단위/통합 테스트 (19개)
│
├── frontend/                    # Next.js 15 Web UI (React 18, Tailwind, @dnd-kit)
│   ├── src/
│   │   ├── app/                 # App Router (layout.tsx, page.tsx, globals.css)
│   │   ├── components/
│   │   │   ├── board/           # KanbanBoard, KanbanColumn, TicketCard
│   │   │   └── actor/           # ActorBadge
│   │   └── lib/                 # SSE 구독 및 API 클라이언트
│   └── package.json
│
├── cli/                         # ak5 터미널 도구
│   └── src/ak5_cli/
│       ├── main.py              # Click CLI 엔트리포인트
│       ├── config.py            # 세션 및 API URL 관리
│       └── commands/            # login, agents, delegate, board, demo
│
├── docker-compose.yml           # 컨테이너 오케스트레이션
└── pyproject.toml               # uv 기반 워크스페이스 설정
```

---

## 3. 실행 방법

### 3.1 사전 요구사항
* Python 3.11+
* [uv](https://github.com/astral-sh/uv)
* Node.js 18+ 및 npm

### 3.2 백엔드 (FastAPI & MCP Gateway) 실행
```bash
# 가상환경 생성 및 의존성 설치
uv sync
uv pip install -e backend -e cli

# 백엔드 서버 기동 (기본 포트 8000)
uv run uvicorn ak5.main:app --host 127.0.0.1 --port 8000 --reload
```
* **Swagger API 문서:** `http://127.0.0.1:8000/docs`
* **MCP SSE 엔드포인트:** `http://127.0.0.1:8000/mcp/sse`

### 3.3 프론트엔드 (Next.js 15 Web Dashboard) 실행
```bash
cd frontend
npm install
npm run dev
```
* **웹 대시보드:** `http://localhost:3000`

### 3.4 CLI (`ak5`) 도구 사용법
```bash
# 1. 액터 등록 및 로그인
uv run ak5 login --id "agent_code_reviewer" --role "Senior Reviewer" --caps "python,rust,security"

# 2. 역량 기반 에이전트 검색
uv run ak5 agents --cap "image-resize"

# 3. 작업 위임 실행
uv run ak5 delegate TK-001 \
    --to agent_image_worker \
    --title "WebP 썸네일 변환기 구현" \
    --desc "150x150 WebP 포맷 변환 함수 작성"

# 4. 실시간 터미널 칸반 뷰
uv run ak5 board --watch

# 5. 자율 멀티 에이전트 협업 데모 시뮬레이션 실행
uv run ak5 demo
```

---

## 4. MCP (Model Context Protocol) 툴셋

외부 에이전트(Claude Desktop, Cursor, LibrAgent 등)에서 다음 5가지 표준 툴을 호출하여 AK5 칸반을 직접 조작할 수 있습니다:

1. `ak5_list_available_agents(capability, search_query)`: 가용 에이전트 역량 검색
2. `ak5_delegate_subtask(parent_ticket_id, target_agent_id, title, description, priority)`: 하위 티켓 발급 및 에이전트 위임
3. `ak5_get_ticket_context(ticket_id)`: 티켓 세부사항, 서브태스크 진척도, 최근 코멘트, 실행 맥락 조회
4. `ak5_update_ticket_status(ticket_id, column_name, status_note, execution_context)`: 티켓 상태 전이 및 산출물 기록
5. `ak5_report_block(ticket_id, blocking_reason, required_actor_id)`: 티켓 블록 처리 및 PM/담당자 멘션

---

## 5. 테스트 검증

```bash
# 백엔드 및 CLI 전체 단위/통합 테스트 실행
uv run pytest backend/tests
```
19개의 모든 테스트 케이스(Lexorank 보간/리밸런싱, Actor 식별 및 검색, 티켓 수명주기, 계층형 서브태스크 위임, SSE 이벤트 버스, MCP 툴셋, CLI 명령어)가 통과합니다.
