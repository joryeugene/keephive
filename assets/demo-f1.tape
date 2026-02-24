# keephive F1 maintenance flow — checkup → flow → daemon
# Record: HIVE_HOME="$HOME/.claude/hive-demo" vhs assets/demo-f1.tape
# Setup:  just demo-seed

Output assets/cli-demo-f1.gif

Set FontSize 14
Set Width 1100
Set Height 650
Set Theme "GitHub Dark"
Set Padding 20
Set TypingSpeed 45ms

# ── Arrive: what's the daemon doing? ──
Type "hive daemon status"
Enter
Sleep 3.5s

# ── Inspect: production health in 6 stages ──
Type "hive checkup"
Enter
Sleep 5s

# ── Drain: guided queue review ──
Type "hive flow --skip-verify"
Enter
Sleep 4s

# ── Proposals: what has KingBee noticed? ──
Type "hive improve list"
Enter
Sleep 2.5s

# ── Trigger: distill sessions into identity ──
Type "hive daemon run soul-update"
Enter
Sleep 3s

# ── Verify: health as JSON for scripting ──
Type "hive checkup --json | python3 -m json.tool | head -20"
Enter
Sleep 3s
