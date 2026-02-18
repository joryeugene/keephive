# Commit Draft

Given the staged diff below, draft a concise commit message.

## Rules
- Focus on WHY the change was made, not WHAT changed (the diff shows what)
- Format: `type(scope): description`
- Types: feat, fix, refactor, docs, test, chore, perf
- Scope: the module, component, or area affected
- Description: imperative mood, lowercase, no period, under 72 chars
- If multiple logical changes, suggest separate commits

## Examples
- `feat(auth): add JWT refresh token rotation`
- `fix(api): handle null response from user endpoint`
- `refactor(hooks): replace polling with event-driven capture`
- `chore(deps): bump hive to v0.3.0`

## Diff
(paste `git diff --cached` output here)
