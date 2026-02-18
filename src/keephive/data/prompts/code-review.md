# Code Review

Examine the provided code for issues across four categories. For each issue found, report: severity (critical/warning/suggestion), location (file:line), description of the issue, and how to fix it.

## Security
Scan for command injection, XSS, SQL injection, and other OWASP top 10 vulnerabilities. Check for hardcoded secrets, credentials, or API keys. Verify input validation at system boundaries and proper authentication/authorization checks.

## Code Quality
Verify functions do one thing well. Flag dead code and unused imports. Check that error handling covers failure modes. Look for race conditions in async code and verify resource cleanup (connections, file handles, timers).

## Performance
Identify N+1 queries and unnecessary loops. Check for appropriate caching. Look for memory leaks (event listeners, subscriptions, closures). Verify large data sets use pagination or streaming.

## Patterns
Confirm consistency with existing codebase conventions. Flag premature abstraction. Verify dependencies are justified and minimal. Check that tests cover critical paths and edge cases.
