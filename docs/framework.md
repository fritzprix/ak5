# AK5 Workflow Framework

**AK5 (Agent Kanban)** is a board for running work through an **adversarial agent loop** with a **fallibilist** stance and **appetite** limits. This document defines the domain-agnostic workflow framework (**Axis 1**).

- **Three axes:**
  1. **Workflow and framework** *(this document)* — domain-general task operation (software, writing, legal, tax, HR, and other enterprise work).
  2. **Loop skills** — how agents execute the rules each tick; skills read rules from the kanban rather than hardcoding policy.
  3. **Kanban (`ak5`)** — where rules are enforced and recorded.
- For the verified mapping onto current `ak5` (`master` / `2.0.2`) and the **canonical gap list**, see **issue #194**.

---

## 1. Loop ↔ Kanban Interface

A loop is stateless between ticks; the kanban is the source of truth. Each tick, a loop:

- **Reads:**
  - **Workspace halt / freeze state** — halt/freeze signals are distinct from "no work available" so loops stop cleanly when halted.
  - **Versioned org config** — teams (one board per team), pipeline-stage order, roles, sensitive-area ownership rules, decision criteria, and appetite defaults (with optional overrides).
  - **Next claimable ticket** (`next`) on the loop's target board(s) or inbox, excluding blocked tickets (`dependency`, `external`, `escalation`, `needs_human`) — **never** excluding tickets with an **attention flag**.
  - **Ticket context** — specification, acceptance criteria, `execution_context` (including submitted `deliverable` and `refutation`), `TicketComment` thread, `attempt` / `max_attempts`, and status fields (whose turn, reviewed vs. current artifact version, policy version).
  - **Precedents** — prior refutations, postmortems, and consolidated policy versions (queried before verdict; explicitly withheld for `audit` by default).
  - **Project decision board** — pending or newly decided decision cards (for `pm` / `tm` interpretation).
- **Writes:**
  - **Claim / lease** (`claim`, `heartbeat`, `release`) — activity-aware claim-holder writes extend or protect the active lease.
  - **Deliverable** (`submit`) — attaches a falsifiable claim (`summary`, `artifacts`, `criteria_self_check`, and `refutation.method`).
  - **Verdict** (`verdict`) — `approve` (baseline met) or `reject` (with findings or `misrouted`), citing the policy version.
  - **Postmortem** (`postmortem`) — root cause and revised spec/criteria after human rejection or reopen.
  - **Handoff / routing** — `route` from inbox, formal cross-board handoff (1:1 move or child tickets via `parent_ticket_id`), backward return with reason, or `tm` accept/reject by executability.
  - **Infeasible stop** — stops in place with a reason, no deliverable, and no bounce to inbox.
  - **Attention & blocking** — non-blocking `attention` flag (does not stop pull, claim, or review), blocking overlay (`dependency` / `external` / `escalation`), or workspace `halt`.
  - **Comments & decision cards** — `TicketComment` updates (including `tm` interpretation of project decisions) and decision-board cards when important-matter criteria trigger.

---

## 2. Glossary

Use **pipeline stage** and **ticket stage** distinctly; never use bare "stage" where both could apply.

