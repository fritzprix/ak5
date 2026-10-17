# AK5 Identity & Tenancy Spec — Workspace, User, Agent

Status: Draft · 2026-10-17 · 관련: [workflow.md](workflow.md), [events.md](events.md), [cli.md](cli.md) · Issue #21

## 0. 원칙

1. **사람의 신원은 사람의 경로로만 만든다.** User는 표준 OAuth 2.0 / OpenID Connect 로그인으로만 생성·인증된다.
2. **신원 종류는 타입이다.** 사람(User)과 agent(Agent)는 서로 다른 principal이다. 클라이언트가 자기 종류를 신고하는 필드는 없다.
3. **agent는 혼자 존재할 수 없다.** 모든 Agent는 그것을 승인한 User와 Workspace에 묶인다.
4. **표준만 사용한다.** OIDC Authorization Code + PKCE, OAuth 2.0 Device Authorization Grant (RFC 8628), Bearer token (RFC 6750), 토큰 폐기 (RFC 7009).
5. **local / self-hosted / hosted가 같은 모델과 같은 코드 경로를 쓴다.** 차이는 설정뿐이다.

## 1. 모델

```
User (global)                      사람. upstream IdP의 (issuer, subject)로 식별
 └─ Membership ──► Workspace       role: owner | admin | member
                    ├─ Agent        service principal. created_by = User
                    │   │           workflow_role: triage | worker | gatekeeper (고정)
                    │   │           capabilities[]
                    │   └─ AgentCredential   token_hash, board_scope[], expires_at, revoked_at
                    ├─ Board[]      kind: inbox (workspace당 정확히 1개) | standard
                    │   └─ Ticket[] → revisions, reviews, postmortems
                    ├─ Subscription[]
                    └─ Event log
```

| 테이블 | 핵심 필드 |
|---|---|
| `users` | `user_id`, `email`, `display_name`, `created_at` |
| `user_identities` | `user_id`, `issuer`, `subject` (unique), `email_verified` |
| `workspaces` | `workspace_id`, `name`, `policy` (JSON, §6) |
| `memberships` | `workspace_id`, `user_id`, `role` |
| `agents` | `agent_id`, `workspace_id`, `name`, `workflow_role`, `capabilities`, `created_by` (user_id), `disabled_at` |
| `agent_credentials` | `credential_id`, `agent_id`, `token_hash`, `board_scope`, `expires_at`, `revoked_at`, `last_used_at` |
| `user_sessions` | `session_id`, `user_id`, `refresh_token_hash`, `client` (`web`\|`cli`), `expires_at`, `revoked_at` |

모든 workspace 소유 테이블(boards, columns, tickets, revisions, reviews, postmortems, events, subscriptions, idempotency_keys, audit)은 `workspace_id`를 가진다.

## 2. Principal과 Role 판정

서버가 서명한 access token의 claim:

```json
{ "sub": "usr_… | agt_…", "principal_type": "user | agent", "ws": "ws_…",
  "role": "triage | worker | gatekeeper",   // agent만
  "scope": "…", "exp": 0, "iat": 0, "jti": "…" }
```

- workflow role 판정은 **이 하나의 규칙**뿐이다:
  - `principal_type == "user"` 이고 해당 workspace의 member → `Role.HUMAN`
  - `principal_type == "agent"` → `agents.workflow_role` (토큰 claim과 DB 값이 다르면 거부)
- 기존 `actors.actor_type`, `actors.role`은 workflow 판정에 쓰지 않는다 (마이그레이션 §8 이후 제거).
- `POST /auth/identify` (자기 등록)는 **제거**한다. 로컬 개발용으로만 `AK5_DEV_INSECURE_IDENTIFY=1`일 때 loopback 요청에 한해 허용한다.

## 3. User 인증 (OAuth 2.0 / OIDC)

AK5 서버(FastAPI)가 **OIDC Relying Party**이자 자기 API의 **토큰 발급자**다.

### 3.1 Upstream IdP

- 설정으로 여러 provider 등록: `AK5_OIDC_PROVIDERS` (issuer URL, client_id, client_secret, scopes).
- OIDC discovery(`/.well-known/openid-configuration`)를 지원하는 provider면 무엇이든 사용 가능 (Google, Microsoft Entra, Okta, Auth0, Keycloak, Clerk 등).
- GitHub처럼 OIDC가 아닌 OAuth 2.0 provider는 `userinfo` 어댑터로 지원 (`issuer = "https://github.com"`, `subject = GitHub user id`).
- `email_verified`가 아닌 계정은 초대 매칭에 사용하지 않는다.

### 3.2 웹 로그인

1. `GET /auth/login?provider=<id>` → Authorization Code + **PKCE** + `state` + `nonce`로 IdP 리다이렉트
2. `GET /auth/callback` → code 교환, ID token 검증 (서명, `iss`, `aud`, `nonce`, `exp`)
3. `(issuer, subject)`로 User 조회·생성 → 세션 쿠키 (`HttpOnly`, `Secure`, `SameSite=Lax`) + CSRF 토큰
4. 상태를 바꾸는 웹 요청은 CSRF 검증

