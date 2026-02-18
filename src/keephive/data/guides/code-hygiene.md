---
tags: [hygiene, code-quality, maintenance]
---

# Code Hygiene for AI-Assisted Development

## The Core Problem

AI agents lack continuity between sessions. Each prompt is isolated. The agent doesn't know your existing `formatDate` utility exists, so it writes a new one. This creates:

- Dead exports: functions nobody imports
- Duplicate logic: multiple implementations with subtle divergences
- Empty catch blocks: errors swallowed silently
- Orphaned types: interfaces for APIs that no longer exist
- Config drift: unused dependencies and outdated env vars

## Prevention (During Work)

Before writing new code:

1. hive_recall(concept) or `hive rc <concept>` to check for existing implementations
2. Search the codebase for similar patterns
3. Consolidate rather than duplicate

After finishing work:

- Remove dead code you introduced
- Log or handle exceptions (never empty catch blocks)
- Check for unused imports

## The Weekly Sweep

Combine deterministic tools with manual review:

1. Run linter with unused-code detection (ruff, knip, depcheck)
2. Search for duplicate function signatures
3. Audit recent AI-generated PRs for consolidation opportunities
4. Check for config drift (env vars documented vs used)
5. Update dependencies

## During AI Sessions

AI sessions compound the continuity problem. Each response is an opportunity to introduce drift.

Key practices:

- When the user gives multiple requests, capture each as a TODO before starting work
- Verify each change individually before moving to the next
- Record architecture decisions as they happen (DECISION: chose X over Y because Z)
- When you encounter unfamiliar code, search memory first (hive_recall)
- At session end: check for orphaned imports, dead code, and uncovered changes

## Recording Hygiene Findings

When you find dead code or duplicates:

- hive_remember("CORRECTION: found duplicate formatDate, consolidated to utils/date.ts")
- hive_remember("FACT: unused UserProfile type in types.ts, removed")

Or via CLI:

- `hive r "CORRECTION: found duplicate formatDate, consolidated to utils/date.ts"`
- `hive r "FACT: unused UserProfile type in types.ts, removed"`