| Term | Definition |
| --- | --- |
| **Pipeline stage** | A **team board**. Each team owns its own board; moving work to the next pipeline stage is a cross-board **handoff**. |
| **Ticket stage** | A ticket's in-board state: `unclassified` \| `backlog` \| `in_progress` \| `agent_review` \| `postmortem_pending` \| `human_review` \| `done`. |
| **Ticket** | The work card holding specification, acceptance criteria, lease/claim state, attempt count, and comment history. |
| **Deliverable** | The worker's submitted claim (`summary`, `artifacts`, `criteria_self_check`, and required `refutation.method`). |
| **Refutation** | The falsifiability check (`refutation.method`: what observation or check would show the claim is false) required on every deliverable. |
| **Verdict** | A gatekeeper or human judgment (`approve` or `reject` with findings/reason) recorded against a deliverable and policy version. |
| **Claim / Lease** | Time-bounded exclusive lock (`claim_token`, `claimed_by`, `lease_expires_at`) held by an actor while executing a ticket stage. |
| **Attention flag** | A non-blocking flag (`attention_flag`, `attention_reason`) asking a human to look at a ticket without stopping pull, claim, or review. |
| **Blocked overlay** | A hard stop (`dependency`, `external`, `escalation`, `needs_human`) that removes a ticket from `next` and forbids `claim` until unblocked. |
| **Appetite** | The work budget before forced escalation (`E1`). Default: **3 iterations** (`max_attempts`), no time cap, tokens recorded but uncapped. |
| **Precedent** | A recorded refutation or postmortem outcome that future reviews query, can supersede, and consolidate into policy weekly. |
| **Disagreement vs. Anomaly** | **Disagreement**: human rejects after gatekeeper approve (triggers postmortem + precedent). **Anomaly**: a gatekeeper gives a different verdict on the *same* deliverable under the *same* policy version. |
| **Halt / Freeze** | A workspace-wide stop signal distinct from "no work available"; triggers one deduplicated decision-board card per halt episode. |

---

## 3. Roles

