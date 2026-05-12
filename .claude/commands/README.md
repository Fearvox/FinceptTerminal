# Command Reference

This directory contains 79 slash commands available via `/command-name` in the Claude Code REPL. Many are thin shims that delegate to skills in `.claude/skills/everything-claude-code/`; others are standalone workflows.

## Categories

### Legacy Shims (delegate to skills -- prefer the skill directly)

These commands exist for backward compatibility. Each one simply invokes the corresponding skill.

| Command | Delegates to Skill |
|---|---|
| `agent-sort` | agent-sort |
| `claw` | nanoclaw-repl |
| `context-budget` | context-budget |
| `devfleet` | claude-devfleet |
| `docs` | documentation-lookup |
| `e2e` | e2e-testing |
| `eval` | eval-harness |
| `orchestrate` | dmux-workflows / autonomous-agent-harness |
| `prompt-optimize` | prompt-optimizer |
| `rules-distill` | rules-distill |
| `tdd` | tdd-workflow |
| `verify` | verification-loop |

### Planning and Design

| Command | Description |
|---|---|
| `plan` | Restate requirements, assess risks, create step-by-step implementation plan (waits for confirm) |
| `prp-prd` | Interactive PRD generator -- problem-first, hypothesis-driven product spec |
| `prp-plan` | Comprehensive feature implementation plan with codebase analysis and pattern extraction |
| `prp-implement` | Execute an implementation plan with rigorous validation loops |
| `feature-dev` | Guided feature development with codebase understanding and architecture focus |
| `blueprint` (via skill) | Architecture blueprinting |

### Code Review

| Command | Description |
|---|---|
| `code-review` | Local uncommitted changes or GitHub PR review (pass PR number/URL) |
| `review-pr` | Comprehensive PR review using specialized agents |
| `python-review` | Python code review (PEP 8, type hints, security, idioms) |
| `rust-review` | Rust code review (ownership, lifetimes, error handling, unsafe) |
| `go-review` | Go code review (idiomatic patterns, concurrency, error handling) |
| `cpp-review` | C++ code review (memory safety, modern idioms, concurrency) |
| `kotlin-review` | Kotlin code review (idiomatic patterns, null safety, coroutines) |
| `flutter-review` | Flutter/Dart code review (widgets, state management, a11y) |

### Build and Fix

| Command | Description |
|---|---|
| `build-fix` | Incrementally fix build and type errors with minimal, safe changes |
| `rust-build` | Fix Rust build errors, borrow checker issues, dependency problems |
| `go-build` | Fix Go build errors, go vet warnings, linter issues |
| `cpp-build` | Fix C++ build errors, CMake issues, linker problems |
| `kotlin-build` | Fix Kotlin/Gradle build errors, compiler warnings, dependency issues |
| `flutter-build` | Fix Dart analyzer errors and Flutter build failures |
| `gradle-build` | Fix Gradle build errors for Android and KMP projects |

### Testing and TDD

| Command | Description |
|---|---|
| `tdd` | TDD workflow (legacy shim -> tdd-workflow skill) |
| `test-coverage` | Analyze test coverage, identify gaps, generate missing tests (80%+ target) |
| `rust-test` | TDD for Rust -- write tests first, verify 80%+ coverage with cargo-llvm-cov |
| `go-test` | TDD for Go -- table-driven tests first, verify 80%+ coverage with go test |
| `cpp-test` | TDD for C++ -- GoogleTest tests first, verify coverage with gcov/lcov |
| `kotlin-test` | TDD for Kotlin -- Kotest tests first, verify 80%+ coverage with Kover |
| `flutter-test` | Run Flutter/Dart tests (unit, widget, golden, integration) |
| `e2e` | End-to-end testing (legacy shim -> e2e-testing skill) |

### Refactoring and Quality

| Command | Description |
|---|---|
| `refactor-clean` | Safely remove dead code with test verification at every step |
| `quality-gate` | Run the ECC quality pipeline on demand |
| `santa-loop` | Adversarial dual-review convergence loop -- two reviewers must both approve |

### Git, PR, and Commit Workflow

| Command | Description |
|---|---|
| `prp-commit` | Quick commit with natural language file targeting |
| `prp-pr` | Create a GitHub PR from current branch with unpushed commits |

