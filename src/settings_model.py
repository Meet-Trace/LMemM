"""LMemM - the Settings window, as data and plain functions. No UI, all tested.

Everything you answered in setup, and a few things setup never asked, in one place: You, Apps,
Access, Privacy, General, Data. The window draws view() and forwards presses; apply() is the only
thing that changes a setting.

    G1  A setting takes effect the moment you change it, and says so in one sentence with Undo.
        There is no Save button and no "Apply".
    G2  The same rules as setup: a name needs a letter or digit (onboarding.valid_name); "only some
        apps" is a list of bundle ids and an empty list means every app (apps.py). Taking the last
        app off the list therefore means every app, and the sentence says so.
    G3  Access is read from macOS every time it is drawn, never remembered (onboarding S3), and has
        one button per row, the same ones setup has. Red is only for a permission that is off.
    G4  Privacy has two parts. What LMemM never reads is shown in words and cannot be turned off
        (password managers, banks and sign-in pages, private windows, password fields). Apps you add
        to "Never remember" are skipped before anything is captured, read or remembered.
    G5  Screenshots are kept 1, 3, 7 or 30 days (retention.py); the words say what the choice
        deletes and what it does not (the text and your notes stay).
    G6  Data shows where memory lives and how big it is, opens the file, and deletes everything the
        way the menu does (it asks, says what goes, and restarts). Nothing here deletes quietly.
    G7  A bad value is a sentence and changes nothing (the user block is only replaced whole).
"""

import copy

import apps
import backdrop
import onboarding

SECTIONS = ("you", "apps", "access", "privacy", "general", "data")
TITLES = {"you": "You", "apps": "Apps", "access": "Access", "privacy": "Privacy", "general": "General", "data": "Data"}
KEEP_CHOICES = (1, 3, 7, 30)
KEEP_DEFAULT = 7
ACTIONS = ("set_name", "toggle_role", "watch_add", "watch_remove", "watch_all", "skip_add", "skip_remove", "keep_days", "backdrop")

ALWAYS_PRIVATE = ("Password managers", "Banks, payments and sign-in pages", "Private and incognito windows", "Password fields")
KEEP_WORDS = {1: "1 day", 3: "3 days", 7: "7 days", 30: "30 days"}


def keep_days(user):
    days = (user or {}).get("screenshot_days")
    return days if days in KEEP_CHOICES else KEEP_DEFAULT


def roles_on(user):
    on = (user or {}).get("works_on")
    return [r for r in onboarding.ROLES if r.lower() in (on if isinstance(on, list) else [])]


# ---------------------------------------------------------------- changing a setting (G1, G2, G7)

def apply(user, action, value=None):
    """Change one setting. Returns {"user": the new user block, "message": one sentence}. `user` is
    not touched. ValueError says, in words, why not (G7)."""
    new = copy.deepcopy(user) if isinstance(user, dict) else {}
    if action == "set_name":
        if not onboarding.valid_name(value):
            raise ValueError("Type your first name. It needs at least one letter.")
        new["name"] = onboarding.sanitize_name(value)
        return {"user": new, "message": f"Saved “{new['name']}”"}
    if action == "toggle_role":
        if value not in onboarding.ROLES:
            raise ValueError("That is not one of the choices.")
        mine = {r for r in roles_on(new)} ^ {value}
        picked = [r.lower() for r in onboarding.ROLES if r in mine]
        if picked:
            new["works_on"] = picked
        else:
            new.pop("works_on", None)
        return {"user": new, "message": f"{value} {'added' if value in mine else 'removed'}"}
    if action in ("watch_add", "skip_add", "watch_remove", "skip_remove", "watch_all"):
        key = "watch_apps" if action.startswith("watch") else "skip_apps"
        current = apps.clean(new.get(key))
        if action.endswith("_all"):
            current, message = [], "Watching every app"
        elif action.endswith("_add"):
            entry = apps.clean([value])
            if not entry:
                raise ValueError("That app can't be added.")
            if entry[0]["id"] in {e["id"] for e in current}:
                raise ValueError(f"{entry[0]['name']} is already on the list.")
            current = apps.clean(current + entry)
            message = (f"Watching only {apps.describe(current, 3)[5:]}" if key == "watch_apps"
                       else f"Never remembering {entry[0]['name']}")
        else:
            gone = [e for e in current if e["id"] == (value.get("id") if isinstance(value, dict) else value)]
            if not gone:
                raise ValueError("That app is not on the list.")
            current = [e for e in current if e not in gone]
            if key == "watch_apps":
                message = f"Watching only {apps.describe(current, 3)[5:]}" if current else "Watching every app"
            else:
                message = f"{gone[0]['name']} can be remembered again"
        if current:
            new[key] = current
        else:
            new.pop(key, None)
        return {"user": new, "message": message}
    if action == "keep_days":
        if value not in KEEP_CHOICES:
            raise ValueError("Pick 1, 3, 7 or 30 days.")
        new["screenshot_days"] = value
        return {"user": new, "message": f"Screenshots are kept {KEEP_WORDS[value]}"}
    if action == "backdrop":
        if value != backdrop.AUTO and value not in backdrop.KEYS:
            raise ValueError("Pick Automatic, Dawn, Day, Evening or Night.")
        if value == backdrop.AUTO:
            new.pop("backdrop", None)
            return {"user": new, "message": "Background follows the time of day"}
        new["backdrop"] = value
        return {"user": new, "message": f"Background stays {backdrop.entry(value)['label'].lower()}"}
    raise ValueError("That is not a setting.")


