# keephive F2 — KB identity channel + checkup Stage 3
# Record: HIVE_HOME="$HOME/.claude/hive-demo" vhs assets/demo-f2.tape
# Setup:  just demo-seed

Output assets/cli-demo-f2.gif

Set FontSize 14
Set Width 1100
Set Height 650
Set Theme "GitHub Dark"
Set Padding 20
Set TypingSpeed 45ms

# ── Seed: write something for KingBee (direct message via daily log for demo) ──
Type "hive r 'KingBee, stop starting sentences with \"I\"'"
Enter
Sleep 3.5s

# ── Observe: checkup Stage 3 shows KB queue depth + wander activity ──
Type "hive checkup"
Enter
Sleep 6s

# ── Explore: hive go --help shows new kb session mode ──
Type "hive go --help"
Enter
Sleep 3s

# ── Drain: soul-update processes the KB queue ──
Type "hive daemon run soul-update"
Enter
Sleep 4s

# ── Iterate: loop review flow ──
Type "hive run review"
Enter
Sleep 5s
