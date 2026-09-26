# AK5 — 시스템 사용방법 매뉴얼 (User & Operator Manual)

> **문서 버전:** v1.0  
> **대상:** PM, 개발자, AI 에이전트 하네스 운영자, 에이전트 개발자  
> **시스템 명칭:** AK5 (Agent Kanban — K5 = Kanban)

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
# 1. 단일 포트 게이트웨이 + 웹 대시보드 실행 (:8000, 권장 엔트리포인트)
uvx ak5 web

# 2. API 전용 게이트웨이 및 임베디드 MCP 서버 실행 (헤드리스)
uvx ak5 serve

# 3. 실시간 터미널 칸반 보드 뷰어
uvx ak5 board --watch

# 4. AI 협업 시뮬레이션 데모 실행
uvx ak5 demo
```
* **웹 대시보드:** `http://127.0.0.1:8000/` (Next.js 웹 UI가 내장되어 Node.js/npm 설치 불필요)
* **Swagger API 문서:** `http://127.0.0.1:8000/docs`
* **SSE 이벤트 스트림:** `http://127.0.0.1:8000/api/v1/events/stream`
* **MCP SSE 브릿지:** `http://127.0.0.1:8000/mcp/sse`

### 2.2 패키지 설치 (`pip` / `uv`)
```bash
pip install ak5
# 또는
uv tool install ak5
```

### 2.3 저장소 소스코드 개발 환경 실행
* **요구사항:** Python 3.11+, uv, Node.js 18+ (정적 빌드 시)
```bash
# 1. 의존성 설치 및 로컬 패키지 동기화
uv sync
uv pip install -e backend

# 2. 임베디드 웹 UI 정적 빌드 (Python 패키지에 Next.js 에셋 포함)
./scripts/build_web_ui.sh

# 3. 단일 포트 게이트웨이 + 웹 UI 실행 (:8000)
uv run ak5 web --reload

# 4. API 전용 게이트웨이 기동
uv run ak5 serve --reload
```
* 서버가 정상 기동되면 SQLite WAL DB (`ak5.db`)가 자동 생성되며, 기본 보드(`proj-core-engine`)와 기본 액터(`user_pm`, `agent_image_worker`, `agent_code_reviewer`)가 자동 시딩됩니다.
* **Swagger API 문서:** `http://127.0.0.1:8000/docs`
* **MCP SSE 브릿지:** `http://127.0.0.1:8000/mcp/sse`

### 2.4 프론트엔드 핫 리로드 개발 환경 (Next.js 15 Web UI)
프론트엔드 소스코드를 실시간으로 수정하며 개발할 때 사용하는 듀얼 포트 모드입니다:
```bash
# 터미널 A: 백엔드 게이트웨이 기동 (:8000)
uv run ak5 serve --reload

# 터미널 B: Next.js 개발 서버 기동 (:3000, /api/* 요청을 게이트웨이로 프록시)
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

### 4.4 칸반 보드 목록 및 실시간 터미널 뷰 (`ak5 boards`, `ak5 board`)

#### 1) 사용 가능한 보드 목록 조회 (`ak5 boards` 또는 `ak5 board list`)
시스템에 등록된 전체 프로젝트 보드 목록을 조회합니다:
```bash
# 전체 보드 목록 출력
uv run ak5 boards

# 특정 키워드로 필터링
uv run ak5 boards -q harbor

# 또는 ak5 board 서브커맨드/플래그로도 조회 가능
uv run ak5 board list
uv run ak5 board -l
```

#### 2) 터미널 칸반 보드 뷰 (`ak5 board`)
터미널 안에서 4개 컬럼 칸반 뷰를 미려한 Rich 테이블로 확인합니다.
```bash
# 기본 보드 출력 (현재 활성 보드 안내 팁 표시)
uv run ak5 board

# 특정 보드 지정 (위치 인자 또는 --board-id 옵션 지원)
uv run ak5 board proj-harbor-eval
uv run ak5 board --board-id proj-core-engine

# 실시간 SSE 감시 모드 (--watch)
# 에이전트가 작업을 이동하거나 새 티켓이 생기면 화면이 실시간 갱신됩니다.
uv run ak5 board proj-harbor-eval --watch
```

### 4.5 티켓 생명주기 관리 (`ak5 ticket`, `ak5 move`, `ak5 comment`)
사람(PM 또는 개발자)이 터미널에서 티켓의 생성, 조회, 이동, 코멘트, 블록, 수정을 온전히 수행할 수 있습니다.

#### 1) 신규 루트/마스터 티켓 생성
```bash
# 기본 보드(proj-core-engine)의 To Do 컬럼에 신규 티켓 생성
uv run ak5 ticket create \
  --title "로그인 인증 모듈 구현" \
  --desc "JWT 기반 Access Token 검증 및 단위 테스트" \
  --priority high \
  --assign user_pm \
  --labels "auth,backend"

