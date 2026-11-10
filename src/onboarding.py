"""LMemM - first-run setup, as rules. Plain functions and one small state machine, no UI.

Five screens, in this order, one idea each:

    welcome   what LMemM is, in one line
    you       your first name (required) and what you mostly work on (optional)
    access    Voice notes, Accessibility, Screen Recording: live status, one step each
    try       press ⌃⌥N and see the note card (a rehearsal: nothing is stored)
    done      the pill appears

The ground rules (each one is tested in tests/test_onboarding.py):

    S1  Nothing can trap you. Every screen has Back except the first, and every screen's main
        button works, with one exception: Continue on "you" waits for a name.
    S2  Only a name is required, and the name, roles and apps can only change on their own screen. Roles, apps and every permission can be skipped and fixed later.
    S3  A permission's state is read from macOS, never remembered: granted, off, denied (voice
        only: macOS refused and will not ask again) or restart (Screen Recording was turned on
        but this process cannot see it until LMemM restarts). The row follows the state at once.
    S4  Screen Recording is last on its screen: the restart it may need comes after the others
        are done. A restart resumes on the screen you were on, with your answers.
    S5  "Only some apps" is a list of bundle ids (apps.py). An empty list means every app.
    S6  The setup record is written once, when you press Done (or "Not now" while a restart is
        pending): the "user" block of memory.json. Before that, only onboarding.json exists.
    S7  Setup runs once. A finished setup is never shown again by itself.
    S8  Everything on screen comes from view(): the window draws it and forwards presses; it
        decides nothing.

Plain Python with no PyObjC. The window is onboarding_ui.py; the macOS side is permissions.py.
"""

import json
import os
import unicodedata
from datetime import date

import apps

VERSION = 1
STEPS = ("welcome", "you", "access", "try", "done")
ROLES = ("Code", "Writing", "Research", "Design", "Studying", "Business")
NAME_MAX = 40
HOTKEY_KEYS = ("⌃", "⌥", "N")

# a permission's state
GRANTED, OFF, DENIED, RESTART = "granted", "off", "denied", "restart"
PERMS = ("voice", "ax", "screen")                     # S4: the order on screen

TRUST = "Stays on this Mac. Never uploaded. Delete it any time."
WELCOME_TRUST = "Everything stays on this Mac. Nothing goes to the cloud."
NAME_TRUST = "Saved on this Mac only. Delete it any time."
SAVED_LINES = ("Your first name", "What you mostly work on, if you pick any",
               "The apps you choose, if you limit Screen Recording", "Your language and time zone, from this Mac")


# ---------------------------------------------------------------- the name

def sanitize_name(raw):
    """A name as it will be stored: control characters removed, runs of white space made one
    space, trimmed, at most NAME_MAX characters. Not a string gives ''."""
    if not isinstance(raw, str):
        return ""
    kept = "".join(" " if ch.isspace() else ch for ch in raw if unicodedata.category(ch)[0] != "C" or ch.isspace())
    return " ".join(kept.split())[:NAME_MAX].strip()


def valid_name(name):
    """A name needs at least one letter or digit in it ("..." is not a name)."""
    clean = sanitize_name(name)
    return bool(clean) and any(ch.isalnum() for ch in clean)


# ---------------------------------------------------------------- permission states (S3)

def _status(value):
    """macOS authorization words, from the speech helper, as granted / denied / undetermined."""
    value = str(value or "").lower()
    if value in ("granted", "authorized"):
        return "granted"
    if value in ("denied", "restricted"):
        return "denied"
    return "undetermined"


def perm_state(perm, facts):
    """granted / off / denied / restart for one permission. `facts` is what the system reports:
    {"screen": bool (this process can capture), "screen_fresh": bool (a new process can),
     "ax": bool, "mic": str, "speech": str}. Anything missing counts as off."""
    facts = facts or {}
    if perm == "screen":
        if facts.get("screen"):
            return GRANTED
        return RESTART if facts.get("screen_fresh") else OFF
    if perm == "ax":
        return GRANTED if facts.get("ax") else OFF
    if perm == "voice":
        mic, speech = _status(facts.get("mic")), _status(facts.get("speech"))
        if mic == "granted" and speech == "granted":
            return GRANTED
        return DENIED if "denied" in (mic, speech) else OFF
    raise ValueError(f"unknown permission: {perm}")


