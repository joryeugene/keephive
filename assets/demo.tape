# keephive CLI demo
# Record: vhs assets/demo.tape
# Requires: demo profile active (hive profile use demo)

Output assets/cli-demo.gif

Set FontSize 14
Set Width 1000
Set Height 600
Set Theme "GitHub Dark"
Set Padding 20
Set TypingSpeed 40ms

# Status overview
Type "hive"
Enter
Sleep 3s

# Capture a fact
Type "hive r 'FACT: Redis cluster uses 6-node ring with 3 replicas'"
Enter
Sleep 2s

# Instant recall
Type "hive rc redis"
Enter
Sleep 3s

# Activity stats
Type "hive stats"
Enter
Sleep 3s
