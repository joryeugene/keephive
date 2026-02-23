## Codex CLI specifics
- Hooks: the `notify` handler is available at `~/.keephive/hooks/codex/notify.py`. Codex config schemas differ between releases, so keephive does **not** auto-edit `config.toml`. Follow the troubleshooting guide to register the hook manually once Codex exposes a stable notify format.
- Skill location: `~/.codex/skills/keephive-helper/SKILL.md` (created on setup).
- Backend: when Anthropic and Gemini options are unavailable but OpenAI keys exist, keephive auto-switches to the OpenAI Responses API.