def row_action(perm, state):
    """What the row's one button does: allow (macOS asks), settings (open System Settings),
    restart, or None when there is nothing to do."""
    if state == GRANTED:
        return None
    if state == RESTART:
        return "restart"
    if perm == "voice":
        return "settings" if state == DENIED else "allow"
    return "settings"


ROW_TEXT = {
    "voice": ("Voice notes", None, "Turns what you say into notes."),
    "ax": ("Accessibility", "Recommended", "Lets VS Code share what you view with project Memory, and keeps the pill fast."),
    "screen": ("Screen Recording", "Needed", "Knows which window you’re in."),
}
BUTTON = {"allow": "Allow", "settings": "Open System Settings", "restart": "Restart LMemM"}


def row_line(perm, state, watch_apps=()):
    """The one short line under a permission's name."""
    if state == DENIED:
        return "Off. Turn it on in System Settings."
    if state == RESTART:
        return "Turned on. Restart to finish."
    chosen = apps.describe(watch_apps) if perm == "screen" else None
    if chosen:
        return f"{chosen}. Off everywhere else."
    return ROW_TEXT[perm][2]


# ---------------------------------------------------------------- the state

def fresh_state():
    return {"version": VERSION, "step": "welcome", "name": "", "roles": [], "watch_apps": [],
            "rehearsed": False, "completed": False}


def normalize(raw):
    """Any dict from disk into a valid state. Unknown keys are dropped, bad values reset, and
    a step that needs a name sends you back to 'you' when there is none (S1). Never raises."""
    state = fresh_state()
    if not isinstance(raw, dict):
        return state
    state["name"] = sanitize_name(raw.get("name"))
    roles = raw.get("roles")
    state["roles"] = [r for r in ROLES if isinstance(roles, list) and r in roles]
    state["watch_apps"] = apps.clean(raw.get("watch_apps"))
    state["rehearsed"] = raw.get("rehearsed") is True
    state["completed"] = raw.get("completed") is True
    step = raw.get("step")
    state["step"] = step if step in STEPS else "welcome"
    if STEPS.index(state["step"]) > STEPS.index("you") and not valid_name(state["name"]):
        state["step"] = "you"
    return state


def can_continue(state):
    """S1: the only closed door is a missing name."""
    return state["step"] != "you" or valid_name(state["name"])


def continue_style(state, facts):
    """On the access screen the button is solid once Screen Recording is on (or about to be)
    or the person limited it to some apps; quiet otherwise. Never a different word."""
    screen = perm_state("screen", facts)
    return "primary" if screen in (GRANTED, RESTART) or state["watch_apps"] else "quiet"


def user_record(state, today=None, language=None, time_zone=None):
    """The "user" block for memory.json."""
    today = today or date.today()
    record = {"name": sanitize_name(state["name"])}
    if state["roles"]:
        record["works_on"] = [r.lower() for r in ROLES if r in state["roles"]]
    chosen = apps.clean(state["watch_apps"])
    if chosen:
        record["watch_apps"] = chosen
    for key, value in (("language", language), ("time_zone", time_zone)):
        if isinstance(value, str) and 0 < len(value.strip()) <= 40:
            record[key] = value.strip()
    record["set_up"] = today.isoformat()
    record["onboarding"] = VERSION
    return record


def needs_setup(state):
    """S7: setup is shown until it has been finished once."""
    return not state["completed"]


# ---------------------------------------------------------------- persistence

