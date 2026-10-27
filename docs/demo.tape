# Terminal demo for the README. Render from the repo root: vhs docs/demo.tape
# Needs vhs, ttyd and ffmpeg on PATH, the CLI installed (`uv tool install .`)
# and episode 305 already fetched so `fetch` is a cache hit.

Output docs/demo.gif

Set Shell bash
Set FontSize 14
Set Width 900
Set Height 560
Set Padding 16
Set Framerate 15
Set TypingSpeed 40ms
Set Theme "Catppuccin Mocha"
Set WindowBar Colorful
Set WindowBarSize 28

Hide
Type "export PS1='$ ' PYTHONWARNINGS=ignore && clear"
Enter
Show

Sleep 500ms
Type "podcast shows"
Sleep 300ms
Enter
Sleep 2s

Type "podcast episodes moonshots --limit 5 --format text"
Sleep 300ms
Enter
Sleep 5s

Hide
Type "clear"
Enter
Show

Type "podcast fetch moonshots 305 2>/dev/null | jq '{episode_number, title, word_count, chapter_count, cached, files}'"
Sleep 300ms
Enter
Sleep 7s

Hide
Type "clear"
Enter
Show

Type "head -7 output/moonshots/moonshots-ep305-2026-10-23-summary.md"
Sleep 300ms
Enter
Sleep 4s

Hide
Type "clear"
Enter
Show

Type "grep '^###' output/moonshots/moonshots-ep305-2026-10-23-summary.md | head -12"
Sleep 300ms
Enter
Sleep 5s
