# Postmortem: `--exec` must wake the harness (2026-09-27)

- **관련:** [PM-20260927-01](postmortem-20260927-subscribe-architecture.md), [PM-20260927-02](postmortem-20260927-subscribe-watch-misuse.md)
- **범위:** **AK5 저장소만** (외부 하네스 스킬/문서는 이 PR에 포함하지 않음)

## 증상

에이전트가 `ak5 subscribe create`는 등록했지만 `--exec`에 `ticket view` / move-only / echo만 넣고, “Gateway stdout이 채팅으로 안 온다 ⇒ 콜백 불가”로 단정한 뒤 clock 폴링으로 도피함.

## 원인

`--exec` placeholder가 “셸 아무거나”로 읽힘. Register 성공(`subscribe ls`)과 **하네스 wake**가 분리되어 있지 않음. claim/work 단계가 hook 본문으로 오해됨.

## AK5 수정 범위 (이 변경셋)

| 포함 | 파일 | 내용 |
| :--- | :--- | :--- |
| ✅ | `frontend/src/lib/agentSetup.ts` | `--exec` 계약, A1 register / A2 when woken, 폴링 last-resort |
| ✅ | `skills/ak5/SKILL.md` | 동일 계약 + anti-pattern |
| ✅ | `backend/src/ak5/cli/commands/subscribe.py` | help / `--exec` help / create 성공 패널 문구 |
| ✅ | `docs/MANUAL.md` §4.9 | `--exec` 계약·성공 정의 (섹션 국소 패치만) |

| 제외 | 이유 |
| :--- | :--- |
| ❌ 외부 하네스 제품 스킬/문서 | 별도 저장소 — 이 변경셋에 포함하지 않음 |
| ❌ 특정 하네스 HTTP URL 하드코딩 | 하네스 비종속 유지 (`agent -p` / curl webhook / 로컬 스크립트 예시만) |

## 계약 (한 줄)

`--exec` = **에이전트 런타임 wake** (`$AK5_*` / stdin). claim/move/work = **깨운 세션**. Gateway stdout ≠ chat.