def load_state(path):
    try:
        with open(path) as fh:
            return normalize(json.load(fh))
    except (OSError, ValueError):
        return fresh_state()


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        json.dump(state, fh, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


# ---------------------------------------------------------------- the machine

class Setup:
    """First-run setup. `system` is permissions.MacSystem on a Mac, a fake in tests:

        facts() -> dict           what macOS reports now (cheap; never prompts)
        ask_voice()               have macOS ask for Microphone and Speech Recognition
        open_settings(perm)       open that permission's pane in System Settings
        relaunch()                start LMemM again (it resumes where this left off)
        language() / time_zone()
        save_user(record)         write the "user" block of memory.json

    Every press returns True when something changed. Every change is written to
    onboarding.json before the call returns, so a quit, a crash or a restart resumes.
    """

    def __init__(self, system, path, today=None):
        self.system, self.path, self.today = system, path, today
        self.state = load_state(path)
        self.facts = {}
        self.error = None                                  # a calm one-line message, or None
        self.refresh()

    # -- reading the world (never prompts, never raises)

    def _safe(self, fn, default):
        try:
            return fn()
        except Exception:
            return default

    def refresh(self):
        """Take the system's current facts. Returns True when the view could look different."""
        facts = self._safe(self.system.facts, None)
        if not isinstance(facts, dict):
            return False
        changed = facts != self.facts
        self.facts = facts
        return changed

    def perm(self, perm):
        return perm_state(perm, self.facts)

    # -- state

    @property
    def step(self):
        return self.state["step"]

    @property
    def completed(self):
        return self.state["completed"]

    def _commit(self):
        try:
            save_state(self.path, self.state)
            self.error = None
        except OSError:
            self.error = "Couldn’t save your progress. Setup still works."

    def _go(self, step):
        self.state["step"] = step
        self._commit()
        return True

    # -- presses

    def next(self):
        if self.completed or not can_continue(self.state):
            return False
        i = STEPS.index(self.step)
        return self._go(STEPS[i + 1]) if i < len(STEPS) - 1 else False

    def back(self):
        i = STEPS.index(self.step)
        return self._go(STEPS[i - 1]) if i > 0 and not self.completed else False

    def set_name(self, raw):
        if self.step != "you" or self.completed:
            return False
        name = sanitize_name(raw)
        if name == self.state["name"]:
            return False
        self.state["name"] = name
        self._commit()
        return True

    def toggle_role(self, role):
        if role not in ROLES or self.step != "you" or self.completed:
            return False
        roles = set(self.state["roles"])
        roles.symmetric_difference_update({role})
        self.state["roles"] = [r for r in ROLES if r in roles]
        self._commit()
        return True

    def set_apps(self, entries):
        """S5: the apps Screen Recording is limited to; an empty list is every app."""
        chosen = apps.clean(entries)
        if self.step != "access" or self.completed or chosen == self.state["watch_apps"]:
            return False
        self.state["watch_apps"] = chosen
        self._commit()
        return True

    def press(self, action, perm=None):
        """A row's button. Only the action the row currently offers does anything."""
        if self.completed or (self.step != "access" and action != "restart"):
            return False
        if action == "refresh":
            return self._attempt(self.system.request_refresh, "Couldn’t check permissions. Restart LMemM and try again.")
        if action == "retry_restart":
            if self.step != "access" or self.perm("screen") == GRANTED:
                return False
            self._commit()
            return self._attempt(self.system.relaunch, "Couldn’t restart LMemM. Quit it and open it again.")
        if action == "restart":
            if self.step not in ("access", "try", "done") or self.perm("screen") != RESTART:
                return False
            self._commit()                                   # the resume point is on disk first
            return self._attempt(self.system.relaunch, "Couldn’t restart LMemM. Quit it and open it again.")
        if perm not in PERMS or row_action(perm, self.perm(perm)) != action:
            return False
        if action == "allow":
            return self._attempt(self.system.ask_voice, "Couldn’t ask macOS. Open System Settings instead.")
        return self._attempt(lambda: self.system.open_settings(perm), "Couldn’t open System Settings.")

    def _attempt(self, fn, message):
        try:
            fn()
            self.error = None
        except Exception:
            self.error = message
        return True

    def rehearsed(self):
        """The rehearsal note was saved on the 'try' screen."""
        if self.step != "try" or self.state["rehearsed"]:
            return False
        self.state["rehearsed"] = True
        self._commit()
        return True

    def finish(self):
        """S6: the last press. Not available while a restart is pending unless the person
        chose 'Not now', which is the same call."""
        if self.step != "done" or self.completed or not valid_name(self.state["name"]):
            return False
        record = user_record(self.state, self.today, self._safe(self.system.language, None),
                             self._safe(self.system.time_zone, None))
        try:
            self.system.save_user(record)
        except Exception:
            self.error = "Couldn’t save your details. Try Done again."
            return True
        self.state["completed"] = True
        self._commit()
        return True

    # -- what the window draws (S8)

    def view(self):
        step, state = self.step, self.state
        v = {"step": step, "index": STEPS.index(step), "of": len(STEPS), "back": STEPS.index(step) > 0,
             "title": "", "sub": None, "rows": [], "roles": [], "name": None, "saved": [], "keys": [],
             "trust": None, "notes": [], "primary": None, "secondary": None, "error": self.error}
        if step == "welcome":
            v.update(title="A second memory for what you’re working on.",
                     sub="LMemM remembers where you were and what you meant to do next, then reminds you when you come back.",
                     trust=WELCOME_TRUST, primary=self._btn("Get started", "next"))
        elif step == "you":
            ok = valid_name(state["name"])
            v.update(title="What should we call you?", trust=NAME_TRUST, saved=list(SAVED_LINES),
                     name={"value": state["name"], "placeholder": "First name",
                           "hint": "Just a first name is fine."},
                     roles=[{"name": r, "on": r in state["roles"]} for r in ROLES],
                     primary=self._btn("Continue", "next", enabled=ok))
        elif step == "access":
            v.update(title="Let LMemM see and hear.", trust=TRUST, rows=self._rows(),
                     can_limit_apps=True, watch_apps=list(state["watch_apps"]),
                     checking=bool(self.facts.get("checking")),
                     can_retry_restart=self.perm("screen") != GRANTED,
                     permission_help=("Couldn’t read every permission. Refresh to try again." if self.facts.get("errors") else
                                      "Already enabled in System Settings? Refresh, then restart if needed."),
                     primary=self._btn("Continue", "next", quiet=continue_style(state, self.facts) == "quiet"))
        elif step == "try":
            done = state["rehearsed"]
            v.update(title="Try it. Press this, then say a thought.", keys=list(HOTKEY_KEYS),
                     sub="Hold Control and Option, tap N. A small card opens. Talk, or type, then press Return.")
            if done:
                v["notes"] = [{"tone": "good", "lead": "That’s a note.",
                               "text": "It will be waiting when you come back to this window, in any app."}]
                v["primary"] = self._btn("Continue", "next")
            else:
                v["primary"] = self._btn("Show me", "show_note", quiet=True)
                v["secondary"] = {"label": "Skip for now", "action": "next"}
        else:
            screen = self.perm("screen")
            name = sanitize_name(state["name"]) or "there"
            v.update(title=f"You’re set, {name}.",
                     sub="LMemM now lives at the bottom of your screen as a small pill. Press ⌃⌥N anywhere to leave a note.")
            if state["watch_apps"]:
                v["notes"].append({"tone": "info", "text": f"LMemM watches only {', '.join(a['name'] for a in state['watch_apps'])}. Everywhere else it is off."})
            if self.perm("ax") != GRANTED:
                v["notes"].append({"tone": "info", "text": "Accessibility is off, so the pill may follow you a little slower, and VS Code cannot share what you view with project Memory."})
            if screen == RESTART:
                v["notes"].append({"tone": "warn", "text": "Screen Recording is waiting for a restart. The pill will show a red mark until then."})
                v["primary"] = self._btn("Restart LMemM", "restart")
                v["secondary"] = {"label": "Not now", "action": "finish"}
            else:
                if screen != GRANTED:
                    v["notes"].append({"tone": "warn", "text": "Screen Recording is still off. The pill will show a red mark until it’s fixed."})
                v["primary"] = self._btn("Done", "finish")
        return v

    def _btn(self, label, action, enabled=True, quiet=False):
        return {"label": label, "action": action, "enabled": bool(enabled), "quiet": bool(quiet)}

    def _rows(self):
        rows = []
        for perm in PERMS:
            state = self.perm(perm)
            title, tag, _ = ROW_TEXT[perm]
            action = row_action(perm, state)
            rows.append({"key": perm, "title": title, "tag": tag, "state": state,
                         "line": row_line(perm, state, self.state["watch_apps"]),
                         "action": action, "button": BUTTON.get(action)})
        return rows

    def press_primary(self):
        """What the main button does right now (the window calls this on a click)."""
        button = self.view()["primary"]
        if not button or not button["enabled"]:
            return False
        action = button["action"]
        if action == "next":
            return self.next()
        if action == "finish":
            return self.finish()
        if action == "restart":
            return self.press("restart")
        return False                                           # show_note is the window's job
