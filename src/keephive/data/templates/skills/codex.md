## Codex CLI specifics
- Hooks: the `notify` handler posts turn transcripts into keephive for memory capture and telemetry.
- Config file: `~/.codex/config.toml` gains a `notify = ["python3", "~/.keephive/hooks/codex/notify.py"]` entry with safe fallbacks.
- Skill location: `~/.codex/skills/keephive-helper/SKILL.md` (created on setup).
- Backend: when Anthropic and Gemini options are unavailable but OpenAI keys exist, keephive auto-switches to the OpenAI Responses API.
