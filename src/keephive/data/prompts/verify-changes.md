# Verify Changes

Review the changes made in this session and verify each one.

## For Each Change
1. Identify the file, function, or component modified
2. Describe the before state (how it looked or behaved)
3. Describe the after state (how it looks or behaves now)
4. Run a command that proves the change works and paste the output
5. Check whether this change is covered by tests. If not, flag it.

## Red Flags
Report any of these immediately:
- A change claimed as "working" without a test run
- Files modified but not mentioned in the review
- New functionality without corresponding tests
- "Should work" language instead of actual proof
- Hardcoded values, secrets, or debug code left behind

## Output
For each change: VERIFIED (with proof command + output) or UNVERIFIED (with reason and suggested verification command).
