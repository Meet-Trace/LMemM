"""LMemM - `lmemm.py trail ...`: run the event trail, look at it, pause it, delete it.

    trail start                 run the tracker (Ctrl-C stops)
    trail show [N] [--text]     the last N events (default 40); --text includes what appeared on screen
    trail places [--hours H]    where you were: one line per visit, with how long
    trail status                is it running, permissions, what a read costs
    trail pause | resume
    trail forget (--last MIN | --app NAME | --all)
    trail cover [SECONDS]       compare what accessibility sees of the front window with its pixels
    trail probe [SECONDS]       dump the front app's accessibility tree (for tuning on a new app)

Everything except `start` and `probe` is plain Python and works on any OS.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import config
import trail_ax
from trail_store import TrailStore

USAGE = "usage: lmemm.py trail start|show [N] [--text]|places [--hours H]|status|pause|resume|forget (--last MIN|--app NAME|--all)|probe [SECONDS]|cover [SECONDS]"


def hms(ts):
    return datetime.fromisoformat(ts).astimezone().strftime("%H:%M:%S")


def seconds(ms):
    s = ms / 1000
    return f"{s:.0f}s" if s < 90 else f"{s / 60:.0f}m"


def describe(e):
    k = e["kind"]
    if k == "focus":
        p = dict(e["place"], name=e["place"].get("name") or e["place"].get("key", "?"))
        how = f' [{"+".join(p.get("signals", []))} {p.get("confidence", 0):.2f}{" CONFLICT" if p.get("conflict") else ""}]'
        return f'→ {p["kind"]}: {p["name"]} ({p.get("service", e.get("app"))}){how}'
    if k == "app_switch":
        return f'app: {e["app"]}'
    if k == "text":
        return f'text +{len(e["added"])} -{e["removed"]} ({e["source"]})'
    if k in ("typing", "scroll"):
        return f'{k} x{e["n"]} over {e["ms"]} ms' + (f' in "{e["field"]}"' if e.get("field") else "")
    if k == "click":
        return f'click {e.get("button", "")} {e.get("role") or ""} {("“" + e["target"] + "”") if e.get("target") else ""}'.strip()
    if k == "shortcut":
        return f'shortcut {e["combo"]}'
    if k == "gap":
        return f'gap: {e["reason"]}'
    return k


def show(store, n=40, with_text=False):
    events = store.read(limit=None)
    if not events:
        print("the trail is empty. Run:  python3 lmemm.py trail start")
        return
    for e in events[-n:]:
        print(f'{hms(e["t"])}  {describe(e)}')
        if with_text and e["kind"] == "text":
            for line in e["added"][:8]:
                print(f"            + {line[:100]}")


def places(store, hours=2.0):
    """One line per visit to a place, from the focus events."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    focus = [e for e in store.read(since=since) if e["kind"] == "focus"]
    if not focus:
        print("no places in that time.")
        return
    for a, b in zip(focus, focus[1:] + [None]):
        p = dict(a["place"], name=a["place"].get("name") or a["place"].get("key", "?"))
        end = datetime.fromisoformat(b["t"]) if b else datetime.now(timezone.utc)
        ms = (end - datetime.fromisoformat(a["t"])).total_seconds() * 1000
        print(f'{hms(a["t"])}  {seconds(ms):>5}  {p["kind"]:8}  {p["name"][:48]:48}  {p.get("service", "")}')


def status(paths=None):
    paths = paths or config.paths()
    path = os.path.join(paths.trail_dir, ".status.json")
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        print("not running (no status file).")
        return
    alive = False
    try:
        os.kill(doc["pid"], 0)
        alive = time.time() - doc["updated"] < 60
    except (OSError, KeyError):
        pass
    print(f'{"running" if alive else "not running"}  pid {doc.get("pid")}  paused: {doc.get("paused")}  gap: {doc.get("gap")}')
    print(f'accessibility: {doc.get("accessibility")}  input events: {doc.get("input_tap")}  '
          f'observer notifications: {doc.get("observer_notifications")}')
    print(f'now: {doc.get("place")}')
    print(f'reads {doc.get("reads")} (full {doc.get("full_reads")}, screen {doc.get("ocr_reads")})  events {doc.get("events")}  '
          f'full read ms {doc.get("full_read_ms")}  nodes p50 {doc.get("full_read_nodes_p50")}  '
          f'truncated {doc.get("truncated_reads")}')


