# Troubleshooting keephive

Common issues and how to resolve them.

## 1. 401 Unauthorized (API Key Issues)

If you see an error like `■ unexpected status 401 Unauthorized: Incorrect API key provided`, it means the LLM backend is receiving an invalid key.

### Codex CLI
Codex stores its own API keys in `~/.codex/auth.json`. 
- **Fix**: Check `~/.codex/auth.json` and ensure `OPENAI_API_KEY` matches your current working key.
- **Tip**: This file is independent of your shell environment variables.

### Gemini CLI
Gemini uses `~/.gemini/auth.json` or environment variables.
- **Fix**: Run `gemini login` or check `~/.gemini/auth.json`.

### Environment Variables
For direct API backends (`anthropic_api`, `openai_api`, `gemini_api`):
- **Fix**: Ensure `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY` is exported in your shell.
- **Check**: Run `env | grep _API_KEY` to verify.

---

## 2. No LLM Backend Available

If `hive doctor` reports `No operational backend detected`:
- **Cause**: You haven't installed the `claude` CLI and haven't provided API keys for direct access.
- **Fix**:
  1. Install Claude Code: `npm install -g @anthropic-ai/claude-code`
  2. OR set an API key: `export ANTHROPIC_API_KEY="your-api-key"`
  3. To force CLI-only routing (skip direct API): `hive privacy cli`

---

## 3. Hooks Not Firing

If your sessions aren't being tracked in `hive stats` or `hive ps`:
- **Fix**: Run `hive setup`. This will re-register hooks in `~/.claude/settings.json`, `~/.gemini/settings.json`, etc.
- **Manual Check**: 
  - For Claude: Check `~/.claude/settings.json` for a `"hooks"` block.
  - For Gemini: Check `~/.gemini/settings.json`.

---

## 4. ModuleNotFoundError: No module named 'keephive'

If you see this when a hook runs:
- **Cause**: The `PYTHONPATH` isn't set correctly for the hook process.
- **Fix**: `keephive` tries to set `KEEPHIVE_PYTHONPATH` automatically during setup. If you moved the `keephive` source directory, run `hive setup` again.

---

## 5. Session Times Not Connecting

If `hive stats` shows durations of 0m for long sessions:
- **Cause**: Context compaction occurred and the original start time was lost from the active log.
- **Fix**: `keephive` v1.0.0+ automatically heals this by looking up the true start time in `~/.keephive/data/stats.json`. Ensure you are on the latest version.
