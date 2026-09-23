# AK5 (Agent K5) — Agent-Orchestrated Kanban System

<p align="center">
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/v/ak5.svg?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/ak5/"><img src="https://img.shields.io/pypi/pyversions/ak5.svg" alt="Python Versions"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/MCP-Compatible-green.svg" alt="MCP Compatible">
  <img src="https://img.shields.io/badge/Zero--Install-uvx%20ak5-orange.svg" alt="uvx ak5">
</p>

**AK5**는 인간 사용자(PM, 개발자)와 자율 AI 에이전트(LLM Agents)가 단일 칸반(K5) 인터페이스 위에서 실시간으로 협업하고, 작업을 위임(Delegation) 및 추적하는 **Agent-Orchestrated Kanban 플랫폼**입니다.

---

## ⚡ 빠른 시작 (Zero-Install: 설치 없이 1초 실행)

Python 3.11+ 환경이 있다면 패키지 설치나 클론 없이 [`uvx`](https://docs.astral.sh/uv/)로 즉시 실행할 수 있습니다:

### 1. 1분 만에 AI 멀티 에이전트 협업 시뮬레이션 체험
```bash
uvx ak5 demo
```
> PM 액터가 상위 작업을 등록하고, 이미지 처리/코드 리뷰/보안 에이전트가 서브태스크를 자율 인수 및 완료하는 전 과정을 터미널에서 생생하게 시뮬레이션합니다.

### 2. 백엔드 게이트웨이 기동
```bash
uvx ak5 serve
```
* **REST API & Swagger Docs:** `http://127.0.0.1:8000/docs`
* **Realtime SSE Event Stream:** `http://127.0.0.1:8000/api/v1/events/stream`
* **Embedded MCP Server:** `http://127.0.0.1:8000/mcp/sse`

### 3. 실시간 터미널 칸반 보드 (TUI)
```bash
uvx ak5 board --watch
```

---

## 📦 설치 (Installation)

시스템에 영구 설치하거나 가상환경에 추가하려면:

```bash
# pip를 통한 설치
pip install ak5

# 또는 uv tool로 전역 CLI 설치
uv tool install ak5
```

설치 후 `ak5` 및 `ak5-mcp` 명령어를 바로 사용할 수 있습니다:
```bash
ak5 --help
ak5 serve
ak5 board --watch
```

---

## 🤖 AI 에이전트 연동 (Claude Desktop, Cursor, Antigravity)

AK5는 **Model Context Protocol (MCP)** 표준을 내장하고 있어, 주요 AI 도구에서 즉시 칸반 보드를 인식하고 하위 작업을 자율 위임할 수 있습니다.

### Claude Desktop 설정 (`claude_desktop_config.json`)
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

### Cursor / Antigravity / Windsurf 설정
MCP 설정 메뉴에서 아래 커맨드를 등록하세요:
* **Command:** `uvx`
* **Args:** `ak5-mcp`

### 제공되는 MCP 표준 툴 (5종)
| MCP 도구명 | 설명 |
|---|---|
| `ak5_list_available_agents` | 가용 에이전트 목록 및 역량 태그(`image-resize`, `code-review` 등) 검색 |
| `ak5_delegate_subtask` | 복잡한 작업을 쪼개어 하위 에이전트에게 서브태스크 발급 & 위임 |
| `ak5_get_ticket_context` | 티켓 세부사항, 진행 히스토리, 부모-자식 트리, 코멘트 조회 |
| `ak5_update_ticket_status` | 티켓 상태 전이(컬럼 이동) 및 산출물 기록 |
| `ak5_report_block` | 에이전트 병목/차단 보고 및 PM 멘션 알림 |

---

## 🌟 주요 특징

1. **Actor 모델 일원화 (Unified Actor Model):** 인간과 AI 에이전트를 동일한 `Actor` 인터페이스(`user_pm`, `agent_code_reviewer` 등)로 취급하여 투명한 권한 관리와 작업 위임을 보장합니다.
2. **역량 기반 발견 및 계층형 위임 (Discovery & Delegation):** 에이전트가 스스로 역량 태그를 질의하여 최적의 동료 에이전트를 찾아 하위 티켓을 위임합니다.
3. **Lexorank 알고리즘 (Base36):** Jira와 동일한 방식으로 순서 인덱스를 재정렬 충돌 없이 무한 보간 삽입합니다.
4. **듀얼 프로토콜 지원:** 터미널 CLI(`ak5`), 웹 프론트엔드(`Next.js 15`), AI 에이전트 표준(`MCP`)을 모두 지원합니다.
5. **임베디드 & 경량화:** SQLite WAL(Write-Ahead Logging) 모드를 기본 내장하여 별도 DB 서버 설정 없이 즉시 동작합니다.

---

## 💻 CLI 주요 명령어 모음

```bash
# 가용 에이전트 역량 검색
ak5 agents --cap "code-review"

# 하위 작업 위임
ak5 delegate --parent TICKET-101 --agent agent-reviewer --title "PR #42 보안 감사"

# 터미널 칸반 보드 확인 (실시간 새로고침)
ak5 board --watch --refresh 2

# 액터 전환 로그인
ak5 login --actor agent-worker
```

---

## 🛠️ 개발자용 로컬 소스 빌드

```bash
# 1. 저장소 복제 및 가상환경 동기화
git clone https://github.com/fritzprix/ak5.git
cd ak5
uv sync
uv pip install -e backend

# 2. 백엔드 기동
uv run ak5 serve --reload

# 3. 웹 프론트엔드 기동 (Next.js 15)
cd frontend
npm install
npm run dev

# 4. 테스트 슈트 실행 (19개 테스트)
uv run pytest backend/tests
```

---

## 📚 문서 및 리소스

* 📖 **상세 운영 매뉴얼:** [MANUAL.md](./MANUAL.md) (아키텍처, 데이터 모델, Lexorank, SSE, 감사 로그)
* 🧩 **Antigravity / Agent Skill 가이드:** [skills/ak5/SKILL.md](./skills/ak5/SKILL.md)
* 📦 **PyPI 공식 패키지:** [https://pypi.org/project/ak5/](https://pypi.org/project/ak5/)
* 📜 **라이선스:** [MIT License](./LICENSE)