def set_pause(on):
    flag = os.path.join(config.paths().trail_dir, ".paused")
    os.makedirs(os.path.dirname(flag), mode=0o700, exist_ok=True)
    if on:
        open(flag, "w").close()
    elif os.path.exists(flag):
        os.remove(flag)
    print("trail paused" if on else "trail resumed")


def forget(store, args):
    if "--all" in args:
        n = store.forget(everything=True)
    elif "--last" in args:
        try:
            n = store.forget(minutes=float(args[args.index("--last") + 1]))
        except (IndexError, ValueError):
            sys.exit(USAGE)
    elif "--app" in args:
        try:
            n = store.forget(app=args[args.index("--app") + 1])
        except IndexError:
            sys.exit(USAGE)
    else:
        sys.exit(USAGE)
    print(f"removed {n} event(s)")


def dump_tree(window, max_nodes=500, max_depth=18, width=60):
    """The accessibility tree as indented text, for tuning a new app's rules. Typed text and
    password fields are not shown; other values are cut to `width` characters. Empty wrapper
    groups are left out, and a browser's toolbars and tab strip are named but not opened, so the
    page itself is what fills the file."""
    out, stack, n = [], [(window, 0, 0)], 0
    while stack and n < max_nodes:
        node, depth, shown = stack.pop()
        n += 1
        a = node.attrs()
        role, sub = a.get("AXRole"), a.get("AXSubrole")
        parts = [role or "?"] + ([sub] if sub else [])
        hide = sub == trail_ax.SECURE or role in trail_ax.TEXT_FIELDS and role != "AXSearchField"
        said = False
        for key, tag in (("AXTitle", "title"), ("AXDescription", "desc"), ("AXPlaceholderValue", "placeholder"),
                         ("AXURL", "url")) + (() if hide else (("AXValue", "value"),)):
            v = a.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(f'{tag}="{" ".join(v.split())[:width]}"')
                said = True
        if a.get("AXSelected") is True:
            parts.append("SELECTED")
            said = True
        skipped = role in trail_ax.SKIP_ROLES
        if said or role not in (None, "AXGroup", "AXScrollArea") or skipped:
            out.append("  " * shown + " ".join(parts) + (" (not opened)" if skipped else ""))
            shown += 1
        if depth < max_depth and not skipped:
            stack.extend((c, depth + 1, shown) for c in reversed(node.children()[:trail_ax.MAX_CHILDREN]))
    return "\n".join(out)


def probe(delay=3.0):
    import trail_mac
    print(f"switch to the app you want to inspect; reading in {delay:.0f} s…")
    time.sleep(delay)
    info = trail_mac.front()
    reader = trail_mac.AXReader()
    if not reader.trusted():
        sys.exit("Accessibility is off for this app.")
    el = reader.app_element(info["pid"], info["bundle_id"])
    if reader.warming(info["pid"]):
        print("this app just switched its accessibility tree on; waiting 3 s for it to fill in…")
        time.sleep(3)
    win = trail_ax.element_attr(el, "AXFocusedWindow") or trail_ax.element_attr(el, "AXMainWindow")
    if win is None:
        sys.exit(f"{info['app']} exposes no window to accessibility.")
    snap = trail_ax.collect(trail_ax.LiveNode(win), max_nodes=trail_ax.MAX_NODES)
    import trail_place
    p = trail_place.place(info["app"], info["bundle_id"], snap)
    path = os.path.join(config.paths().trail_dir, f'probe-{info["bundle_id"]}.txt')
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f'{info}\nplace: {json.dumps(p, ensure_ascii=False)}\nread: {snap.nodes} nodes, {snap.ms} ms, '
                 f'truncated={snap.truncated}\n\n{dump_tree(trail_ax.LiveNode(win))}\n')
    reader.restore()
    print(f"saved {path}\nplace read as: {p['kind']} / {p['name']} (confidence {p['confidence']})\n"
          f"The file has on-screen text: look it over before sharing it.")