Roles are split into **current** (`ak5` `2.0.2`) and **proposed** roles (tracked in #194):

### Current Roles
- **`triage`** — Processes `unclassified` tickets in the inbox and routes them (`route`) to a board with a refined spec and acceptance criteria.
- **`worker`** (Maker) — Claims `backlog` tickets, executes work, and submits a falsifiable `deliverable` (or bounces misrouted work / stops in place if infeasible).
- **`gatekeeper`** (Refuter) — Independently reviews `agent_review` deliverables (separation of duties: submitter ≠ reviewer) and submits `postmortem` on `postmortem_pending`.
- **`human`** — Final approver at `human_review` (`done`), reopens `done` tickets, unblocks `escalation` (`B3`) with a `resolution`, and decides project decision-board cards.

### Proposed Roles
- **`pm`** (Org-level) — Manages inbox/triage, epic splits, backward-flow policy, and decision requests on the project decision board.
- **`tm`** (Team Manager, per board) — Accepts or rejects incoming tickets by **executability** (can this board carry the work forward from the ticket/deliverable alone?), approves tickets onto the board's `backlog`, sends reviewed tickets to the next pipeline-stage board, interprets project decisions onto team tickets, and escalates to `pm`.
- **`audit`** (External, read-only) — Read-only reviewer over tickets, deliverables, verdicts, and logs; **default precedent read is explicitly withheld** to preserve independent refutation.

---

## 4. Lifecycle of Tickets, Deliverables, and Handoffs

1. **Intake & Deduplication:** Work enters `unclassified` (inbox) with a source key (origin + content hash) so duplicate filings are rejected while changed content can be re-filed.
2. **In-Board Execution (`ticket stage`):**
   - `backlog` → `in_progress` (`claim`) → `agent_review` (`submit` with `refutation.method`) → `human_review` (`gatekeeper` `approve`) → `done` (`human` `approve`, allowed **only on the final pipeline-stage board**).
   - **Approve = baseline, not release.** A gatekeeper `approve` means the deliverable survived baseline checks; it does not prove truth or authorize release.
   - **Pre-review refutation check:** A deliverable lacking `refutation.method` is rejected without content review and returned to `backlog` without incrementing `attempt`.
   - **Infeasible work:** If work cannot proceed, the worker stops **in place** with a reason, no deliverable, and no bounce to inbox.
   - **Activity-aware claims:** Any claim-holder write on the ticket protects/extends the active lease; idle expired leases are reclaimed.
3. **Pipeline-Stage Handoff (`board` to `board`):**
   - **1:1 handoff:** Moves the same ticket to the next team board, preserving one history and `TicketComment` thread.
   - **Split / parallel review:** Creates child tickets via `parent_ticket_id`; the parent remains open until all children finish.
   - **Backward flow:** If the receiving board's `tm` rejects acceptance for lack of executability, the ticket returns to the previous board with a reason.
   - **Acceptor ≠ Merger & Sensitive-area ownership:** Ownership rules in org config require the owning board's `tm` to accept when work touches an owned area.
4. **Precedents & Consolidation:**
   - Gatekeepers query relevant precedents before issuing a verdict.
   - Refuted deliverables and human disagreements become precedents; stale precedents can be refuted or superseded.
   - Precedents are consolidated into versioned org policy **weekly**.

---

## 5. Invariants & Decided Policies

### Important-Matter Criteria (Project Decision Board)
Any of the following creates a card on the **project decision board** immediately:
1. Scope or goal change
2. Release or external exposure
3. Security policy or permission change
4. Policy-doc or loop-skill change
5. **Appetite exhausted (first `E1`)** — ticket is blocked until human `B3` release (**urgent**)
6. **Appetite exhausted again after a human (`B3`) release** of a prior `E1` (**urgent**)
7. Same cause returning more than 3 times
8. Irreversible action (deletion, spending, or external sending)

Items 1–4, 7, and 8 are **not urgent by default** (they do not execute before approval). Items 5 and 6 **are urgent** because waiting would leave the ticket blocked.

### Appetite Default
- **3 iterations** (`max_attempts = 3`), **no time cap**, and **tokens recorded but uncapped**.
- Optional time or token caps may be configured in org config as **overrides**, never as defaults. When a time cap is set, its clock starts at **first claim**.
- Cut-scope is a `tm` revision, not an automatic path on exhaustion.

### Urgency, Card Creation, and Notification Cadence
- **Card creation:** Decision cards are **always created immediately** (with options, recommendation, rationale, and optional `decision_deadline`).
- **Notification batching:** Only **human notification** is batched weekly — default **Monday 09:00 in the workspace timezone** (configurable).
- **Urgency criterion:** A card is **urgent** when waiting until the next notification batch would **increase harm** or **make the decision moot**. Specifically:
  - **(a) Halt:** Any agent sets workspace halt; the server creates **one decision-board card per halt episode** (deduplicated).
  - **(b) Ongoing harm:** The creating principal sets an explicit `ongoing_harm` flag (human or `pm` may reclassify; the server does not infer domain harm on its own except for halt and `E1`).
  - **(c) Deadline:** `decision_deadline` is earlier than the next notification batch.
- Urgent cards notify the human immediately; non-urgent cards wait for the weekly batch.
- `tm` interprets decided cards onto team tickets (using `attention` where needed) and records the applied result in `TicketComment`.

---

## 6. Mapping Tables (Verification)

### Software Example (Design in #194)

| Concept | Software Mapping |
| --- | --- |
| **Cards (ticket & deliverable)** | `Ticket` (spec, acceptance criteria) + `Deliverable` (summary, commit/PR artifacts, self-check, `refutation.method`) |
| **Claim** | Implementation/fix satisfies acceptance criteria and passes targeted tests |
| **Refuter** | `gatekeeper` (PR/code review), next-board `tm` (QA/security executability), `audit` (spot check) |
| **Pipeline-stage handoff** | `triage`/`pm` → `dev` board → `qa` / `security` (parallel children via `parent_ticket_id`) → `release` board (`done`) |
| **Precedent example** | "Missing migration rollback caused prod failure" → consolidated into weekly review checklist |
| **Human decision point** | `human_review` approval, `B3` unblock after `E1`, or project decision card (e.g., release, schema/auth policy) |
| **Appetite unit** | 3 iterations (`max_attempts = 3`); optional token/time override |

### Compatibility Set (Enterprise Work)

#### Book Writing
| Concept | Mapping |
| --- | --- |
| **Cards** | Ticket = chapter/section brief; Deliverable = manuscript draft + `refutation.method` (fact/continuity/style check) |
| **Claim** | Draft fulfills chapter outline, voice, and factual accuracy criteria |
| **Refuter** | Continuity/fact `gatekeeper`, copy-edit board `tm`, external `audit` |
| **Pipeline-stage handoff** | Outline (`pm`) → Drafting board → Fact-check & Copy-edit boards → Final proof (`done`) |
| **Precedent example** | "Unverified historical date in Chapter 3" → citation-check precedent |
| **Human decision point** | Author/editor approves final manuscript or decides scope/tone change |
| **Appetite unit** | 3 revision iterations per chapter |

#### Research and Paper Writing
| Concept | Mapping |
| --- | --- |
| **Cards** | Ticket = experiment or section task; Deliverable = results/manuscript + `refutation.method` (replication/statistical check) |
| **Claim** | Methodology, citations, and statistical claims hold under replication |
| **Refuter** | Methodology `gatekeeper`, peer-review simulation board `tm`, `audit` |
| **Pipeline-stage handoff** | Literature/Design → Analysis board → Writing board → Submission-readiness (`done`) |
| **Precedent example** | "Small-sample p-hacking flagged" → pre-registration check precedent |
| **Human decision point** | Principal investigator approves submission or releases `E1` blocked experiment |
| **Appetite unit** | 3 iterations per section/experiment |

#### Report Writing
| Concept | Mapping |
| --- | --- |
| **Cards** | Ticket = report section/analysis; Deliverable = formatted brief + `refutation.method` (source-data reconciliation) |
| **Claim** | Figures and conclusions match underlying data and executive brief |
| **Refuter** | Data-verification `gatekeeper`, editorial board `tm` |
| **Pipeline-stage handoff** | Data pull → Synthesis board → Executive review board (`done`) |
| **Precedent example** | "Mismatch between chart and appendix table" → reconciliation precedent |
| **Human decision point** | Executive sponsor approves external publication (important-matter card) |
| **Appetite unit** | 3 draft-review iterations |

#### Legal Review
| Concept | Mapping |
| --- | --- |
| **Cards** | Ticket = contract/clause review; Deliverable = redline + risk memo + `refutation.method` (counter-clause/statute check) |
| **Claim** | Clauses comply with policy and mitigate identified liability |
| **Refuter** | Compliance `gatekeeper`, domain-counsel board `tm`, external `audit` |
| **Pipeline-stage handoff** | Intake → Commercial review board → Privacy/IP board (parallel children) → Sign-off (`done`) |
| **Precedent example** | "Uncapped indemnity accepted in error" → indemnity-cap precedent |
| **Human decision point** | General Counsel approves non-standard liability or irreversible external signing |
| **Appetite unit** | 3 negotiation/redline iterations |

#### Tax Review
| Concept | Mapping |
| --- | --- |
| **Cards** | Ticket = jurisdiction/filing item; Deliverable = tax position memo + workpapers + `refutation.method` (statutory/audit test) |
| **Claim** | Calculation and nexus position are defensible under current tax code |
| **Refuter** | Senior-tax `gatekeeper`, partner-review board `tm`, `audit` |
| **Pipeline-stage handoff** | Data gathering → Calculation board → Technical review board → Filing (`done`) |
| **Precedent example** | "Missing nexus apportionment schedule" → multi-state checklist precedent |
| **Human decision point** | Tax partner approves filing or urgent card before statutory `decision_deadline` |
| **Appetite unit** | 3 workpaper-review iterations |

#### HR (Hiring or Policy Change)
| Concept | Mapping |
| --- | --- |
| **Cards** | Ticket = candidate evaluation or policy draft; Deliverable = scorecard/policy + `refutation.method` (rubric/compliance check) |
| **Claim** | Candidate meets role rubric or policy complies with labor law |
| **Refuter** | Calibration `gatekeeper`, hiring-committee / legal board `tm`, `audit` |
| **Pipeline-stage handoff** | Intake → Screening/Drafting board → Panel/Legal board → Offer/Publish (`done`) |
| **Precedent example** | "Inconsistent interview rubric weighting" → calibration precedent |
| **Human decision point** | Hiring manager / HR lead approves offer or policy-doc change card |
| **Appetite unit** | 3 calibration/revision iterations |
