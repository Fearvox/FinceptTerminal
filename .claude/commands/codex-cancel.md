---
description: Cancel an active background Codex job in this repository
argument-hint: '[job-id]'
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

!`node "/home/user/claude-code-reimagine-for-learning/.claude/codex-plugin/codex-companion.mjs" cancel $ARGUMENTS`