def cover(delay=3.0):
    """What the screen reader sees of the window in front, against its pixels (macOS)."""
    import collections
    import macos
    import trail_cover
    import trail_mac
    print(f"switch to the window you want to check; reading in {delay:.0f} s…")
    time.sleep(delay)
    info = macos.front()                      # the screenshot tracker's own view of the front window (has its bounds)
    trail = trail_mac.Trail()
    for _ in range(3 if trail.reader.warming(info["pid"]) else 1):
        trail.reader.app_element(info["pid"], info["bundle_id"])
        time.sleep(1.5)
    display_index, _frame = macos.display_for(info["bounds"])
    display = macos.display_bounds(display_index)
    shot = macos.grab(display_index)
    snap = None
    for attempt in range(6):                  # a browser builds its page tree a while after being asked to
        trail.reader.enabled_at.pop(info["pid"], None)       # read_now refuses while "warming"; here we wait ourselves
        snap = trail.read_now(info)
        print(f"  read {attempt + 1}: {snap.nodes if snap else '-'} nodes, {len(snap.items) if snap else '-'} texts")
        if snap is not None and snap.items:
            break
        time.sleep(1.5)
    if shot is None or snap is None:
        sys.exit(f"could not read: screenshot={'ok' if shot else 'none (Screen Recording?)'} tree={'ok' if snap else 'none'}")
    scale = shot.width / display["Width"]
    v = trail_cover.judge(snap, shot.gray, display, scale)
    kinds = collections.Counter(k for _r, k in snap.regions)
    print(f"app: {info['app']}   window bounds (CG): {info['bounds']}")
    print(f"tree window rect: {snap.window}   display: {display}   frame: {shot.width}x{shot.height}   scale: {scale:.3f}")
    print(f"nodes: {snap.nodes}  truncated: {snap.truncated}  texts: {len(snap.items)}  regions: {dict(kinds)}")
    print(f"verdict: {'READ FROM ACCESSIBILITY' if v['ok'] else 'read the screen'}  ({v['why']}; {v['gap']} of {v['ink']} ink cells unexplained)")
    for text, rect in snap.items[:6] + snap.items[-3:]:
        print("  sample:", [round(n) for n in rect], text[:40])
    if snap.items:
        xs = [r[0] for _t, r in snap.items]
        ys = [r[1] for _t, r in snap.items]
        print(f"  text boxes span x {round(min(xs))}..{round(max(xs))}  y {round(min(ys))}..{round(max(ys))}")
    print("\n# explained ink   X unexplained ink   + covered, no ink   . empty\n")
    print(trail_cover.ascii_map(snap, shot.gray, display, scale))
    shot.release()


def main(args):
    cmd = args[0] if args else "show"
    rest = args[1:]
    if cmd == "start":
        import trail_mac
        trail_mac.Trail().run()
    elif cmd == "show":
        nums = [a for a in rest if a.isdigit()]
        show(TrailStore(), int(nums[0]) if nums else 40, "--text" in rest)
    elif cmd == "places":
        hours = float(rest[rest.index("--hours") + 1]) if "--hours" in rest else 2.0
        places(TrailStore(), hours)
    elif cmd == "status":
        status()
    elif cmd in ("pause", "resume"):
        set_pause(cmd == "pause")
    elif cmd == "forget":
        forget(TrailStore(), rest)
    elif cmd == "cover":
        cover(float(rest[0]) if rest else 3.0)
    elif cmd == "probe":
        probe(float(rest[0]) if rest else 3.0)
    else:
        sys.exit(USAGE)