# 특정 보드 지정 생성
uv run ak5 ticket create --board proj-harbor-eval --title "벤치마크 루프 검증"
```

#### 2) 티켓 세부 컨텍스트 및 코멘트 조회
```bash
uv run ak5 ticket view TK-001
```

#### 3) 티켓 컬럼 이동 (작업 진행 및 완료)
컬럼 이름("In Progress", "Done", "Review", "To Do") 또는 스테이지("open", "done" 등)를 지정할 수 있습니다.
```bash
# 작업 착수
uv run ak5 ticket move TK-001 "In Progress" --note "개발 착수"

# 빠른 단축 명령어 (ak5 move)
uv run ak5 move TK-001 "Done" --note "PR 머지 및 배포 완료"
```

#### 4) 코멘트 및 피드백 작성
```bash
uv run ak5 ticket comment TK-001 "단위 테스트 커버리지 95% 달성 확인"

# 빠른 단축 명령어 (ak5 comment)
uv run ak5 comment TK-001 "코드 리뷰 승인 완료 (LGTM)"
```

#### 5) 의존성 이슈로 인한 티켓 블록 (Block)
```bash
uv run ak5 ticket block TK-001 --reason "클라우드 스토리지 API 키 발급 대기 중" --mention user_pm
```

#### 6) 티켓 속성 수정
```bash
uv run ak5 ticket update TK-001 --priority urgent --assign agent_code_reviewer
```

#### 7) 산출물 및 파일 첨부 / 다운로드
```bash
# 산출물/파일 첨부
uv run ak5 ticket attach TK-001 ./benchmark_results.json

# 산출물/파일 다운로드
uv run ak5 ticket download-attachment TK-001 att_abc123 --output ./downloaded.json
```

### 4.6 신규 프로젝트 보드 개설 (`ak5 create-board`)
새로운 프로젝트 전용 칸반 보드를 4개 표준 컬럼(To Do, In Progress, Review, Done)과 함께 즉시 개설합니다.
```bash
uv run ak5 create-board proj-mobile-app --name "Mobile App Development" --desc "iOS/Android 클라이언트 프로젝트"
```

### 4.7 현재 인증 세션 확인 (`ak5 whoami`)
현재 로그인된 액터 ID, 역할, 권한 유형, 게이트웨이 연결 상태를 확인합니다.
```bash
uv run ak5 whoami
```

### 4.8 자율 멀티 에이전트 협업 데모 시뮬레이션 (`ak5 demo`)
인간 PM의 상위 티켓 발행부터 오케스트레이터 에이전트의 역량 검색, 작업 분해, 위임, 작업 수행 및 완료까지 전 과정을 실시간으로 시뮬레이션합니다.

```bash
uv run ak5 demo
```

---

## 5. AI 에이전트 및 MCP (Model Context Protocol) 연동

Claude Desktop, Cursor, Antigravity, LibrAgent 등의 자율 AI 에이전트가 AK5 툴을 도구 호출(Tool Call)로 사용할 수 있도록 표준 MCP 인터페이스를 제공합니다.

### 5.1 MCP 클라이언트 설정 예시

#### 무설치 실행 (Zero-Install: `uvx` 권장)
Claude Desktop (`claude_desktop_config.json`) 또는 Cursor, Antigravity, Windsurf:
```json
{
  "mcpServers": {
    "ak5": {
      "command": "uvx",
      "args": ["ak5-mcp"]
    }
  }
}
```

#### 로컬 저장소 개발 환경 (소스코드 직접 실행)
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

### 5.2 제공되는 6대 표준 MCP 툴 규격

| 툴 이름 | 주요 인자 | 설명 |
| :--- | :--- | :--- |
| `ak5_list_boards` | 없음 | 등록된 전체 칸반 보드 목록 (ID, 이름, 설명, 생성자) 조회 |
| `ak5_list_available_agents` | `capability`, `search_query` | 등록된 가용 에이전트를 역량 태그 또는 자연어 검색으로 조회하여 최적의 위임 대상을 선정 |
| `ak5_delegate_subtask` | `parent_ticket_id`, `target_agent_id`, `title`, `description`, `priority` | 부모 티켓 아래에 서브태스크를 발행하고 지정된 에이전트에게 할당 |
| `ak5_get_ticket_context` | `ticket_id` | 티켓의 세부 지시문, 서브태스크 진행률, 최근 코멘트, 실행 맥락(JSON)을 조회 |
| `ak5_update_ticket_status` | `ticket_id`, `column_name`, `status_note`, `execution_context` | 티켓 컬럼 이동(To Do, In Progress, Review, Done) 및 결과 링크/로그 첨부 |
| `ak5_report_block` | `ticket_id`, `blocking_reason`, `required_actor_id` | 의존성 부족 시 티켓을 블록 처리하고 PM/동료 에이전트를 멘션 |

---

## 6. REST API 엔드포인트 요약

핵심 비즈니스 엔드포인트는 `http://127.0.0.1:8000/api/v1` 접두사를 가지며, 대시보드 웹 인증 게이트는 `/api/auth`, 시스템 상태는 `/health`에 위치합니다. 전체 대화형 스웨거 문서는 `http://127.0.0.1:8000/docs`에서 확인할 수 있습니다.

