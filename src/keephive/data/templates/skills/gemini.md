## Gemini CLI specifics
- Hooks: SessionStart, BeforeTool, AfterModel shims capture prompts, tool runs, and model output summaries to keep telemetry in sync.
- Config file: `~/.gemini/settings.json` gets updated with keephive hook entries and watcher config.
- Skill location: `~/.gemini/skills/keephive-helper/SKILL.md`.
- Backend: if `GEMINI_API_KEY` is present and Anthropic tooling is missing, keephive automatically routes structured prompts through Gemini.