### 3.3 CLI 로그인 (사람)

`ak5 login` → **Device Authorization Grant (RFC 8628)**:

1. CLI: `POST /oauth/device_authorization` (`client_id=ak5-cli`, `principal=user`) → `device_code`, `user_code`, `verification_uri`
2. 사람: 브라우저에서 `verification_uri` 열기 → §3.2 웹 로그인 → `user_code` 확인 화면에서 승인
3. CLI: `POST /oauth/token` (`grant_type=urn:ietf:params:oauth:grant-type:device_code`) 폴링 → user access token (15분) + refresh token (회전, 최대 12시간, 갱신에 브라우저 재로그인 불필요하지만 12시간 후 재로그인)

## 4. Agent 신원

Agent는 **User가 승인해야만** 생긴다. 두 가지 경로:

### 4.1 Device grant로 등록 (권장)

```
ak5 login --agent <name> --role worker [--caps ..] [--workspace ..]
```

1. CLI: `POST /oauth/device_authorization` (`principal=agent`, 요청 name/role/caps)
2. 사람: 브라우저에서 로그인 → 승인 화면에서 **workspace, workflow_role, capabilities, board 범위, 만료**를 확인·수정 후 승인 (승인자는 workspace `admin` 이상)
3. CLI(daemon): 토큰 폴링 → `Agent` + `AgentCredential` 생성, agent 토큰 수령 → **daemon credential store**에 profile로 저장 ([client.md](client.md) §3)

### 4.2 UI에서 발급

workspace admin이 웹 UI에서 Agent를 만들고 토큰을 발급 → agent 환경에 `AK5_TOKEN`으로 주입. 토큰은 발급 시 한 번만 표시.

### 4.3 Agent 토큰

- opaque 토큰 (`ak5_agt_…`), DB에는 해시만 저장. 매 요청 시 해시 조회 → 짧은 access JWT로 교환하거나 직접 검증.
- `board_scope`가 비어 있지 않으면 해당 board(와 inbox) 밖의 ticket에 대한 쓰기는 `ROLE_FORBIDDEN`.
- 폐기: UI 또는 `POST /oauth/revoke` (RFC 7009). 폐기 즉시 효력 (opaque 조회 기반).
- agent는 User 토큰, 다른 Agent, Membership을 만들 수 없다.

## 5. 권한 요약

| 행위 | User (member) | User (admin/owner) | Agent |
|---|---|---|---|
| T1 create / human 전이 (T15, T16, T17, T21, B3) | ✅ | ✅ | ❌ |
| triage / worker / gatekeeper 전이 | ❌ | ❌ | `workflow_role`에 따라 |
| board 생성 | ✅ | ✅ | triage만 (route의 `create_board`, `max_auto_boards`) |
| Agent 승인·발급·폐기 | ❌ | ✅ | ❌ |
| Membership 관리 | ❌ | ✅ | ❌ |

## 6. Workspace 정책

`workspaces.policy` (JSON):

| 키 | 기본 | 의미 |
|---|---|---|
| `require_user_presence_for_approval` | `false` | T15(done 승인)에 **passkey(WebAuthn) 확인**을 요구. 켜면 human 승인은 웹 UI에서만 가능하고 CLI human verdict approve는 `ROLE_FORBIDDEN`. |
| `allowed_idp_issuers` | `[]` (전부 허용) | 로그인 허용 issuer 목록 |
| `allowed_email_domains` | `[]` | 초대 없이 가입 허용할 이메일 도메인 |
| `max_auto_boards` | 50 | triage의 board 자동 생성 한도 |

passkey 확인은 spec에 정의하되 구현은 선택 기능(후속 이슈)으로 한다.

**남는 위험**: 사람이 자기 user 토큰을 agent에게 넘기는 내부자 위임은 구조로 막을 수 없다. user 토큰의 짧은 수명, `require_user_presence_for_approval`, audit의 승인자 기록으로 대응한다.

## 7. Workspace와 다른 spec의 관계

