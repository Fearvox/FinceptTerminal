# Raven Mission Capsule Control Room — Design Spec

**Created**: 2026-05-15  
**Author**: Codex + 0xVox  
**Status**: Approved direction, pending implementation plan  
**Primary host**: Raven / EverOS  
**Anchor mission**: `fincept.autoexec-control-room`  
**Reference style**: Anthropic, *The Founder's Playbook: Building an AI-Native Startup* PDF, 2026-05-06

---

## 1. Product Thesis

Raven should become the productized Mission Capsule OS for AI-native operator
work.

The first capsule is **Fincept Auto-Exec Control Room**: a control surface that
observes live Fincept execution infrastructure, preserves Windburn-style gates,
routes bounded work across Claude/Codex/Hermes/Superconductor, and emits receipts
for every action that could affect execution confidence.

This is not a normal dashboard. It is an **editorial command product** for
mission capsules: chapter scale when scanning, dense proof when deciding, matrix
form when selecting a tool, lane, or autonomy mode.

Flight recorder and gate console are core product modules, not the whole
product. Raven still needs its own product surface: capsule home, capsule
install/selection, gates, action drafts, receipts, and operator closeout.

The ambition is high: Raven should feel like the operating manual for a
one-person AI-native company. The posture can borrow the intensity of a
high-conviction closer, but the controls must stay audit-grade. No hype can
launder a red gate into green.

## 2. Source Observations

### 2.1 Raven / EverOS Current Shape

Live EverOS/Raven already has the right substrate:

- `RavenSnapshot` collects packet, watch issues, local gates, remote gates,
  agents, memory health, runs, Superconductor state, risks, and next actions.
- `RavenReceipt` records command, exit code, duration, verdict, evidence excerpt,
  gate effects, and public-safety result.
- `raven status`, `raven gates`, `raven agents list`, `raven runs list`, and
  `raven sc ...` already express a command contract for operator truth.
- Existing gate semantics are conservative: local PASS plus remote BLOCK renders
  overall FLAG; nearby tests must not upgrade a hard gate.
- SkillHub already has `install_targets` that include `raven`, which is the
  right insertion point for capsule packaging.

### 2.2 Fincept Current Shape

Fincept already exposes mission-grade signals:

- TradingView webhook and daemon logs.
- Dispatcher dry-run/live state.
- SQLite trade journal.
- Source allowlist and position-lock concerns.
- Claude main lane and Codex playground patches.
- Monitor and scheduled fallback loops.

The cockpit should make these signals legible without turning them into
pseudo-certainty.

### 2.3 Context-Continuity Cockpit Observation

VS Code has quietly become the practical multi-agent cockpit:

- left side: worktrees, sessions, lanes, gates;
- center: active artifact, diff, packet, source file;
- right side: agent loop, chat, review, evidence;
- bottom: terminal and live runtime truth.

Kilo Code is strong because it keeps the implementation loop glued to the
editor/worktree/terminal context. It does not ask the operator to re-explain the
repo inside a separate orchestration product.

Raven should learn from this without copying or shrinking itself to a passive
overlay:

- **VS Code/Kilo/Codex/Claude** own inline implementation loops.
- **Superconductor** owns session and worktree conduction.
- **MUW/Linear/GitHub** own external truth and issue state.
- **Raven** owns the productized mission layer: capsule selection, trust state,
  gate policy, action drafts, receipts, and closeout.

The design lesson is context continuity, not product retreat. Raven should not
clone VS Code, but it should feel like a real product mode sitting above the
active cockpit: a capsule OS that knows what the operator is trying to ship and
why the current loop is or is not trustworthy.

### 2.4 Founder's Playbook Visual Translation

The reference playbook uses:

- horizontal spreads;
- large serif chapter titles;
- strong color fields;
- hand-drawn operator glyphs;
- two-column evidence pages;
- lightweight matrices for choosing tools;
- explicit stage framing: Idea, MVP, Launch, Scale.

Raven should translate this into:

- mission chapter covers;
- evidence spreads;
- gate/action matrices;
- operator lifecycle framing;
- calm editorial density instead of widget noise.

## 3. Goals

- Ship a Raven capsule direction that can host Fincept control-room work first.
- Make the first screen a high-confidence mission state, not a crowded widget
  wall.
- Preserve `PASS / FLAG / BLOCK` truth and expose the exact reason for each
  verdict.
- Give the operator approved-action drafts without executing risky actions by
  default.
- Establish a future-compatible mode ladder for `--auto`, `--sandbox-auto`,
  `--sandbox-yolo`, and `--yolo`.
- Keep public surfaces sanitized: no raw local paths, secrets, host topology, or
  operator-only commands.

