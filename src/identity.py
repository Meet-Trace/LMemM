"""LMemM - which memory item a screen belongs to: the same thing, or a new one.

One rule for every app. The ref (understand.py) names WHERE you are: a URL, a window,
a draft slot. A verified source id (a Google Doc's id) is authoritative. Otherwise
what you TYPED there tells instances apart:

  - same place, and your earlier text is still on screen      -> the same thing
  - same place, your text vanished while you stayed on it (no scroll), or you come
    back and the spot you typed into is empty                -> a NEW thing that just
    looks the same (a second email, a new note, a fresh prompt)
  - conversations (anything that received text from others) never split
  - new place reached without navigating, while what you typed is still on screen,
    or the old place only had a placeholder name ("new", "untitled")
                                                             -> the same thing, whose
    URL/id/title changed (a draft got saved, a doc got a name)
  - anything else is a different thing: two chats in one WhatsApp tab are two
"""

import hashlib
import re
from difflib import SequenceMatcher

import activity
import understand


def make_id(st, ref):
    return f'{st["kind"]}-{hashlib.sha1(ref.encode()).hexdigest()[:8]}'


def authored(item):
    """Did you type anything into this thing?"""
    return bool(item.get("activity", {}).get("typing", {}).get("text"))


def still_there(item, on_screen):
    """Is a real piece of what you typed into it still visible (exactly, re-read with
    OCR noise, or grown since)? Short fragments don't count: "magick" also matches a
    bookmark called "MagickWorld"."""
    typed = [t.lower() for t in item["activity"]["typing"]["text"][-6:]
             if len(t) >= 8 and activity.is_content(t)]
    screen = [l.lower() for l in on_screen if len(l) >= 8]
    return any(l.startswith(t) or SequenceMatcher(None, t, l).ratio() > 0.85
               for t in typed for l in screen)


def provisional(item):
    """A placeholder name the app gives something before it's saved or named."""
    return bool(re.search(r"(^|[:|/])(new|untitled)\b|\|$", item.get("ref") or "", re.I))


def conversation(item):
    """Anything that has received text from others is a stream (a chat, a thread)."""
    r = item["activity"]["receiving"]
    return bool(r["seconds"] or r.get("count"))


def is_empty_where_typed(item, res):
    """Coming back from elsewhere: is the spot you typed into (nearly) empty again?"""
    box = item.get("typing_area")
    if not box:
        return True
    x, y, w, h = box
    inside = [o for o in res["objects"] if o["text"] and len(re.findall(r"[A-Za-z]", o["text"])) >= 3
              and x - 10 <= o["box"][0] <= x + w + 10 and y - 10 <= o["box"][1] <= y + h + 10]
    return len(inside) <= 1


def resolve_item(items, st, cur, res, trigger, scrolled):
    """-> (item id, ref). `cur` is the open timeline event (or None). May re-key the
    current item in place when its place got a new name without you navigating."""
    ref = understand.ref(st)
    on_screen = [o["text"] for o in res["objects"]
                 if o["text"] and o["kind"] not in understand.CHROME]
    cur_item = items.get(cur["item"]) if cur else None

    instances = [i for i in items.values() if ref in i.get("refs", [i.get("ref")])]
    if st.get("stable_identity"):
        # Reuse existing IDs, including legacy items, without merging history.
        if instances:
            return max(instances, key=lambda i: i["last_seen"])["id"], ref
        return make_id(st, ref), ref
    if instances:
        for inst in sorted(instances, key=lambda i: i["last_seen"], reverse=True):
            if authored(inst) and still_there(inst, on_screen):
                return inst["id"], ref
        latest = max(instances, key=lambda i: i["last_seen"])
        if (authored(latest) and not scrolled and not conversation(latest)
                and (trigger == "timer" or is_empty_where_typed(latest, res))):
            return make_id(st, f"{ref}#{len(instances) + 1}"), ref   # your text is gone: a fresh one
        return latest["id"], ref

    if (cur_item and trigger == "timer" and cur_item["app"] == st["app"]
            and cur_item["kind"] == st["kind"]
            and (still_there(cur_item, on_screen)
                 or (provisional(cur_item) and not authored(cur_item)))):
        cur_item.setdefault("refs", [cur_item["ref"]]).append(ref)
        cur_item["ref"] = ref
        return cur_item["id"], ref
    return make_id(st, ref), ref


EMPTY_SCREEN = {"objects": [], "image_size": {"w": 1, "h": 1}}


