# Universal AI Handoff (MVP)

Move ongoing work from one AI assistant to another without rebuilding the context by hand.
Press a hotkey in the assistant you are using, pick the next one, check the summary if you like,
continue. **LMemM never sends the prompt: you do.**

> **Status: draft, cloud-built, not yet run on a Mac.** The workflow, generator, adapters' contracts
> and view model are covered by 95 automated tests that run anywhere. The AppKit panel, the Carbon
> hotkey and the Accessibility source/destination adapters (`mac.py`, `hotkey.py`, `panel.py`) are
> **unverified** until someone completes [handoff-smoke-tests.md](handoff-smoke-tests.md).
> Nothing in this feature is wired into `lmemm.py start`; it only runs from `lmemm.py handoff`.

## Use

```bash
source .venv/bin/activate
python3 lmemm.py handoff panel [--key ctrl+opt+h]   # real hotkey + panel (macOS)
python3 lmemm.py handoff make transcript.txt --to Cursor   # build a package from "You:"/"Assistant:" text
python3 -m handoff.cli demo      # (PYTHONPATH=src) the whole flow on a synthetic chat, any OS
```

Needs **Accessibility** (to read the window) and nothing else. Default hotkey ⌃⌥H; pass any
combination with a Control, Option or Command key. If another app owns it, the runner says so and exits.

## How it works

`hotkey` → `Coordinator.invoke()` reads the front app on the caller's thread (before LMemM's panel
takes focus) → `capture()` runs the matching **source adapter** on a worker with a timeout →
panel lists **destinations** → `choose()` builds the package with the **generator** →
`transfer()` opens the destination and inserts, or copies. States: loading → source → confirm →
(review) → result. One session at a time, so a second hotkey press reuses the open panel.

| Piece | File | Notes |
|---|---|---|
| Data model | `src/handoff/model.py` | `Conversation.status`: complete / partial / empty / denied / unsupported / failed |
| Generator | `generator.py` | `RuleGenerator`: deterministic, local, no model call. `Generator` is the replaceable interface; a model-backed one must set `external = True` so the UI discloses it |
| Redaction | `redact.py` | API keys, tokens, JWTs, private keys, `password=`, Luhn-valid card numbers |
| Adapters | `adapters.py` | Source/destination contracts, clipboard source, fakes. **Destinations have no send method** |
| Workflow | `coordinator.py` | state machine, timeouts, fallbacks, per-stage timings, metadata-only records |
| View model | `panel_model.py` | what every state shows and what every key does (tested) |
| macOS | `mac.py`, `hotkey.py`, `panel.py` | **unverified**; thin, no logic the tests don't cover |

Generation rules (all tested): constraints are the user's own "must / never / can't / only…" lines;
a decision exists only if the user decided it or agreed to the assistant's proposal; assistant
"maybe / could / perhaps" lines are listed under *Unresolved* as unconfirmed; a later decision
that replaces an earlier one (`instead of`, `switch from`) moves the earlier one to *Ruled out*;
older items about other topics are dropped and counted; every line cites its source message
(`[msg 3]`); secrets are removed; text inside the conversation is framed as history, never as commands.

## Privacy

Local by default: the rule generator makes no network calls. The never-read rules of the memory
tracker apply (password managers, banking sites, private windows). Nothing is stored: the
session lives in memory until the panel closes, and the only thing logged is a `Record` of
stage names, timings and outcome (a test proves no message text reaches it). Nothing reaches a
destination without the user pressing Continue.

## Tests

```bash
./scripts/test.sh                       # everything
PYTHONPATH=src:tests python3 -m unittest discover -s tests -p "test_handoff*.py"   # just this feature
python3 tests/handoff_fixtures/build_eval_set.py --check   # evaluation set matches its builder
```

No test uses a real account, model, clipboard or window. Layers: unit (`test_handoff_generator`,
`_redact`), contract (`_contract`: source, destination, hotkey, privacy gate), integration and
end-to-end (`_coordinator`: exact text reaches the fake composer, nothing sent, failures keep the
handoff, cancel transfers nothing), UI (`_panel`: view model + keys), quality (`_eval`: 55 synthetic
conversations in 9 categories, versioned in `tests/handoff_fixtures/`).

### Acceptance criteria

| ID | Where | | ID | Where |
|---|---|---|---|---|
| AC-01 | `_panel` hotkey/loading + `_contract` parse | | AC-09 | `_coordinator` failed destination |
| AC-02 | `_contract` source contract | | AC-10 | `_generator`, `_panel`, `_coordinator` partial |
| AC-03 | `_generator` synthetic conversation | | AC-11 | `_coordinator`, `_panel` cancel/Escape |
| AC-04 | `_generator`, `_eval` constraint recall | | AC-12 | all fakes; no network or model imports |
| AC-05 | `_coordinator`, `_panel` unsupported source | | AC-13 | **open**: Mac smoke tests |
| AC-06 | `_panel` keyboard-only flow | | AC-14 | `_coordinator` record has no text |
| AC-07 | `_coordinator` end-to-end | | AC-15 | existing suite: same results as `main` |
| AC-08 | `_coordinator`, `_contract` (no send API) | | | |

Not covered by automation here: real AppKit rendering, display scaling and placement, permission
prompts, and the performance targets for hotkey-to-panel (p95 < 150 ms) and end-to-end transfer
(p95 < 5 s). `Record.timings_ms` has the per-stage numbers for measuring them on a Mac.
Generation from a 600-message conversation is covered by a < 2 s test.

## Known limitations

* **Source reading is the weak point.** The Mac source reads only the *visible* text of the window
  and cannot tell who said what, so it always reports `partial` with unattributed text; the package
  then says so and nothing from that text becomes a decision. Whether it works at all per app
  (Claude, ChatGPT, Gemini in a browser, Cursor) is unverified.
* **Destinations are clipboard-only.** `can_insert` is `False` for every Mac destination: composer
  insertion through Accessibility is not trusted until it is shown to work (see smoke tests).
  The UI says "Copies to the clipboard; you paste and send" and never says "inserted" for it.
* The fallback for any app is **Copied text**: copy the conversation (⌘A, ⌘C), press the hotkey, then
  press **C** or the "Use copied text" button on the panel. The Claude desktop app needs this: its chat
  is not exposed to Accessibility (see the audit).
* `python3 lmemm.py handoff probe 4 [--tree]` prints what Accessibility exposes for the app in front.
* Rule-based summaries are conservative and English-only; they quote lines instead of rewriting them.
  A model-backed `Generator` is the intended upgrade and would need disclosure in the UI.
* Hotkey default ⌃⌥H is not stored in settings yet; use `--key`.

## Release checklist

- [ ] All of [handoff-smoke-tests.md](handoff-smoke-tests.md) passes for each advertised source/destination pair, outputs recorded.
- [ ] 30 real handoffs reviewed by a human; constraint recall and unsupported-claim counts meet the thresholds in `test_handoff_eval.py` (currently 100% / 0).
- [ ] Hotkey-to-panel p95 < 150 ms and end-to-end p95 < 5 s measured on a Mac.
- [ ] Permission recovery checked (Accessibility revoked mid-session, app restarted).
- [ ] `./scripts/test.sh` matches `main` on a Mac; no new logs contain message text.
- [ ] Only verified pairs are listed in the app and the README.