### Documentation

| Command | Description |
|---|---|
| `update-docs` | Sync documentation with the codebase |
| `update-codemaps` | Analyze codebase structure and generate architecture documentation |
| `docs` | Documentation lookup (legacy shim -> documentation-lookup skill) |

### Multi-Model Workflows

| Command | Description |
|---|---|
| `multi-workflow` | Multi-model collaborative development (Research -> Execute -> Review) |
| `multi-plan` | Multi-model collaborative planning |
| `multi-execute` | Multi-model collaborative execution |
| `multi-frontend` | Frontend-focused development (Gemini-led) |
| `multi-backend` | Backend-focused development (Codex-led) |

### Session Management

| Command | Description |
|---|---|
| `save-session` | Save current session state for later resumption |
| `resume-session` | Load most recent session and resume work |
| `sessions` | Manage session history, aliases, and metadata |
| `checkpoint` | Create or verify a checkpoint in your workflow |

### Loop and Automation

| Command | Description |
|---|---|
| `loop-start` | Start a managed autonomous loop pattern with safety defaults |
| `loop-status` | Inspect active loop state, progress, and failure signals |
| `orchestrate` | Multi-agent orchestration (legacy shim -> dmux-workflows) |

### Learning and Instincts

| Command | Description |
|---|---|
| `learn` | Extract reusable patterns from the current session |
| `learn-eval` | Extract patterns with self-evaluation and smart save-location |
| `instinct-status` | Show learned instincts (project + global) with confidence |
| `instinct-import` | Import instincts from file or URL |
| `instinct-export` | Export instincts to a file |
| `evolve` | Analyze instincts and suggest evolved structures |
| `promote` | Promote project-scoped instincts to global scope |
| `prune` | Delete pending instincts older than 30 days |
| `projects` | List known projects and their instinct statistics |

### Hooks and Configuration

| Command | Description |
|---|---|
| `hookify` | Create hooks to prevent unwanted behaviors |
| `hookify-list` | List all configured hookify rules |
| `hookify-configure` | Enable or disable hookify rules interactively |
| `hookify-help` | Get help with the hookify system |
| `setup-pm` | Configure preferred package manager (npm/pnpm/yarn/bun) |

### GAN-Style Generators

| Command | Description |
|---|---|
| `gan-build` | GAN-style iterative build loop (generator-evaluator cycles) |
| `gan-design` | GAN-style iterative design loop |

### Evaluation and Auditing

| Command | Description |
|---|---|
| `eval` | Evaluation harness (legacy shim -> eval-harness skill) |
| `harness-audit` | Deterministic repository harness audit with prioritized scorecard |
| `model-route` | Recommend best model tier for current task by complexity and budget |

### Skill Management

| Command | Description |
|---|---|
| `skill-create` | Analyze local git history to extract patterns and generate SKILL.md files |
| `skill-health` | Show skill portfolio health dashboard with charts and analytics |

### Project Tooling

| Command | Description |
|---|---|
| `pm2` | Auto-analyze project and generate PM2 service commands |
| `jira` | Retrieve Jira tickets, analyze requirements, update status |

### Utility

| Command | Description |
|---|---|
| `aside` | Answer a quick side question without losing current task context |
| `agent-sort` | Agent sorting (legacy shim) |

## Overlaps with Bundled Skills

Some commands overlap with skills that are compiled into the CLI binary (`src/skills/bundled/`):

- `/verify` command -> `verification-loop` skill -> also bundled `verify` skill (Anthropic-internal only, gated on `USER_TYPE=ant`)
- `/tdd` command -> `tdd-workflow` skill
- `/docs` command -> `documentation-lookup` skill
- `/e2e` command -> `e2e-testing` skill
- `/eval` command -> `eval-harness` skill
- `simplify` is bundled-only (no command file -- invoked via skill system)
- `loop` is bundled-only (feature-gated on `AGENT_TRIGGERS`)
- `remember`, `debug`, `stuck`, `keybindings`, `batch` are bundled-only

The general pattern is: command files (`.md`) are loaded from disk at runtime, while bundled skills (`.ts` in `src/skills/bundled/`) are compiled into the binary. Legacy shim commands delegate to disk-based skills in `.claude/skills/everything-claude-code/`.