- **Inbox는 workspace마다 하나**다 (workflow.md §2.1 개정). `board_id`는 workspace 안에서 고유하며, inbox는 API에서 `inbox` 별칭으로 접근한다 (`GET /boards/inbox/next`는 토큰의 workspace 기준).
- ticket 번호(`TK-…`)는 workspace 안에서 순번을 매긴다.
- 이벤트 log, subscription, long-poll은 토큰의 workspace 범위로 제한된다 (events.md). 다른 workspace의 이벤트는 절대 반환하지 않는다.
- 모든 쿼리는 workspace 범위를 강제한다. Postgres 배포에서는 Row-Level Security를 이중 방어로 건다 (#20).

## 8. 배포 모드

| 모드 | IdP | Workspace | 비고 |
|---|---|---|---|
| local (`ak5 serve`) | 기본: 내장 **local owner** (첫 실행 시 생성, loopback에서만 비밀번호 로그인). 설정 시 OIDC | `local` 하나 자동 생성 | 설치 없이 바로 사용 |
| self-hosted | OIDC provider 1개 이상 필수 | 여러 개 | 비-loopback bind 시 OIDC 미설정이면 시작 거부 (cli.md §4.1) |
| hosted (Vercel 등) | OIDC 필수 | 여러 개, 사용자가 생성 | Postgres + Blob (#20) |

### 8.1 local / self-hosted에서의 OIDC

local 서버도 같은 OIDC 경로를 쓸 수 있다. upstream IdP는 redirect만 브라우저를 통해 받으므로 **서버가 인터넷에 공개될 필요는 없다** (브라우저와 서버가 IdP에 나가는 연결만 필요).

| 접속 형태 | redirect URI | 근거 |
|---|---|---|
| 같은 머신 (`127.0.0.1` bind) | `http://127.0.0.1:<port>/auth/callback` 또는 `http://localhost:<port>/auth/callback` | loopback은 https 예외 (RFC 8252 §7.3, Google·GitHub·Entra 등 지원) |
| Tailscale | `https://<machine>.<tailnet>.ts.net/auth/callback` (`ak5 serve --tailscale-serve`) | ts.net 인증서로 https |
| LAN / 사설 도메인 | `https://<host>/auth/callback` (TLS 필수) | 일반 web client |

- 서버는 `AK5_PUBLIC_URL`로 callback URL을 만든다 (없으면 bind 주소에서 유도).
- **redirect가 불가능한 환경**(헤드리스 서버, 등록 불가 URL)에서는 upstream **device flow**를 사용한다: GitHub OAuth App과 Google("TV and Limited Input") 모두 RFC 8628을 지원하므로 AK5 서버가 IdP에 device flow를 시작하고, 사람은 IdP 화면에서 코드를 입력한다. redirect URI 등록이 필요 없다.
- OAuth client 등록: self-hosted 운영자가 자기 OAuth App을 등록하고 `AK5_OIDC_PROVIDERS`에 넣는다. 설치형 앱 관례에 따라 프로젝트가 공용 public client(PKCE, secret 없음 또는 비밀로 취급하지 않는 secret)를 제공하는 것은 후속 결정으로 남긴다.
- 오프라인: OIDC 미설정 시 내장 local owner (loopback 전용)로 동작한다.

### 8.2 local의 위협 모델

local에서 agent가 **사람과 같은 OS 사용자 권한**으로 돌면, agent는 DB 파일과 JWT 서명 키(`AK5_DATA_DIR`)를 직접 읽고 쓸 수 있으므로 어떤 인증 방식도 우회된다. local에서 "done은 사람만"을 보장하려면:

1. 서버 데이터 디렉터리를 agent가 접근할 수 없게 한다 — agent를 다른 OS 사용자·컨테이너·sandbox에서 실행하거나, 서버를 다른 머신(원격/Tailscale)에 둔다.
2. 그리고/또는 `require_user_presence_for_approval`(passkey)을 켠다 — 단, (1) 없이는 DB 직접 수정을 막지 못한다.

`ak5 serve`는 같은 OS 사용자로 실행 중인 agent 환경(`AK5_AGENT=1` 등)이 감지되면 이 한계를 경고한다.

## 9. v1 마이그레이션

- `local` workspace 생성.
- `actors.actor_type = 'human'` → `users` + `memberships(owner/member)`. `user_identities`는 비어 있으며, 첫 로그인 시 local owner 또는 이메일 일치로 연결.
- `actors.actor_type = 'agent'` → `agents` (`workflow_role`은 기존 role이 4개 값 중 하나면 그대로, 아니면 `worker`), `created_by` = workspace owner. **credential은 발급하지 않는다** — 재로그인(§4.1) 필요.
- 기존 JWT는 모두 무효화된다 (`jti` 미보유 + 새 claim 형식).
- `tickets.created_by`, `assigned_to`, `claimed_by`, audit의 actor 참조는 `principal_id`(`usr_…`/`agt_…`)로 변환.

## 10. 에러 코드 추가

| 코드 | HTTP | 상황 |
|---|---|---|
| `UNAUTHENTICATED` | 401 | 토큰 없음 / 만료 / 폐기 |
| `WORKSPACE_FORBIDDEN` | 403 | 토큰의 workspace 밖 리소스 접근 |
| `USER_PRESENCE_REQUIRED` | 403 | 정책상 passkey 확인 필요 |
| `AUTHORIZATION_PENDING` / `SLOW_DOWN` / `EXPIRED_TOKEN` / `ACCESS_DENIED` | 400 | RFC 8628 device grant 표준 응답 |

CLI 종료 코드: `UNAUTHENTICATED` → 8, `WORKSPACE_FORBIDDEN`·`USER_PRESENCE_REQUIRED` → 4.
