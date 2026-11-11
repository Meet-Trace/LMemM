# Handoff: real-Mac smoke tests

Cloud sessions have no Mac, so **nothing below has been run**. Each pair is "unverified" until the
output is pasted into the PR and a box is ticked. These tests never send a prompt: stop before
pressing send, and use a throwaway conversation (no real client or private content).

Setup: `./build_app.sh` is not needed; run from the repo with the venv:
`source .venv/bin/activate && python3 lmemm.py handoff panel`, with Accessibility granted to your terminal.
Keep the terminal visible: it prints the hotkey status and (see step 0) the record.

## 0. Hotkey and panel (no assistant needed)
1. Start the runner. Expect `Handoff hotkey ⌃⌥H: ok`. *(AC-01)*
2. Press ⌃⌥H in Notes. Expect the panel within a blink, "Couldn't identify a supported conversation", no destinations selectable. *(AC-05)*
3. Press ⌃⌥H five times quickly. Expect one panel. *(FR-1)*
4. Press Esc: panel closes; clipboard unchanged (`pbpaste`). *(AC-11)*
5. Rerun with `--key ctrl+opt+n` (taken by the note hotkey). Expect `conflict`, exit 1. Then `--key h`: expect a "needs Control, Option or Command" failure.
6. Panel placement: check on the built-in display and an external one, and at a scaled resolution; panel must be fully on screen and not steal the source app's focus (type a letter in the source app after opening: it should still receive keys only after Esc).
Record: ____

## 1. Source extraction (repeat per source)
Sources to try: Claude desktop, ChatGPT desktop, claude.ai / chatgpt.com / gemini.google.com in Chrome and Safari, Cursor chat.
For each, with a 6-message synthetic chat that includes the sentence "It must keep latency under 50 ms":
1. Press ⌃⌥H in the source. Does the panel name the right app and conversation title? (yes / no / wrong)
2. Is the status "Partial: only the visible part…"? Press Return on a destination, then **Review context**: paste the review text below.
3. Does the review text contain the 50 ms constraint, and is anything from other windows or tabs in it? (it must not)
4. Scroll the chat so the first messages are off screen and repeat: the output should shrink, still say partial.
5. Revoke Accessibility for the terminal, press ⌃⌥H: expect "Accessibility access is off", no crash. Re-grant, press again: works without restarting the runner? 
6. Quit and relaunch the assistant, repeat step 1.
Paste for each source: app, panel text, review text (redact anything private), timings printed by the runner.

## 2. Destinations (repeat per destination)
Destinations: Claude, ChatGPT, Gemini (app or site), Cursor.
1. From a verified source, choose the destination, press Continue. Expect the app/site to open and the panel to say "Context copied. Paste it into X to continue." (not "ready in").
2. ⌘V in the destination's composer: exact package text appears? **Do not press send.** Confirm nothing was sent.
3. Uninstall/rename the app (or use one that is not installed): expect "unavailable" in the list.
4. Does it open a *new* conversation? (it is not expected to; note what it does.)
5. Composer insertion experiment (only if you want `can_insert=True` later): with the destination focused, try setting AXValue of the composer via Accessibility Inspector. Record whether the text persists after typing a character and whether the app's send button enables. Browsers/Electron usually fail; do not enable insertion without a regression test.

## 3. Failure paths
* Take the destination offline (Wi-Fi off, web destinations): handoff must still end on the clipboard with the message "Couldn't open…" or a copy confirmation.
* Fill the clipboard with other text before step 2.1, confirm it is replaced only after Continue, never before.
* Open a password manager and a private window and press the hotkey: nothing is read, panel says it is private.

## 4. Timing
Run each pair 20 times and note the runner's `timings_ms` (detect / extract / generate / transfer). Targets: hotkey-to-panel < 150 ms (p95), end-to-end < 5 s (p95, excluding app startup).

## Result table (fill in)
| Source | Right app? | Right chat? | Constraint kept? | Partial shown? | Notes |
|---|---|---|---|---|---|
| Claude desktop | | | | | |
| ChatGPT desktop | | | | | |
| Claude in Chrome | | | | | |
| ChatGPT in Chrome | | | | | |
| Gemini in Chrome | | | | | |
| Cursor | | | | | |

| Destination | Opens? | Clipboard exact? | Not sent? | Notes |
|---|---|---|---|---|
| Claude | | | | |
| ChatGPT | | | | |
| Gemini | | | | |
| Cursor | | | | |
