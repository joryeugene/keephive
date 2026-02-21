# keephive CLI demo
# Record: HIVE_HOME="$HOME/.claude/hive-demo" vhs assets/demo.tape
# Setup:  just demo-seed

Output assets/cli-demo.gif

Set FontSize 14
Set Width 1000
Set Height 600
Set Theme "GitHub Dark"
Set Padding 20
Set TypingSpeed 40ms

# ── Arrive: what's my current state? ──
Type "hive"
Enter
Sleep 3s

# ── Capture: learned something new ──
Type "hive r 'FACT: API uses JWT RS256, tokens expire after 1h'"
Enter
Sleep 2s

# ── Recall: find it back instantly ──
Type "hive rc jwt"
Enter
Sleep 2.5s

# ── Track: add a new task ──
Type "hive t 'Migrate auth tokens to asymmetric signing'"
Enter
Sleep 2s

# ── Close: knock one off the list ──
Type "hive td 'rate'"
Enter
Sleep 2s

# ── Jot: quick thought to the scratchpad ──
Type "hive 1 'Review auth endpoints for token refresh'"
Enter
Sleep 1.5s

# ── Copy: note to clipboard ──
Type "hive nc"
Enter
Sleep 1.5s

# ── Review: what happened today? ──
Type "hive l"
Enter
Sleep 3s

# ── Reference: knowledge guide by prefix ──
Type "hive k api"
Enter
Sleep 3s

# ── Prompt: view a template by prefix ──
Type "hive p code"
Enter
Sleep 2.5s

# ── Big picture: activity sparkline ──
Type "hive stats"
Enter
Sleep 3s
