## Claude Code specifics
- Hooks: SessionStart, PreCompact, PostToolUse, UserPromptSubmit, Stop, SessionEnd, TaskCompleted all call keephive helpers. Keep them enabled for full telemetry.
- Binary path: `claude -p` stays available, but keephive falls back to API/Gemini/OpenAI when needed.
- Skill location: `~/.claude/skills/keephive-helper/SKILL.md` (auto-updated by `hive setup`).
- Telemetry: rich session metrics pulled from `~/.claude/usage-data/`; dashboards merge them with keephive telemetry.
