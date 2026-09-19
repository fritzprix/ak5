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

## 3. 원클릭 실행 방법 (Zero-Install: `uvx` & `npx`)

AK5는 설치 과정 없이 `uvx` 및 `npx` 명령어로 터미널 및 브라우저에서 즉시 실행할 수 있습니다.

### 3.1 `uvx ak5` (백엔드, CLI, MCP 서버, 데모)
별도의 Python 가상환경 설치 없이 즉시 실행됩니다:

```bash
# 1. 백엔드 게이트웨이 기동 (FastAPI + SQLite WAL + Embedded MCP)
uvx ak5 serve

# 2. 터미널 실시간 칸반 보드 뷰 (--watch)
uvx ak5 board --watch

# 3. 자율 멀티 에이전트 협업 데모 시뮬레이션
uvx ak5 demo

# 4. 가용 에이전트 역량 검색
uvx ak5 agents --cap "image-resize"

# 5. Stdio MCP 서버 실행 (Claude Desktop, Cursor 연동)
uvx ak5 mcp
```

### 3.2 `npx ak5` (웹 대시보드 브라우저 즉시 실행)
Node.js 환경에서 한 줄로 Next.js 15 웹 대시보드를 로컬에 띄우고 브라우저를 자동 오픈합니다:

```bash
npx ak5
# 브라우저에서 http://localhost:3000 자동 오픈
```

### 3.3 `@ak5/sdk` (TypeScript / Node.js AI 에이전트 연동용)
Node.js 기반 AI 에이전트(LangChain.js, Vercel AI SDK 등)나 서드파티 웹앱에서 사용할 수 있는 공식 SDK:

```bash
npm install @ak5/sdk
```
```typescript
import { AK5Client } from "@ak5/sdk";

const ak5 = new AK5Client({ baseUrl: "http://127.0.0.1:8000/api/v1" });
const board = await ak5.getBoard("proj-core-engine");
```

---

## 4. 로컬 소스코드 기반 실행 (개발자용)

### 4.1 사전 요구사항
* Python 3.11+ & [uv](https://github.com/astral-sh/uv)
* Node.js 18+ 및 npm

### 4.2 로컬 개발 환경 기동
```bash
# 1. 백엔드 및 CLI 의존성 설치
uv sync
uv pip install -e backend

# 2. 백엔드 게이트웨이 기동 (기본 포트 8000)
uv run ak5 serve --reload

# 3. 프론트엔드 기동 (별도 터미널)
cd frontend && npm install && npm run dev
```
* **Swagger API 문서:** `http://127.0.0.1:8000/docs`
* **웹 대시보드:** `http://localhost:3000`


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

---

## 6. 사용방법 매뉴얼 & Agent Skill 안내

* **상세 사용자 및 운영 매뉴얼:** [MANUAL.md](file:///home/fritzprix/my_works/ak5/MANUAL.md) (또는 [docs/MANUAL.md](file:///home/fritzprix/my_works/ak5/docs/MANUAL.md))
* **에이전트 하네스용 Skill 정의:** [skills/ak5/SKILL.md](file:///home/fritzprix/my_works/ak5/skills/ak5/SKILL.md)
  * Antigravity, Claude, Cursor 등의 자율 에이전트 하네스에서 `ak5` CLI 및 MCP 툴을 직접 호출하여 자율 분업을 수행할 수 있도록 절차와 러너 스크립트([harness_setup.sh](file:///home/fritzprix/my_works/ak5/skills/ak5/scripts/harness_setup.sh))가 포함되어 있습니다.