# ---------------------------------------------------------------- what the window draws

def access_rows(facts, watch_apps=()):
    """The three permissions as setup shows them (G3): title, tag, state, line, button."""
    rows = []
    for perm in onboarding.PERMS:
        state = onboarding.perm_state(perm, facts)
        title, tag, _ = onboarding.ROW_TEXT[perm]
        action = onboarding.row_action(perm, state)
        rows.append({"key": perm, "title": title, "tag": tag, "state": state,
                     "line": onboarding.row_line(perm, state, watch_apps), "action": action,
                     "button": onboarding.BUTTON.get(action)})
    return rows


def view(user, section, facts=None, data=None, hotkey="⌃⌥N"):
    """The Settings page. `facts`: what the system says about permissions. `data`: forget.plan()
    ({"data_dir", "files", "bytes"}) or None when it cannot be read."""
    user = user if isinstance(user, dict) else {}
    section = section if section in SECTIONS else SECTIONS[0]
    watch, skip = apps.clean(user.get("watch_apps")), apps.clean(user.get("skip_apps"))
    page = {"kind": "settings", "title": "Settings", "section": section, "back": True,
            "tabs": [{"id": s, "title": TITLES[s], "on": s == section} for s in SECTIONS]}
    if section == "you":
        on = roles_on(user)
        page["you"] = {"name": user.get("name") or "", "roles": [{"role": r, "on": r in on} for r in onboarding.ROLES],
                       "line": onboarding.NAME_TRUST}
    elif section == "apps":
        page["apps"] = {"mode": "some" if watch else "all", "chosen": watch,
                        "line": (f"{apps.describe(watch, 3)}. Every other app is skipped before anything is read."
                                 if watch else "Every app. Add some to watch only those.")}
    elif section == "access":
        page["access"] = {"rows": access_rows(facts, watch)}
    elif section == "privacy":
        page["privacy"] = {"always": list(ALWAYS_PRIVATE), "never": skip,
                           "line": "Apps you add here are skipped before anything is captured, read or remembered."}
    elif section == "general":
        days = keep_days(user)
        page["general"] = {"keep": [{"days": d, "label": KEEP_WORDS[d], "on": d == days} for d in KEEP_CHOICES],
                           "keep_line": f"Each thing keeps one small picture for {KEEP_WORDS[days]}. Then the picture goes; "
                                        "the words, activity and your notes stay.",
                           "backdrop": {"rows": backdrop.choices(user.get("backdrop")),
                                        "line": "Follows your clock by default and changes slowly. Pick one to keep it."},
                           "hotkey": hotkey,
                           "memory":{"title":"Project Memory","line":"Enable a workspace and manage local search, pause, and agent connections from its Memory tab."}}
    else:
        page["data"] = {"where": data["data_dir"] if data else "", "files": data["files"] if data else 0,
                        "bytes": data["bytes"] if data else 0, "known": bool(data)}
    return page