## 4. Non-Goals

- No v1 live trading execution.
- No v1 automatic cherry-pick, live-arm, or branch mutation.
- No hidden escalation from read-only mode into mutation.
- No unbounded Raven-as-generic-chat-app or Raven-as-editor clone. Productized
  capsule chat/action surfaces are allowed when tied to gates and receipts.
- No generic SaaS dashboard aesthetic.
- No claim that local smoke tests can resolve remote gates.
- No product UI that exposes local filesystem details in public/demo contexts.

## 5. Core Product Model

### 5.1 Mission Capsule

```ts
type MissionCapsule = {
  id: string
  title: string
  sources: SourceAdapter[]
  gates: GatePolicy[]
  panels: RavenPanel[]
  actions: ApprovedActionDraft[]
  modes: CapsuleMode[]
  receipts: ReceiptPolicy[]
}
```

For v1:

```text
id: fincept.autoexec-control-room
title: Fincept Auto-Exec Control Room
default mode: read-only
execution policy: draft only unless operator approves outside the capsule
```

### 5.2 Capsule Lifecycle

Raven should frame each capsule through five stages:

| Stage | Purpose | Output |
| --- | --- | --- |
| Observe | Refresh live agents, runtimes, issues, journal, and logs. | Source truth snapshot |
| Validate | Apply mission gates and pressure-test stale or missing evidence. | PASS/FLAG/BLOCK table |
| Route | Select the right lane: Claude, Codex, Hermes, Superconductor, or human. | Bounded assignment draft |
| Execute | Run only actions allowed by the selected mode. | Receipt or blocked reason |
| Close | Attach proof, residual risk, and next action. | Operator-readable closeout |

### 5.3 Mode Ladder

| Mode | Raven may | Blocks |
| --- | --- | --- |
| `default` | Read, refresh, compare, draft actions. | Any mutation or live execution. |
| `--auto` | Run approved low-risk refreshes and write receipts. | Branch mutation, live trading, secret changes. |
| `--sandbox-auto` | Execute dry-run or paper-only commands in isolated space. | Main lane mutation, live surface changes. |
| `--sandbox-yolo` | Mutate disposable worktrees with visible danger state. | Main lane, live trading, remote deploy gates. |
| `--yolo` | Future explicit operator unlock only. | Never default; requires red UI and post-run audit. |

V1 implements only `default` semantics in the product. The other modes are
documented now so the UI and data model do not paint us into a corner.

## 6. UI Design

### 6.1 Product Feel

Raven should feel like a productized field manual for commanding AI systems:

- editorial, not decorative;
- high-contrast and calm;
- visibly human-directed and execution-tool-aware;
- cohesive enough to be a standalone product mode;
- fast to scan under stress;
- precise enough to defend a decision later.

The first screen is a mission chapter and trust verdict:

```text
Fincept Auto-Exec Control Room
VERDICT: FLAG
Reason: dispatcher dry-run is healthy, but SMC live source remains blocked.
Next: review approved-action drafts before changing mode.
```

### 6.2 Layout

Primary layout, inspired by VS Code's real cockpit shape but productized for
mission capsules:

```text
┌─────────────────────────────────────────────┐
│ Mission Chapter Cover                        │
│ huge title / verdict / one-line reason       │
├───────────────┬───────────────┬─────────────┤
│ Live Truth    │ Gates         │ Actions     │
│ sources       │ PASS/FLAG/... │ drafts      │
│ evidence      │ blockers      │ receipts    │
└───────────────┴───────────────┴─────────────┘
```

Dense mode uses a two-column spread:

- left: source truth, timestamps, commands, excerpts;
- right: gate effects, action drafts, residual risk.

Decision mode uses matrices:

- which agent/lane should handle this;
- which autonomy mode is allowed;
- which gates block completion;
- which receipt is required.

Raven may expose agent/chat/action surfaces, but they must be capsule-scoped and
receipt-backed. It should not become a generic chat box detached from mission
truth.

### 6.3 Visual Language

- Serif display titles for mission chapter covers.
- Sans-serif tables and controls for operational details.
- Muted chapter colors, not neon dashboards.
- Hand-drawn glyphs for mission identity, but no decorative clutter.
- Cards only for repeated items or focused panels.
- No nested cards.
- No public UI that leaks absolute paths, hostnames, token paths, or raw SSH/tmux
  targets.
- No attempt to replace VS Code, Kilo, Claude, Codex, or Superconductor.

## 7. Data Flow

