---
name: keephive-helper
title: Keephive Helper – {{platform_title}}
description: Keep {{platform_title}} sessions synced with the keephive knowledge loop.
version: 1
---

# Keephive Helper – {{platform_title}}

## What this skill does
- Keeps your agent synced with the keephive knowledge loop: memory (`hive remember`), recall (`hive recall`), verification (`hive verify`), reflection (`hive reflect`), audit (`hive audit`).
- Uses keephive MCP tools over stdio so every agent instance—local or remote—can query the same data store (`keephive mcp-serve`).
- Surfaces TODOs, rules, and daily logs so follow-ups never get lost between prompts.

## Quick reactions
| Situation | Say this | What happens |
|-----------|----------|--------------|
| Capture a fact | `fact: <detail>` | Invokes `hive remember` with `FACT:` prefix |
| Log a decision | `decision: <rationale>` | Writes decision entry, suggests follow-up verify |
| Need prior context | `search logs for <topic>` | Calls `hive recall` and shares top matches |
| Task drifting | `todo: <task>` | Adds TODO, mirrors in dashboard + session reminders |
| Unsure about freshness | `verify <topic>` | Runs `hive verify`, reports results or queues follow-up |

## Session hygiene
1. Call `hive status` at session start for streaks, TODOs, and backend health.
2. When code changes slow down, run `hive reflect` for pattern spotting.
3. Finish long sessions with `hive audit` to capture action items.

## MCP endpoints your agent can call
- `hive_recall` – semantic + keyword search over memory/logs/TODOs.
- `hive_status` – structured session summary (facts/TODOs/streaks/backend state).
- `hive_remember` – store new facts/decisions/insights (automatically prefixes).
- `hive_todo` – CRUD for TODO list items.
- `hive_verify` – queue structured verification tasks when your LLM backend supports tools.
- `hive_guides` – fetch bundled guides and skill prompts.

{{platform_specific}}

## Need to recover?
- No LLM backend available ⇒ keephive queues the work; rerun when API keys/CLI return.
- Reset hooks ⇒ `hive setup --platform {{platform_slug}} --yes` re-syncs skill + scripts.
- Manual removal ⇒ delete the `keephive-helper` skill file and rerun `hive setup`.
