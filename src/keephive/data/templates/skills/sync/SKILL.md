---
name: sync
description: Install or update keephive and its ecosystem. Bootstraps a fresh machine or brings an existing install current. Use when the user says "sync keephive", "update keephive", "install keephive", or "set up keephive".
user-invocable: true
---

# keephive Sync

Install keephive from scratch or bring an existing installation up to date. This skill walks through each step, verifying as it goes.

## Step 1: Check for uv

Run `which uv` to confirm the package manager is available.

If `uv` is not found, tell the user:

> uv is required but not installed. Install it with:
> `curl -LsSf https://astral.sh/uv/install.sh | sh`
>
> Then re-run this sync.

Stop here if uv is missing. Do not proceed.

## Step 2: Install or update keephive

Check if keephive is already installed:

```bash
which keephive
```

If installed, update to the latest version:

```bash
uv tool install --force keephive
```

If not installed, install fresh:

```bash
uv tool install keephive
```

After install, verify:

```bash
keephive --version
```

Print the installed version.

## Step 3: Run setup

Run the full setup to register hooks, MCP server, seed content, and initialize KingBee:

```bash
keephive setup --yes
```

This is idempotent. Running it on an already-configured machine updates anything that drifted without destroying existing data.

## Step 4: Offer claude-stack companion

Read `~/.claude/settings.json` and check the `enabledPlugins` object for any key containing `claude-stack`.

If claude-stack is already installed, print:

> claude-stack is already installed. No action needed.

If claude-stack is NOT installed, ask the user:

> [claude-stack](https://github.com/joryeugene/claude-stack) provides engineering discipline for Claude Code: TDD, debugging protocols, spec-writing, verification workflows, and enforcement hooks. keephive handles memory. claude-stack handles methodology. They pair directly.
>
> Install claude-stack? (This requires user confirmation.)

Only proceed if the user explicitly says yes. If yes:

```bash
claude plugin install joryeugene/claude-stack
```

If the user declines, acknowledge and move on. Do not ask again.

## Step 5: Summary

Print what was done:

- keephive version installed/updated
- Hooks registered (count)
- MCP server status
- claude-stack status (installed / declined / already present)
- Next steps: `hive s` to check status, `hive doctor` to verify health
