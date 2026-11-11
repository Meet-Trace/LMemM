# Handoff: repository audit, compatibility matrix, risk report (Phase 1)

Written in a cloud session from reading the code; no Mac was available, so nothing here is a
measured result. "Reusable" means the component can serve the handoff workflow as-is.

## What already existed

| Component | Where | Verdict |
|---|---|---|
| Global hotkey (Carbon `RegisterEventHotKey`, ⌃⌥N) | `src/dictation.py` | **Pattern reused, code not**: it hard-codes one combo and one callback and cannot report a conflict. `handoff/hotkey.py` follows the same approach with a configurable combo, a distinct key id (handler ignores other ids) and conflict reporting. Not yet shared with the note hotkey. |
| Front app + window | `macos.front()`, `macos.browser_info()` | **Reused** (`mac.front_info`). |
| Never-read rules | `config.SKIP_APPS / SKIP_SITES / SKIP_TITLES` | **Reused** (`mac.is_private`), checked before any extraction. |
| Accessibility tree reader | `trail_ax.collect`, `LiveNode` | **Reused** for the visible-text source. Bounded by node and time budgets; never reads password fields. It returns visible text without speaker roles, which is why that source is partial. |
| Window/AX label reader | `ax.py` | Not needed (only names the focused control). |
| Floating panel | `widget.py` | **Not reused**: 1500 lines of pill/card drawing tied to the memory UI. `panel.py` is a small plain AppKit panel; it could adopt the widget's controls once the flow is verified. |
| Key navigation | `keynav.py` | Not reused: zone model for the memory window; `panel_model.py` has 6 keys. |
| Resume brief / context export | `context.py`, `semantic_memory/context_service.py`, PR #26 | **Not reused**: they summarise *what the user did on the Mac* from memory, not a specific conversation. The handoff builds from the conversation in front, as the spec requires. |
| Hybrid memory service, MCP server | `semantic_memory/` | **Deliberately not used.** No database, no permanent store. |
| Test infrastructure | `tests/` (unittest, flat) | Reused (`scripts/test.sh`); new tests use fakes only. Existing suite has 29 errors + 1 failure on Linux (pyobjc/clang) both before and after this change. |

## Compatibility matrix

| App | As source | As destination | Evidence |
|---|---|---|---|
| Claude desktop | **not readable (observed on a Mac, 2026-10-15)**: with `AXManualAccessibility` on, the window shows only the shell page (`file:///Applications/Claude.app/.../app.asar/`, 72 elements, no text); the chat view is not in the tree. Use *Copied text* | open app + clipboard: **unverified** | none yet |
| ChatGPT desktop | visible text via AX: **unverified** | open app + clipboard: **unverified** | none yet |
| claude.ai / chatgpt.com / gemini.google.com in Chrome | URL match + AX: **unverified**; Chromium needs `AXManualAccessibility`, `trail_ax` handles it | opens the site: **unverified** | none yet |
| Cursor | **unverified** (Electron editor; chat panel text may not be in the tree) | open app + clipboard: **unverified** | none yet |
| Any app via copied text | works (pasted; marked unverified content) | n/a | automated tests |
| Composer insertion (any destination) | n/a | **not implemented**: `can_insert=False` | to be evaluated per smoke-test 2.5 |

The previous Mac findings in the repo's history are directly relevant: Gmail's tree was cut off by a 90 ms
budget and VS Code's editor text is not in the AX tree (PR #12 notes). Expect Electron AI apps to behave
similarly until measured.

## Technical risk report

1. **Reading a full conversation is not guaranteed** (high). AX exposes only rendered rows (virtualised lists), with no roles. Mitigation in the MVP: always `partial`, disclosed in the panel and the package, no decision is ever derived from unattributed text. If measurement shows it is unreliable, the supported flow narrows to *Copied text* (selection or whole conversation via the app's own copy), which is still a one-keypress improvement over rewriting context.
2. **Composer insertion** (high). Not attempted; clipboard handoff is the shipped path. Attempting it needs a per-app regression test and must verify the text landed before saying so (`insert()` returns True only on verification).
3. **Prompt injection through captured text** (medium). The package frames content as history; the generator never executes anything; LMemM has no tool-calling. A model-backed generator would need the same framing and must not follow instructions in the conversation.
4. **Secrets in conversations** (medium). Pattern-based redaction (keys, tokens, JWT, private keys, `password=`, Luhn cards) runs before rendering. It cannot catch free-form secrets; the Review view exists for that.
5. **Rule generator quality on real chats** (medium). Evaluated on 55 synthetic conversations only; the thresholds there are necessary, not sufficient. Needs the 30-handoff human review before release.
6. **Hotkey collisions / shared Carbon handler** (low-medium). Distinct key id + handler filter; conflict reported. Untested on a Mac with both hotkeys active.
7. **Focus** (medium). The panel is a non-activating panel that becomes key. The front app is read before it appears, but typing into the source app while the panel is open is unverified.
8. **Provider UI changes** (ongoing). Matching is by bundle id / host only, and failures are visible (`partial`/`empty`) instead of silently wrong.

## Exit condition for Phase 1

"Demonstrate a real source-to-destination handoff on a Mac for at least one pair" is **not met** (no Mac in the
cloud session). The smoke-test procedure in `handoff-smoke-tests.md` is the way to meet it; the first thing to try
is Claude in Chrome (or the desktop app) → Cursor/ChatGPT via clipboard.
