---
name: ult-evo
description: "xAI Grok 4-Agent system. Trigger: /ult-evo <agent> <input>. Agents: trend-radar(热搜商单雷达), meddicc-prep(MEDDICC会议备弹), academic-tone(学术语感校准), idea-catcher(灵感捕手). Built-in tools: x_search, web_search, code_interpreter."
---

# ult-evo — xAI Grok Agent System

4 个专用 Agent，全部由 xAI Grok 家族驱动，内置 x_search / web_search / code_interpreter。

## Usage

```
/ult-evo                          → 列出所有 Agent
/ult-evo trend-radar <话题>       → 热搜商单雷达
/ult-evo meddicc-prep <公司/人名> → MEDDICC会议备弹
/ult-evo academic-tone <段落>     → 学术语感校准
/ult-evo idea-catcher <想法>      → 灵感捕手
```

## Execution

When this skill is invoked, execute the following:

### Step 1: Parse args

Extract `<agent-id>` and `<input>` from the skill arguments.

- If no args or `--list`: run `bun ultra-evo/src/cli.ts --list` and display the result.
- If args provided: first token = agent-id, rest = input text.

### Step 2: Validate environment

```bash
echo $XAI_API_KEY | head -c 4
```

If empty or not starting with `xai-`, stop and tell the user:
```
export XAI_API_KEY=<your-key>
```

### Step 3: Execute agent

```bash
export XAI_API_KEY=$XAI_API_KEY && bun ultra-evo/src/cli.ts <agent-id> "<input>" --no-stream
```

Timeout: 120s for trend-radar/meddicc-prep (reasoning model + tools), 30s for academic-tone/idea-catcher (fast model).

### Step 4: Display result

Show the agent's output directly to the user. If citations are present, list them at the bottom.

## Agent Reference

| ID | Name | Model | Tools | Use Case |
|----|------|-------|-------|----------|
| trend-radar | 热搜商单雷达 | grok-4.20-reasoning | x_search, web_search | 社交热点→商单机会 |
| meddicc-prep | MEDDICC会议备弹 | grok-4.20-reasoning | web_search, x_search | 客户背调+MEDDICC六维卡 |
| academic-tone | 学术语感校准 | grok-4-1-fast | none | 论文口语化检查 |
| idea-catcher | 灵感捕手 | grok-4-1-fast | code_interpreter | 碎片想法→结构化记录 |

## Flags

- `--model <name>`: Override model (e.g., `--model grok-4-1-fast`)
- `--no-stream`: Disable streaming (default for skill invocation)
- `--json`: Output raw JSON

## Files

- `ultra-evo/src/cli.ts` — CLI entry point
- `ultra-evo/src/client.ts` — xAI /v1/responses API client
- `ultra-evo/src/router.ts` — Auto model+tool routing
- `ultra-evo/src/agents/*.ts` — Agent system prompts