```text
Fincept logs/journal/webhook ─┐
Claude/Codex/Hermes lanes ────┼─> Source adapters ─┐
Superconductor sessions ──────┘                    │
                                                    v
                                             Capsule snapshot
                                                    │
                                                    v
                                              Gate policies
                                                    │
                         ┌──────────────────────────┴──────────────────────────┐
                         v                                                     v
                 Raven UI panels                                      Action drafts
                         │                                                     │
                         v                                                     v
                  Operator decision                                  Receipt policy
```

V1 source adapters are read-only.

## 8. Gate Policy

The capsule must preserve conservative Raven semantics:

- PASS means the exact requirement was tested at the claimed scope.
- FLAG means usable but stale, missing external evidence, or not fully proven.
- BLOCK means a required gate failed or needs human approval.
- Local PASS plus hard remote BLOCK renders overall FLAG.
- Approved-action drafts never change verdict until the action is performed and
  verified.
- A receipt is not proof unless it includes source truth, command/result, gate
  effect, and residual risk.

Initial Fincept gates:

| Gate | PASS | FLAG | BLOCK |
| --- | --- | --- | --- |
| Webhook health | Process alive and recent heartbeat. | Alive but stale heartbeat. | Process missing. |
| Dispatcher mode | Dry-run confirmed or live gate intentionally armed. | Unknown mode. | Live enabled without approval. |
| Source allowlist | Signal source is explicitly allowed. | Source is research-only. | Source attempts execution while blocked. |
| Position lock | No same-source open opposite conflict. | Existing exposure needs review. | Same-source lock violation. |
| Claude/Codex lane split | Main lane and playground boundaries clear. | Diff pending review. | Unapproved patch mutates main lane. |

## 9. Action Drafts

V1 actions are drafts only:

- ask Claude to review a packet;
- ask Codex to draft a patch in playground;
- produce cherry-pick candidate summary;
- produce dry-run command proposal;
- produce live-arm checklist;
- produce post-incident closeout.

Each draft includes:

- exact command or payload;
- required mode;
- required gates;
- expected receipt;
- abort conditions;
- public-safety note.

## 10. Error Handling

- Missing source: keep UI alive, mark source FLAG.
- Missing hard dependency: mark relevant gate BLOCK.
- Stale timestamp: mark gate FLAG with age.
- Contradictory evidence: preserve both facts and mark overall FLAG.
- Sanitizer hit: redact public field and record public-safety FLAG.
- Adapter failure: fail closed; never infer PASS.

## 11. Testing Strategy

- Snapshot unit tests for source adapters.
- Gate policy tests for PASS/FLAG/BLOCK combinations.
- Public-safety sanitizer tests for paths, hostnames, token paths, and command
  payloads.
- Deterministic TUI smoke using the existing `RAVEN_TUI_ONCE=1` pattern.
- Fixture-based capsule rendering for `fincept.autoexec-control-room`.
- Regression test: local PASS plus hard remote BLOCK must render overall FLAG.

## 12. Implementation Slices

This spec is intentionally product-direction first. A later implementation plan
should split it into these slices:

1. Capsule schema and fixture.
2. Fincept read-only source adapter.
3. Gate policy evaluator.
4. Raven TUI panel for mission capsule.
5. Action draft and receipt shape.
6. Visual polish pass inspired by the playbook style.
7. Public-safety and deterministic smoke tests.

## 13. Risks

- **Over-scoping**: trying to build every mode now would delay v1. Mitigation:
  implement only read-only default first.
- **Dashboard drift**: adding too many panels can erase the editorial command
  feel. Mitigation: mission chapter first, dense proof second.
- **False autonomy**: future flags could imply permission that gates do not
  allow. Mitigation: mode ladder is explicit and hard-blocked.
- **Public leakage**: local paths and operational topology can appear in demo UI.
  Mitigation: sanitizer is a first-class gate.
- **Cross-repo confusion**: Fincept and EverOS have separate worktrees and owners.
  Mitigation: capsule reads Fincept; Raven owns product UI; mutation remains
  operator-approved.

## 14. Decisions Locked

- Top-level abstraction is **Mission Capsule**.
- First mission is **Fincept Auto-Exec Control Room**.
- V1 default mode is read-only plus approved-action drafts.
- Future autonomy flags are designed but not implemented in v1.
- Product direction is editorial command surface, not widget dashboard.
- Raven is the host product; Fincept is the first capsule source.

## 15. Open Implementation Questions

These are for the implementation-plan phase, not blockers for the product
direction:

- Should the capsule schema live in Raven core or SkillHub first?
- Should Fincept source reads happen through file adapters or a small local
  capsule bridge command?
- Should the first UI land in Raven TUI only, or also export an HTML owner
  packet?
- Which exact fields should become stable JSON for external capsules?