def quick_state(meta, res=None):
    """What a screen with this metadata is, or None when that can't be told without
    reading the pixels. Without `res` only the URL, tab/window title and app are used,
    which is enough for pages, docs, AI chats, editors, terminals and most chat apps."""
    try:
        st = understand.describe(res or EMPTY_SCREEN, meta)
    except Exception:
        return None
    return st if st.get("target") else None


def _squash(name):
    """A chat name as OCR-proof letters: lowercase, no punctuation, look-alikes merged."""
    s = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return s.translate(str.maketrans("oli", "011"))


def open_notes(item):
    done = item.get("notes_done", {})
    return sum(1 for n in item.get("notes", []) if not done.get(notes_id(n)))


def notes_id(n):
    import notes
    return notes.note_id(n)


def find_chat(items, app, name, threshold=0.85):
    """The chat item for a name read quickly off the screen. Quick OCR misspells names
    ("Sehen Dey 180DC26" / "1800C26"), and each spelling became its own item, so match by
    similarity and prefer the spelling that holds open notes."""
    want = _squash(name)
    if len(want) < 2:
        return None
    close = []
    for i in items.values():
        if i.get("app") != app or i.get("kind") != "chat":
            continue
        have = _squash(i.get("title") or "")
        if have and (have == want or SequenceMatcher(None, have, want).ratio() >= threshold):
            close.append(i)
    if not close:
        return None
    return max(close, key=lambda i: (open_notes(i), i["last_seen"]))["id"]


def find_item(items, st):
    """Id of the most recent item at the place `st` names, or None if you've never been there."""
    if st.get("kind") == "chat" and st.get("target"):
        fuzzy = find_chat(items, st["app"], st["target"])
        if fuzzy:
            return fuzzy
    ref = understand.ref(st)
    seen = [i for i in items.values() if ref in i.get("refs", [i.get("ref")])]
    # Several items can share a ref (a fresh one starts when typed text vanishes). The one
    # holding open notes wins, so a newer empty twin never hides a note from the pill.
    return max(seen, key=lambda i: (open_notes(i) > 0, i["last_seen"]))["id"] if seen else None


def places(item):
    return item.setdefault("places", [])


def learn_place(item, key):
    """Remember that this signature (see ax.key) was seen while this item was in front."""
    if key and key not in places(item):
        places(item).append(key)
        del item["places"][:-8]


def find_place(items, key, label=""):
    """The item a signature names, or None. Exact for every place seen before. A spelling
    twin (OCR once read the same chat two ways) resolves to the one with open notes; two
    genuinely different items on one signature means it can't tell, so None."""
    if not key:
        return None
    found = [i for i in items.values() if key in i.get("places", [])]
    if not found and label:                               # never seen: does an item's title end the label?
        tail = _squash(label)
        found = [i for i in items.values() if len(_squash(i.get("title"))) >= 4
                 and tail.endswith(_squash(i["title"]))]
    if not found:
        return None
    names = [_squash(i.get("title")) for i in found]
    if any(SequenceMatcher(None, names[0], n).ratio() < 0.85 for n in names[1:]):
        return None
    return max(found, key=lambda i: (open_notes(i), i["last_seen"]))["id"]


def page_state(app, url, title):
    """What a browser page is, from its URL and window title alone (what the Accessibility API
    gives in a millisecond); the same state a capture would build, so it finds the same item."""
    site = re.sub(r"^https?://", "", url).split("/")[0] or None
    names = {app, "Brave", "Google Chrome", "Microsoft Edge", "Arc", "Safari"}
    tab = re.sub(r"\s[-–—]\s(?:%s)(?:\s.*)?$" % "|".join(map(re.escape, names)), "", title or "")
    return quick_state({"app": app, "bundle_id": "", "window": title, "url": url, "site": site, "tab_title": tab})


def ax_item(items, app, sig, key, is_browser):
    """-> (item id or None, decided). Which item the app's own Accessibility reading names.
    A focused text field gives a place signature, but on a web page that signature is often
    one never seen before (a search box, a comment form); the page's URL must still be
    tried, or the page's notes vanish whenever a field has focus. `decided` is False when
    nothing here names a place, so the caller falls back to the screen read."""
    if not sig:
        return None, False
    found = find_place(items, key, sig["label"]) if key else None
    if found:
        return found, True
    if is_browser and sig["doc"]:
        st = page_state(app, sig["doc"], sig["title"])
        if st:
            return find_item(items, st), True
    return None, False
