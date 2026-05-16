# Raven Mission Capsule Control Room Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Raven mission capsule for `fincept.autoexec-control-room` that exposes Fincept auto-exec truth, gates, action drafts, and mode ladder without mutating Fincept or EverOS state.

**Architecture:** Implement capsule support inside the existing Raven Rust console as typed snapshot data plus a read-only Fincept adapter, CLI output, and a TUI panel. Keep Fincept-specific WebUI as a separate downstream surface: it may later become a capsule source, but it does not define the Raven host product.

**Tech Stack:** Rust 2021, `serde`, `serde_json`, `clap`, `ratatui`, existing `bin/raven` wrapper, existing `just raven-console-check` target.

---

## Scope Boundary

Implementation happens in the EverOS repo under `use-cases/hermes-everos-memory/`.
This plan file lives in FinceptTerminal only as the product/design anchor.

V1 is read-only:

- No live trading.
- No branch mutation.
- No automatic cherry-pick.
- No `--auto`, `--sandbox-auto`, `--sandbox-yolo`, or `--yolo` execution.
- Future modes appear as data and UI labels only.

The Fincept-specialized WebUI can continue in parallel. Raven treats it as a
future source adapter, not as the product direction.

## File Map

Create in EverOS:

- `use-cases/hermes-everos-memory/raven-console/src/adapters/capsules.rs`
  - Builds built-in mission capsule views.
  - Reads optional `RAVEN_FINCEPT_ROOT`.
  - Never writes to Fincept.
- `use-cases/hermes-everos-memory/skillhub/fixtures/fincept-autoexec-control-room-capsule.json`
  - SkillHub packet declaring the capsule install target.

Modify in EverOS:

- `use-cases/hermes-everos-memory/raven-console/src/model.rs`
  - Adds capsule data types and `RavenSnapshot.mission_capsules`.
- `use-cases/hermes-everos-memory/raven-console/src/adapters/mod.rs`
  - Exports the new `capsules` adapter.
- `use-cases/hermes-everos-memory/raven-console/src/snapshot.rs`
  - Adds capsule views to the snapshot without changing existing hard-gate verdict semantics.
- `use-cases/hermes-everos-memory/raven-console/src/commands.rs`
  - Adds `raven capsules list` and `raven capsules show <id>`.
- `use-cases/hermes-everos-memory/raven-console/src/output.rs`
  - Adds human and JSON-friendly capsule output.
- `use-cases/hermes-everos-memory/raven-console/src/tui.rs`
  - Adds a Capsules panel and editorial mission capsule view.
- `use-cases/hermes-everos-memory/raven/COMMAND_CONTRACT.md`
  - Documents the new command contract.
- `use-cases/hermes-everos-memory/raven/README.md`
  - Adds operator commands and just target notes.
- `use-cases/hermes-everos-memory/justfile`
  - Adds capsule smoke targets.

## Task 1: Add Mission Capsule Model Types

**Files:**
- Modify: `use-cases/hermes-everos-memory/raven-console/src/model.rs`

- [ ] **Step 1: Write the failing model serialization test**

Add this test inside the existing `#[cfg(test)]` module at the bottom of
`raven-console/src/model.rs`. If there is no module yet, create one.