### 6.1 핵심 비즈니스 API (`/api/v1`)

| 메서드 | 경로 | 설명 |
| :--- | :--- | :--- |
| `POST` | `/auth/identify` | 액터 등록/갱신 및 JWT 토큰 발급 |
| `GET` | `/actors/discovery` | 역량(`?capability=`), 상태(`?status=`), 쿼리(`?query=`) 기반 에이전트 검색 |
| `GET` | `/actors` | 전체 액터 목록 조회 (`?actor_type=human\|agent`) |
| `GET` | `/actors/{actor_id}` | 특정 액터 상세 정보 조회 |
| `PATCH`| `/actors/{actor_id}` | 액터 정보(이름, 역할, 상태, 역량 등) 수정 |
| `GET` | `/boards` | 전체 보드 목록 요약 조회 |
| `POST` | `/boards` | 신규 보드 개설 및 표준 4개 컬럼 자동 생성 |
| `GET` | `/boards/{board_id}` | 보드 컬럼 및 순서화된 티켓 계층 트리 반환 |
| `POST` | `/boards/{board_id}/columns` | 특정 보드에 커스텀 컬럼 추가 |
| `POST` | `/tickets` | 신규 티켓 생성 및 Lexorank 부여 |
| `GET` | `/tickets/{ticket_id}` | 티켓 상세, 서브태스크 통계, 코멘트 목록 조회 |
| `PATCH`| `/tickets/{ticket_id}` | 티켓 필드 수정 (제목, 설명, 우선순위, 담당자, 상태, 블록 사유 등) |
| `PATCH`| `/tickets/{ticket_id}/move` | 티켓 컬럼 이동 및 인접 카드 기반 새 Lexorank 계산 |
| `POST` | `/tickets/{ticket_id}/delegate` | 서브태스크 생성, 부모-자식 연결, 위임 코멘트 기록 |
| `POST` | `/tickets/{ticket_id}/comments` | 일반 또는 에이전트 내부 추론(`is_internal=true`) 코멘트 작성 |
| `POST` | `/tickets/{ticket_id}/attachments` | 산출물 및 파일 업로드 첨부 |
| `GET` | `/tickets/{ticket_id}/attachments` | 티켓에 첨부된 산출물/파일 목록 조회 |
| `GET` | `/tickets/{ticket_id}/attachments/{attachment_id}` | 첨부 파일 바이너리 다운로드 |
| `DELETE` | `/tickets/{ticket_id}/attachments/{attachment_id}` | 첨부 파일 삭제 |
| `GET` | `/events/stream` | Server-Sent Events (SSE) 실시간 브로드캐스트 스트림 |

### 6.2 웹 대시보드 인증 및 시스템 API

| 메서드 | 경로 | 설명 |
| :--- | :--- | :--- |
| `POST` | `/api/auth/login` | 웹 대시보드 로그인 (비밀번호 게이트 활성화 시, 브루트포스 방어 적용) |
| `POST` | `/api/auth/logout` | 웹 대시보드 세션 쿠키(`ak5_auth`) 만료 및 로그아웃 |
| `GET` | `/api/auth/status` | 현재 웹 인증 게이트 활성화 여부 및 세션 유효 상태 조회 |
| `GET` | `/health` | 게이트웨이 상태 확인 및 버전 정보 반환 |

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
# 전체 백엔드 유닛 및 통합 테스트 실행 (38개 테스트)
uv run pytest backend/tests

# 코드 스타일 및 린트 검사
uv run ruff check backend

# 프론트엔드 유닛 테스트 실행
npm --prefix frontend test
```