```rust
#[cfg(test)]
mod tests {
    use super::{
        CapsuleActionDraftView, CapsuleGateView, CapsuleModeView, CapsuleSourceView,
        MissionCapsuleView, Verdict,
    };

    #[test]
    fn mission_capsule_serializes_default_read_only_mode() {
        let capsule = MissionCapsuleView {
            id: "fincept.autoexec-control-room".to_string(),
            title: "Fincept Auto-Exec Control Room".to_string(),
            verdict: Verdict::Flag,
            mode: "default".to_string(),
            summary: "dispatcher dry-run is healthy, but live gate remains locked".to_string(),
            sources: vec![CapsuleSourceView {
                id: "fincept.dispatch_log".to_string(),
                label: "dispatch log".to_string(),
                verdict: Verdict::Pass,
                evidence: "latest dispatch was dry-run".to_string(),
            }],
            gates: vec![CapsuleGateView {
                id: "fincept.dispatcher_mode".to_string(),
                name: "Dispatcher mode".to_string(),
                verdict: Verdict::Pass,
                blocks_completion: true,
                evidence: "dry_run=true".to_string(),
            }],
            actions: vec![CapsuleActionDraftView {
                id: "fincept.review_dry_run".to_string(),
                label: "Review dry-run state".to_string(),
                required_mode: "default".to_string(),
                verdict: Verdict::Pass,
                command: "raven capsules show fincept.autoexec-control-room".to_string(),
                receipt: "capsule read-only receipt".to_string(),
                abort_conditions: vec!["live mode requested without operator approval".to_string()],
            }],
            modes: vec![CapsuleModeView {
                name: "default".to_string(),
                may: "Read, refresh, compare, and draft actions.".to_string(),
                blocks: "Any mutation or live execution.".to_string(),
                implemented: true,
            }],
        };

        let value = serde_json::to_value(&capsule).expect("capsule serializes");

        assert_eq!(value["id"], "fincept.autoexec-control-room");
        assert_eq!(value["verdict"], "FLAG");
        assert_eq!(value["modes"][0]["name"], "default");
        assert_eq!(value["modes"][0]["implemented"], true);
        assert_eq!(value["actions"][0]["required_mode"], "default");
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `use-cases/hermes-everos-memory`:

```bash
cargo test --manifest-path raven-console/Cargo.toml mission_capsule_serializes_default_read_only_mode
```

Expected: FAIL with unresolved imports such as `MissionCapsuleView` not found.

- [ ] **Step 3: Add the model types**

In `raven-console/src/model.rs`, after `RunView`, add:

```rust
#[derive(Clone, Debug, Serialize)]
pub struct CapsuleSourceView {
    pub id: String,
    pub label: String,
    pub verdict: Verdict,
    pub evidence: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapsuleGateView {
    pub id: String,
    pub name: String,
    pub verdict: Verdict,
    pub blocks_completion: bool,
    pub evidence: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapsuleActionDraftView {
    pub id: String,
    pub label: String,
    pub required_mode: String,
    pub verdict: Verdict,
    pub command: String,
    pub receipt: String,
    pub abort_conditions: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CapsuleModeView {
    pub name: String,
    pub may: String,
    pub blocks: String,
    pub implemented: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct MissionCapsuleView {
    pub id: String,
    pub title: String,
    pub verdict: Verdict,
    pub mode: String,
    pub summary: String,
    pub sources: Vec<CapsuleSourceView>,
    pub gates: Vec<CapsuleGateView>,
    pub actions: Vec<CapsuleActionDraftView>,
    pub modes: Vec<CapsuleModeView>,
}
```

Then update `RavenSnapshot` by adding this field after `sc: ScReport`:

```rust
pub mission_capsules: Vec<MissionCapsuleView>,
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cargo test --manifest-path raven-console/Cargo.toml mission_capsule_serializes_default_read_only_mode
```

Expected: PASS.

- [ ] **Step 5: Commit the model change**

```bash
git add use-cases/hermes-everos-memory/raven-console/src/model.rs
git commit -m "feat(raven): add mission capsule model types" -m "Add read-only mission capsule data structures for sources, gates, action drafts, modes, and snapshot embedding." -m "Co-authored-by: Codex <noreply@openai.com>"
```

## Task 2: Add Read-Only Fincept Capsule Adapter

**Files:**
- Create: `use-cases/hermes-everos-memory/raven-console/src/adapters/capsules.rs`
- Modify: `use-cases/hermes-everos-memory/raven-console/src/adapters/mod.rs`

- [ ] **Step 1: Write failing adapter tests**

Create `raven-console/src/adapters/capsules.rs` with tests first:

```rust
use crate::model::{
    CapsuleActionDraftView, CapsuleGateView, CapsuleModeView, CapsuleSourceView,
    MissionCapsuleView, Verdict,
};

#[derive(Clone, Debug, Default)]
pub struct FinceptFacts {
    pub root_configured: bool,
    pub webhook_file_present: bool,
    pub dispatcher_file_present: bool,
    pub dispatch_log_present: bool,
    pub latest_source: Option<String>,
    pub latest_reason: Option<String>,
    pub latest_dry_run: Option<bool>,
    pub live_enabled: Option<bool>,
}

#[cfg(test)]
mod tests {
    use super::{fincept_autoexec_capsule, FinceptFacts};
    use crate::model::Verdict;

    #[test]
    fn fincept_capsule_flags_when_root_is_missing() {
        let capsule = fincept_autoexec_capsule(FinceptFacts::default());

        assert_eq!(capsule.id, "fincept.autoexec-control-room");
        assert_eq!(capsule.verdict, Verdict::Flag);
        assert!(capsule
            .gates
            .iter()
            .any(|gate| gate.id == "fincept.source_truth" && gate.verdict == Verdict::Flag));
    }

    #[test]
    fn fincept_capsule_blocks_live_enabled_without_operator_approval() {
        let capsule = fincept_autoexec_capsule(FinceptFacts {
            root_configured: true,
            webhook_file_present: true,
            dispatcher_file_present: true,
            dispatch_log_present: true,
            latest_source: Some("fincept".to_string()),
            latest_reason: Some("dry_run".to_string()),
            latest_dry_run: Some(false),
            live_enabled: Some(true),
        });

        assert_eq!(capsule.verdict, Verdict::Block);
        assert!(capsule.summary.contains("live mode"));
    }

    #[test]
    fn fincept_capsule_passes_dry_run_and_allowlist_evidence() {
        let capsule = fincept_autoexec_capsule(FinceptFacts {
            root_configured: true,
            webhook_file_present: true,
            dispatcher_file_present: true,
            dispatch_log_present: true,
            latest_source: Some("smc".to_string()),
            latest_reason: Some("source_not_allowed:smc".to_string()),
            latest_dry_run: Some(true),
            live_enabled: Some(false),
        });

        assert_eq!(capsule.verdict, Verdict::Pass);
        assert!(capsule
            .gates
            .iter()
            .any(|gate| gate.id == "fincept.source_allowlist" && gate.verdict == Verdict::Pass));
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cargo test --manifest-path raven-console/Cargo.toml fincept_capsule_
```

Expected: FAIL because `fincept_autoexec_capsule` is not implemented.

- [ ] **Step 3: Implement the adapter**

Replace the initial test-only contents of `raven-console/src/adapters/capsules.rs`
with this implementation while keeping the tests:

```rust
use crate::model::{
    CapsuleActionDraftView, CapsuleGateView, CapsuleModeView, CapsuleSourceView,
    MissionCapsuleView, Verdict,
};
use serde_json::Value;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Default)]
pub struct FinceptFacts {
    pub root_configured: bool,
    pub webhook_file_present: bool,
    pub dispatcher_file_present: bool,
    pub dispatch_log_present: bool,
    pub latest_source: Option<String>,
    pub latest_reason: Option<String>,
    pub latest_dry_run: Option<bool>,
    pub live_enabled: Option<bool>,
}

pub fn mission_capsules() -> Vec<MissionCapsuleView> {
    vec![fincept_autoexec_capsule(load_fincept_facts())]
}

pub fn find_capsule(id: &str) -> Option<MissionCapsuleView> {
    mission_capsules()
        .into_iter()
        .find(|capsule| capsule.id == id)
}

pub fn fincept_autoexec_capsule(facts: FinceptFacts) -> MissionCapsuleView {
    let source_truth = source_truth_gate(&facts);
    let dispatcher_mode = dispatcher_mode_gate(&facts);
    let source_allowlist = source_allowlist_gate(&facts);
    let position_lock = position_lock_gate(&facts);
    let live_gate = live_gate(&facts);
    let gates = vec![
        source_truth,
        dispatcher_mode,
        source_allowlist,
        position_lock,
        live_gate,
    ];
    let verdict = summarize_verdict(&gates);
    let summary = match verdict {
        Verdict::Pass => "Fincept capsule is read-only healthy; dry-run and allowlist evidence are present.",
        Verdict::Flag => "Fincept capsule is usable but missing fresh source truth or complete gate evidence.",
        Verdict::Block => "Fincept capsule detected live mode or a hard gate requiring operator approval.",
    }
    .to_string();

    MissionCapsuleView {
        id: "fincept.autoexec-control-room".to_string(),
        title: "Fincept Auto-Exec Control Room".to_string(),
        verdict,
        mode: "default".to_string(),
        summary,
        sources: source_views(&facts),
        gates,
        actions: action_drafts(),
        modes: mode_ladder(),
    }
}

fn load_fincept_facts() -> FinceptFacts {
    let Some(root) = env::var_os("RAVEN_FINCEPT_ROOT").map(PathBuf::from) else {
        return FinceptFacts::default();
    };
    let base = root.join("fincept-qt/scripts/data_algo/propfirm_engine");
    let dispatch_log = base.join("auto_exec/.logs/dispatches.jsonl");
    let latest = latest_dispatch(&dispatch_log);

    FinceptFacts {
        root_configured: true,
        webhook_file_present: base.join("tv_webhook.py").exists(),
        dispatcher_file_present: base.join("auto_exec/dispatcher.py").exists(),
        dispatch_log_present: dispatch_log.exists(),
        latest_source: latest
            .as_ref()
            .and_then(|value| value.get("payload"))
            .and_then(|payload| payload.get("source"))
            .and_then(Value::as_str)
            .map(str::to_string),
        latest_reason: latest
            .as_ref()
            .and_then(|value| value.get("decision"))
            .and_then(|decision| decision.get("reason"))
            .and_then(Value::as_str)
            .map(str::to_string),
        latest_dry_run: latest
            .as_ref()
            .and_then(|value| value.get("decision"))
            .and_then(|decision| decision.get("dry_run"))
            .and_then(Value::as_bool),
        live_enabled: env::var("P5B_AUTOEXEC_ENABLED")
            .ok()
            .map(|value| value == "1" || value.eq_ignore_ascii_case("true")),
    }
}

fn latest_dispatch(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    text.lines()
        .rev()
        .find_map(|line| serde_json::from_str::<Value>(line).ok())
}

fn source_views(facts: &FinceptFacts) -> Vec<CapsuleSourceView> {
    vec![
        CapsuleSourceView {
            id: "fincept.webhook".to_string(),
            label: "TradingView webhook".to_string(),
            verdict: present_verdict(facts.root_configured && facts.webhook_file_present),
            evidence: if facts.webhook_file_present {
                "tv_webhook.py present".to_string()
            } else {
                "RAVEN_FINCEPT_ROOT missing or tv_webhook.py not found".to_string()
            },
        },
        CapsuleSourceView {
            id: "fincept.dispatcher".to_string(),
            label: "Auto-exec dispatcher".to_string(),
            verdict: present_verdict(facts.root_configured && facts.dispatcher_file_present),
            evidence: if facts.dispatcher_file_present {
                "auto_exec/dispatcher.py present".to_string()
            } else {
                "dispatcher source not found".to_string()
            },
        },
        CapsuleSourceView {
            id: "fincept.dispatch_log".to_string(),
            label: "Dispatch log".to_string(),
            verdict: present_verdict(facts.root_configured && facts.dispatch_log_present),
            evidence: facts
                .latest_reason
                .clone()
                .unwrap_or_else(|| "no dispatch reason available".to_string()),
        },
    ]
}

fn source_truth_gate(facts: &FinceptFacts) -> CapsuleGateView {
    let ok = facts.root_configured && facts.webhook_file_present && facts.dispatcher_file_present;
    CapsuleGateView {
        id: "fincept.source_truth".to_string(),
        name: "Source truth".to_string(),
        verdict: present_verdict(ok),
        blocks_completion: false,
        evidence: if ok {
            "Fincept root, webhook, and dispatcher are visible read-only.".to_string()
        } else {
            "Set RAVEN_FINCEPT_ROOT to enable Fincept source truth reads.".to_string()
        },
    }
}

fn dispatcher_mode_gate(facts: &FinceptFacts) -> CapsuleGateView {
    let verdict = match facts.latest_dry_run {
        Some(true) => Verdict::Pass,
        Some(false) => Verdict::Block,
        None => Verdict::Flag,
    };
    CapsuleGateView {
        id: "fincept.dispatcher_mode".to_string(),
        name: "Dispatcher mode".to_string(),
        verdict,
        blocks_completion: true,
        evidence: match facts.latest_dry_run {
            Some(true) => "latest dispatch record has dry_run=true".to_string(),
            Some(false) => "latest dispatch record has dry_run=false".to_string(),
            None => "no dispatch dry_run evidence found".to_string(),
        },
    }
}

fn source_allowlist_gate(facts: &FinceptFacts) -> CapsuleGateView {
    let reason = facts.latest_reason.clone().unwrap_or_default();
    let source = facts.latest_source.clone().unwrap_or_default();
    let verdict = if reason.starts_with("source_not_allowed:") || source == "fincept" {
        Verdict::Pass
    } else if facts.dispatch_log_present {
        Verdict::Flag
    } else {
        Verdict::Flag
    };
    CapsuleGateView {
        id: "fincept.source_allowlist".to_string(),
        name: "Source allowlist".to_string(),
        verdict,
        blocks_completion: true,
        evidence: if reason.starts_with("source_not_allowed:") {
            format!("blocked non-allowlisted source via {reason}")
        } else if source == "fincept" {
            "latest dispatch source is fincept".to_string()
        } else {
            "no recent allowlist evidence found".to_string()
        },
    }
}

fn position_lock_gate(facts: &FinceptFacts) -> CapsuleGateView {
    let reason = facts.latest_reason.clone().unwrap_or_default();
    let blocked = reason.contains("position_lock");
    CapsuleGateView {
        id: "fincept.position_lock".to_string(),
        name: "Position lock".to_string(),
        verdict: if blocked { Verdict::Block } else { Verdict::Flag },
        blocks_completion: true,
        evidence: if blocked {
            reason
        } else {
            "no latest position-lock violation; needs journal adapter for PASS".to_string()
        },
    }
}

fn live_gate(facts: &FinceptFacts) -> CapsuleGateView {
    let live = facts.live_enabled.unwrap_or(false);
    CapsuleGateView {
        id: "fincept.live_gate".to_string(),
        name: "Live execution gate".to_string(),
        verdict: if live { Verdict::Block } else { Verdict::Pass },
        blocks_completion: true,
        evidence: if live {
            "P5B_AUTOEXEC_ENABLED indicates live mode in Raven environment".to_string()
        } else {
            "Raven default mode is read-only; live execution is not armed here".to_string()
        },
    }
}

fn action_drafts() -> Vec<CapsuleActionDraftView> {
    vec![
        CapsuleActionDraftView {
            id: "fincept.review_packet".to_string(),
            label: "Ask Claude to review current packet".to_string(),
            required_mode: "default".to_string(),
            verdict: Verdict::Pass,
            command: "raven capsules show fincept.autoexec-control-room".to_string(),
            receipt: "sanitized capsule view plus Claude review transcript".to_string(),
            abort_conditions: vec!["missing packet path".to_string()],
        },
        CapsuleActionDraftView {
            id: "fincept.codex_patch".to_string(),
            label: "Ask Codex to draft patch in playground".to_string(),
            required_mode: "default".to_string(),
            verdict: Verdict::Flag,
            command: "draft only; operator chooses worktree and patch scope".to_string(),
            receipt: "diff summary, tests, and residual risk".to_string(),
            abort_conditions: vec!["main lane mutation requested".to_string()],
        },
    ]
}

fn mode_ladder() -> Vec<CapsuleModeView> {
    vec![
        CapsuleModeView {
            name: "default".to_string(),
            may: "Read, refresh, compare, and draft actions.".to_string(),
            blocks: "Any mutation or live execution.".to_string(),
            implemented: true,
        },
        CapsuleModeView {
            name: "--auto".to_string(),
            may: "Future low-risk refreshes and receipts.".to_string(),
            blocks: "Branch mutation, live trading, and secret changes.".to_string(),
            implemented: false,
        },
        CapsuleModeView {
            name: "--sandbox-auto".to_string(),
            may: "Future dry-run or paper-only execution in isolated space.".to_string(),
            blocks: "Main lane mutation and live surfaces.".to_string(),
            implemented: false,
        },
        CapsuleModeView {
            name: "--sandbox-yolo".to_string(),
            may: "Future mutation in disposable worktrees only.".to_string(),
            blocks: "Main lane, live trading, and remote deploy gates.".to_string(),
            implemented: false,
        },
        CapsuleModeView {
            name: "--yolo".to_string(),
            may: "Future explicit operator unlock only.".to_string(),
            blocks: "Never default; requires red UI and post-run audit.".to_string(),
            implemented: false,
        },
    ]
}

fn present_verdict(ok: bool) -> Verdict {
    if ok {
        Verdict::Pass
    } else {
        Verdict::Flag
    }
}

fn summarize_verdict(gates: &[CapsuleGateView]) -> Verdict {
    if gates.iter().any(|gate| gate.verdict == Verdict::Block) {
        Verdict::Block
    } else if gates.iter().any(|gate| gate.verdict == Verdict::Flag) {
        Verdict::Flag
    } else {
        Verdict::Pass
    }
}
```

In `raven-console/src/adapters/mod.rs`, add:

```rust
pub mod capsules;
```

- [ ] **Step 4: Run adapter tests**

```bash
cargo test --manifest-path raven-console/Cargo.toml fincept_capsule_
```

Expected: PASS.

- [ ] **Step 5: Commit the adapter**

```bash
git add use-cases/hermes-everos-memory/raven-console/src/adapters/capsules.rs use-cases/hermes-everos-memory/raven-console/src/adapters/mod.rs
git commit -m "feat(raven): add fincept mission capsule adapter" -m "Add a read-only Fincept auto-exec capsule adapter with source, gate, mode, and action draft views." -m "Co-authored-by: Codex <noreply@openai.com>"
```

## Task 3: Wire Capsules Into Snapshot And CLI

**Files:**
- Modify: `use-cases/hermes-everos-memory/raven-console/src/snapshot.rs`
- Modify: `use-cases/hermes-everos-memory/raven-console/src/commands.rs`
- Modify: `use-cases/hermes-everos-memory/raven-console/src/output.rs`

- [ ] **Step 1: Write the failing snapshot test**

In `raven-console/src/snapshot.rs`, add this test to the existing test module:

```rust
#[test]
fn capsule_block_does_not_green_or_override_remote_gate_semantics() {
    let remote_gates = vec![RemoteGate {
        id: "DAS-2666".to_string(),
        name: "remote deploy".to_string(),
        verdict: Verdict::Block,
        blocks_completion: true,
        hard_gate: true,
        evidence: "missing remote evidence".to_string(),
        gate_effect: "blocks remote".to_string(),
    }];

    assert_eq!(overall_verdict(Verdict::Pass, &remote_gates), Verdict::Flag);
}
```

This test intentionally preserves the existing remote hard-gate behavior while
capsules are added.

- [ ] **Step 2: Run the test**

```bash
cargo test --manifest-path raven-console/Cargo.toml capsule_block_does_not_green_or_override_remote_gate_semantics
```

Expected: PASS or duplicate-test compile error if the assertion was already
covered. If duplicate, rename it to `remote_block_still_wins_after_capsules`.

- [ ] **Step 3: Add capsules to the snapshot**

In `raven-console/src/snapshot.rs`, update the adapter import:

```rust
use crate::adapters::{capsules, muw, packet, sc, verify};
```

In `assemble`, before `RavenSnapshot {`, add:

```rust
    let mission_capsules = capsules::mission_capsules();
```

In the `RavenSnapshot` initializer, after `sc,`, add:

```rust
        mission_capsules,
```

- [ ] **Step 4: Add CLI command types**

In `raven-console/src/commands.rs`, add a command variant:

```rust
    /// Inspect installed mission capsules.
    Capsules {
        #[command(subcommand)]
        command: Option<CapsulesCommand>,
    },
```

Add the subcommand enum after `AgentsCommand`:

```rust
#[derive(Subcommand)]
pub enum CapsulesCommand {
    /// List mission capsules.
    List,
    /// Show one mission capsule by id.
    Show { id: String },
}
```

- [ ] **Step 5: Add CLI command execution**

In `execute`, add this match arm before `Commands::Gates`:

```rust
        Commands::Capsules { command } => {
            let snapshot = snapshot::build(ctx);
            match command.unwrap_or(CapsulesCommand::List) {
                CapsulesCommand::List => {
                    if cli.json {
                        output::json(&snapshot.mission_capsules)
                    } else {
                        output::capsules(&snapshot);
                        Ok(())
                    }
                }
                CapsulesCommand::Show { id } => {
                    let capsule = snapshot
                        .mission_capsules
                        .iter()
                        .find(|capsule| capsule.id == id)
                        .ok_or_else(|| format!("unknown mission capsule `{id}`"))?;
                    if cli.json {
                        output::json(capsule)
                    } else {
                        output::capsule(capsule);
                        Ok(())
                    }
                }
            }
        }
```

In `dispatch_repl`, add:

```rust
        "/capsules" => output::capsules(&snapshot::build(ctx)),
```

And in the `/help` block, add:

```rust
            println!("/capsules");
```

- [ ] **Step 6: Add output helpers**

In `raven-console/src/output.rs`, extend the model import:

```rust
    MissionCapsuleView,
```

Add these functions after `agents`:

```rust
pub fn capsules(snapshot: &RavenSnapshot) {
    line("RAVEN_CAPSULES");
    line("VERDICT: FLAG");
    for capsule in &snapshot.mission_capsules {
        line(&format!(
            "- {}: {} mode={} summary={}",
            capsule.id, capsule.verdict, capsule.mode, capsule.summary
        ));
    }
}

pub fn capsule(capsule: &MissionCapsuleView) {
    line("RAVEN_CAPSULE");
    line(&format!("ID: {}", capsule.id));
    line(&format!("TITLE: {}", capsule.title));
    line(&format!("VERDICT: {}", capsule.verdict));
    line(&format!("MODE: {}", capsule.mode));
    line(&format!("SUMMARY: {}", capsule.summary));
    line("");
    line("SOURCES:");
    for source in &capsule.sources {
        line(&format!(
            "- {} / {}: {} evidence={}",
            source.id, source.label, source.verdict, source.evidence
        ));
    }
    line("");
    line("GATES:");
    for gate in &capsule.gates {
        line(&format!(
            "- {} / {}: {} blocks={} evidence={}",
            gate.id, gate.name, gate.verdict, gate.blocks_completion, gate.evidence
        ));
    }
    line("");
    line("ACTION_DRAFTS:");
    for action in &capsule.actions {
        line(&format!(
            "- {} / {}: {} mode={} command={} receipt={}",
            action.id, action.label, action.verdict, action.required_mode, action.command, action.receipt
        ));
    }
    line("");
    line("MODES:");
    for mode in &capsule.modes {
        line(&format!(
            "- {} implemented={} may={} blocks={}",
            mode.name, mode.implemented, mode.may, mode.blocks
        ));
    }
}
```

- [ ] **Step 7: Run CLI smokes**

```bash
bin/raven capsules list
bin/raven capsules show fincept.autoexec-control-room
bin/raven --json capsules show fincept.autoexec-control-room
```

Expected:

- `list` prints `RAVEN_CAPSULES`.
- `show` prints `RAVEN_CAPSULE`.
- JSON contains `"id": "fincept.autoexec-control-room"`.
- Without `RAVEN_FINCEPT_ROOT`, verdict is `FLAG`, not a crash.

- [ ] **Step 8: Commit snapshot and CLI wiring**

```bash
git add use-cases/hermes-everos-memory/raven-console/src/snapshot.rs use-cases/hermes-everos-memory/raven-console/src/commands.rs use-cases/hermes-everos-memory/raven-console/src/output.rs
git commit -m "feat(raven): expose mission capsules in cli" -m "Wire mission capsules into Raven snapshots and add read-only capsule list/show commands." -m "Co-authored-by: Codex <noreply@openai.com>"
```

## Task 4: Add Capsules Panel To Raven TUI

**Files:**
- Modify: `use-cases/hermes-everos-memory/raven-console/src/tui.rs`

- [ ] **Step 1: Write the failing palette test**

In the existing `#[cfg(test)]` module in `tui.rs`, add:

```rust
#[test]
fn palette_routes_to_capsules_panel() {
    let mut state = TuiState::default();

    apply_palette("capsules", &mut state);

    assert_eq!(state.panel, Panel::Capsules);
}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cargo test --manifest-path raven-console/Cargo.toml palette_routes_to_capsules_panel
```

Expected: FAIL because `Panel::Capsules` does not exist.

- [ ] **Step 3: Add panel enum and key routing**

In `Panel`, add:

```rust
    Capsules,
```

In `handle_normal_key`, add:

```rust
        KeyCode::Char('x') => state.panel = Panel::Capsules,
```

In `apply_palette`, add:

```rust
        "capsules" | "capsule" | "x" => state.panel = Panel::Capsules,
```

- [ ] **Step 4: Add rail item and render match**

In `render_rail`, add this item before Gates:

```rust
        ("x", "Capsules", "missions", Panel::Capsules),
```

In `render_panel`, add:

```rust
        Panel::Capsules => ("Capsules", capsule_lines(snapshot)),
```

In `panel_color`, add:

```rust
        Panel::Capsules => Color::LightGreen,
```

In `help_lines`, replace the panel text with:

```rust
        kv(
            "panels",
            "s status | p packet | h chat | m memory | a agents",
        ),
        kv("panels", "x capsules | g gates | r runs | d doctor"),
        kv("panels", "n native audit | o superconductor"),
```

In the normal input prompt string, include `x`:

```rust
"keys: h chat | i input | u refresh | ? help | : palette | / memory | s/p/m/a/x/g/r/o/d/n panels | q quit"
```

- [ ] **Step 5: Add capsule panel renderer**

Add this function near `agent_lines`:

```rust
fn capsule_lines(snapshot: &RavenSnapshot) -> Vec<Line<'static>> {
    let mut lines = vec![
        section("MISSION CAPSULES"),
        Line::from(vec![
            Span::styled(
                "default mode is read-only; action drafts do not execute.",
                Style::default().fg(Color::Gray),
            ),
        ]),
        Line::from(""),
    ];

    for capsule in &snapshot.mission_capsules {
        lines.push(Line::from(vec![
            verdict_span(capsule.verdict.to_string()),
            Span::raw(" "),
            Span::styled(
                format!("{:<34}", capsule.id),
                Style::default().fg(Color::White),
            ),
            Span::styled(capsule.summary.clone(), Style::default().fg(Color::Gray)),
        ]));
        lines.push(Line::from(vec![
            Span::styled("mode      ", Style::default().fg(Color::DarkGray)),
            Span::styled(capsule.mode.clone(), Style::default().fg(Color::Cyan)),
        ]));
        lines.push(Line::from(vec![
            Span::styled("gates     ", Style::default().fg(Color::DarkGray)),
            Span::styled(
                capsule
                    .gates
                    .iter()
                    .map(|gate| format!("{}={}", gate.id, gate.verdict))
                    .collect::<Vec<_>>()
                    .join(" | "),
                Style::default().fg(Color::Gray),
            ),
        ]));
        lines.push(Line::from(vec![
            Span::styled("actions   ", Style::default().fg(Color::DarkGray)),
            Span::styled(
                capsule
                    .actions
                    .iter()
                    .map(|action| action.id.clone())
                    .collect::<Vec<_>>()
                    .join(" | "),
                Style::default().fg(Color::Gray),
            ),
        ]));
        lines.push(Line::from(""));
    }

    lines
}
```

- [ ] **Step 6: Run TUI tests and smoke**

```bash
cargo test --manifest-path raven-console/Cargo.toml palette_routes_to_capsules_panel
RAVEN_TUI_ONCE=1 bin/raven tui
```

Expected:

- Test PASS.
- TUI smoke output contains `COMMAND RAIL`.
- Pressing `x` in interactive TUI would route to Capsules panel.

- [ ] **Step 7: Commit TUI panel**

```bash
git add use-cases/hermes-everos-memory/raven-console/src/tui.rs
git commit -m "feat(raven): add mission capsule tui panel" -m "Expose installed mission capsules in the Raven TUI command rail while preserving read-only default mode." -m "Co-authored-by: Codex <noreply@openai.com>"
```

## Task 5: Document Capsule Contract And SkillHub Fixture

**Files:**
- Create: `use-cases/hermes-everos-memory/skillhub/fixtures/fincept-autoexec-control-room-capsule.json`
- Modify: `use-cases/hermes-everos-memory/raven/COMMAND_CONTRACT.md`
- Modify: `use-cases/hermes-everos-memory/raven/README.md`
- Modify: `use-cases/hermes-everos-memory/justfile`

- [ ] **Step 1: Add SkillHub fixture**

Create `skillhub/fixtures/fincept-autoexec-control-room-capsule.json`:

```json
{
  "id": "fincept.autoexec-control-room",
  "name": "Fincept Auto-Exec Control Room",
  "summary": "Read-only Raven mission capsule for observing Fincept auto-exec gates, source truth, action drafts, and receipts.",
  "owner_id": "operator",
  "visibility": "private",
  "status": "draft",
  "version": "0.1.0",
  "source": "manual",
  "domains": ["raven", "fincept", "auto-exec", "mission-capsule"],
  "install_targets": ["raven", "hermes", "claude_code"],
  "evidence_refs": [
    "raven/COMMAND_CONTRACT.md",
    "raven-console/src/adapters/capsules.rs",
    "raven-console/src/model.rs"
  ],
  "body_markdown": "# Fincept Auto-Exec Control Room\n\nA read-only Raven mission capsule that observes Fincept webhook, dispatcher, dispatch log, source allowlist, position-lock, and live gate evidence. V1 drafts actions only; it does not mutate worktrees or execute trades.\n\n## Modes\n\n- default: read, refresh, compare, draft actions\n- --auto: future receipt-only automation\n- --sandbox-auto: future dry-run or paper-only sandbox\n- --sandbox-yolo: future disposable worktree mutation\n- --yolo: future explicit operator unlock only\n\n## Hard rule\n\nLocal smoke does not green live or remote gates."
}
```

- [ ] **Step 2: Update command contract**

In `raven/COMMAND_CONTRACT.md`, add this command row after `raven agents list`:

```markdown
| `raven capsules [list|show <id>] [--json]` | built-in capsule adapters and optional source roots | mission capsule table or single capsule view | capsules are read-only in v1; action drafts do not execute |
```

Add this section before `## Gate Semantics`:

```markdown
## Mission Capsule Behavior

Mission capsules package one operator mission as source adapters, gates, panels,
action drafts, mode ladder, and receipt policy.

V1 ships `fincept.autoexec-control-room` as a read-only capsule. It can inspect
Fincept source truth when `RAVEN_FINCEPT_ROOT` is set, but it does not write to
Fincept, arm live execution, cherry-pick patches, or change remote deploy gates.

Capsule verdicts are visible inside Raven snapshots and UI panels. They do not
launder remote hard gates: a local capsule PASS cannot change `DAS-2666` or any
other remote gate verdict.
```

- [ ] **Step 3: Update README commands**

In `raven/README.md`, add these commands to the console entrypoints list:

```bash
bin/raven capsules list
bin/raven --json capsules list
bin/raven capsules show fincept.autoexec-control-room
bin/raven --json capsules show fincept.autoexec-control-room
```

Add these just targets to the Just targets list:

```bash
just raven-capsules
just raven-capsule-fincept
```

- [ ] **Step 4: Add just targets**

In `use-cases/hermes-everos-memory/justfile`, after `raven-agents`, add:

```make
raven-capsules:
  bin/raven capsules list

raven-capsule-fincept:
  bin/raven capsules show fincept.autoexec-control-room
```

After `skillhub-sample`, add:

```make
skillhub-fincept-capsule:
  node bin/skillhub-packet.mjs validate skillhub/fixtures/fincept-autoexec-control-room-capsule.json
```

- [ ] **Step 5: Run docs/fixture smokes**

```bash
node bin/skillhub-packet.mjs validate skillhub/fixtures/fincept-autoexec-control-room-capsule.json
just raven-capsules
just raven-capsule-fincept
```

Expected:

- SkillHub validation exits 0.
- `just raven-capsules` prints `RAVEN_CAPSULES`.
- `just raven-capsule-fincept` prints `RAVEN_CAPSULE`.

- [ ] **Step 6: Commit docs and fixture**

```bash
git add use-cases/hermes-everos-memory/skillhub/fixtures/fincept-autoexec-control-room-capsule.json use-cases/hermes-everos-memory/raven/COMMAND_CONTRACT.md use-cases/hermes-everos-memory/raven/README.md use-cases/hermes-everos-memory/justfile
git commit -m "docs(raven): document fincept mission capsule" -m "Add the Fincept auto-exec mission capsule contract, SkillHub fixture, and smoke targets." -m "Co-authored-by: Codex <noreply@openai.com>"
```

## Task 6: Full Verification And Closeout

**Files:**
- Verify all changed EverOS files.

- [ ] **Step 1: Run focused tests**

```bash
cargo test --manifest-path raven-console/Cargo.toml mission_capsule_serializes_default_read_only_mode
cargo test --manifest-path raven-console/Cargo.toml fincept_capsule_
cargo test --manifest-path raven-console/Cargo.toml palette_routes_to_capsules_panel
```

Expected: all PASS.

- [ ] **Step 2: Run Raven console check**

```bash
just raven-console-check
```

Expected:

- `cargo fmt --check` PASS.
- `cargo clippy -D warnings` PASS.
- `cargo test` PASS.

- [ ] **Step 3: Run CLI and TUI smoke**

```bash
bin/raven capsules list
bin/raven capsules show fincept.autoexec-control-room
bin/raven --json capsules show fincept.autoexec-control-room
RAVEN_TUI_ONCE=1 bin/raven tui
```

Expected:

- Capsule commands do not crash without `RAVEN_FINCEPT_ROOT`.
- JSON is sanitized.
- TUI smoke renders deterministic output.

- [ ] **Step 4: Run optional live-source read smoke**

Only run this on a trusted local machine where the Fincept repo is present.
Use a repo-relative or shell-provided path; do not paste private paths into
public screenshots.

```bash
RAVEN_FINCEPT_ROOT="$FINCEPT_ROOT" bin/raven capsules show fincept.autoexec-control-room
```

Expected:

- If Fincept files are present, source truth gates improve from FLAG to PASS.
- The capsule remains read-only.
- Live mode remains BLOCK if `P5B_AUTOEXEC_ENABLED=1`.

- [ ] **Step 5: Inspect git state**

```bash
git status --short
git log --oneline -6
```

Expected:

- Only intentional files are modified.
- The four implementation commits are present.
- Existing unrelated dirty files remain untouched.

- [ ] **Step 6: Final closeout note**

Report:

```text
VERDICT: PASS / FLAG / BLOCK
Changed: model, adapter, CLI, TUI, docs, SkillHub fixture.
Verified: exact commands and results.
Residual risk: Fincept live-source adapter is read-only and env-root dependent; Fincept WebUI remains separate.
Next: decide whether to build HTML owner-packet export or richer journal adapter.
```
