import math
import re
from collections import Counter

S = 1_000_000_000
WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
PERSISTENT_SHARE = 0.6
LINE_CAP = 160


def clean_lines(lines):
    seen, out = set(), []
    for raw in lines:
        text = re.sub(r"\s+", " ", str(raw)).strip()
        if len(text) < 4:
            continue
        letters = sum(c.isalpha() for c in text)
        if letters / len(text) < 0.4:
            continue
        symbols = sum(not (c.isalnum() or c.isspace()) for c in text)
        if symbols / len(text) > 0.35:
            continue
        words = WORD.findall(text)
        if not words:
            continue
        if len(words) >= 3 and len({w.lower() for w in words}) / len(words) < 0.5:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text[:LINE_CAP])
    return out


def salience(line):
    words = WORD.findall(line)
    if not words:
        return 0.0
    digits = sum(c.isdigit() for c in line) / len(line)
    caps = sum(w[0].isupper() for w in words) / len(words)
    length = min(len(words), 12) / 12
    return 0.5 * length + 0.3 * caps + 0.2 * (1 - digits)


def interaction_summary(events):
    clicks = Counter()
    keys = Counter()
    scroll_y = scroll_x = scroll_events = scroll_abs = 0.0
    modifiers = Counter()
    moves = []
    for e in events:
        kind, p = e["type"], e.get("payload") or {}
        if kind == "click" and p.get("phase") == "down":
            clicks["double" if (p.get("click_count") or 1) >= 2 else p.get("button", "left")] += 1
        elif kind == "keypress" and p.get("phase") == "down" and not p.get("is_repeat"):
            keys[p.get("key_class", "other")] += 1
            for m in p.get("modifiers") or []:
                if m in ("command", "control"):
                    modifiers[m] += 1
        elif kind == "scroll":
            scroll_y += p.get("dy", 0) or 0
            scroll_abs += abs(p.get("dy", 0) or 0)
            scroll_x += p.get("dx", 0) or 0
            scroll_events += 1
        elif kind == "cursor_move":
            moves.append((p.get("x", 0), p.get("y", 0)))
    travel = sum(math.dist(a, b) for a, b in zip(moves, moves[1:]))
    typed = keys["character"] + keys["whitespace"]
    summary = {
        "clicks": dict(clicks), "click_total": sum(clicks.values()), "keys": dict(keys), "typed": typed,
        "deletes": keys["delete"], "returns": keys["return"], "tabs": keys["tab"], "nav_keys": keys["navigation"],
        "shortcuts": sum(modifiers.values()), "scroll_events": int(scroll_events), "scroll_y": round(scroll_y),
        "scroll_x": round(scroll_x), "scroll_abs": round(scroll_abs), "cursor_samples": len(moves), "cursor_travel": round(travel),
    }
    summary["total"] = (summary["click_total"] + sum(keys.values()) + summary["scroll_events"] + (1 if moves else 0))
    summary["pattern"] = pattern_of(summary)
    return summary


def pattern_of(s):
    if s["total"] == 0:
        return "no_interaction"
    if s["typed"] >= 3 or s["deletes"] >= 2:
        return "typing"
    if s["scroll_events"] >= 3 and s["click_total"] <= 1 and s["typed"] == 0:
        return "scrolling"
    if s["nav_keys"] >= 3 and s["typed"] == 0:
        return "keyboard_navigation"
    if s["click_total"] >= 1 and s["scroll_events"] == 0:
        return "pointer_clicks"
    if s["click_total"] == 0 and s["typed"] == 0 and s["scroll_events"] == 0:
        return "cursor_only"
    return "mixed"


def describe_interaction(s):
    if s["total"] == 0:
        return "none observed"
    parts = []
    if s["click_total"]:
        parts.append("clicks " + ", ".join(f"{k} x{v}" for k, v in sorted(s["clicks"].items())))
    if s["typed"] or s["deletes"] or s["returns"] or s["tabs"]:
        bits = [f"{s['typed']} character/space keys"]
        if s["deletes"]:
            bits.append(f"{s['deletes']} deletes")
        if s["returns"]:
            bits.append(f"{s['returns']} returns")
        if s["tabs"]:
            bits.append(f"{s['tabs']} tabs")
        parts.append("keys: " + ", ".join(bits))
    if s["nav_keys"]:
        parts.append(f"{s['nav_keys']} navigation keys (arrows, page, home/end)")
    if s["shortcuts"]:
        parts.append(f"{s['shortcuts']} keys with command/control")
    if s["scroll_events"]:
        steady = abs(s["scroll_y"]) / s["scroll_abs"] if s["scroll_abs"] else 0
        vertical = "mostly one direction" if steady >= 0.7 else "back and forth"
        horizontal = "" if abs(s["scroll_x"]) < 5 else ", some sideways"
        parts.append(f"scroll {s['scroll_events']} events, vertical {vertical}, {s['scroll_abs']} units in total{horizontal}")
    if s["cursor_samples"]:
        parts.append(f"cursor moved about {s['cursor_travel']} px")
    return "; ".join(parts)


def jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def delta(prev, cur):
    changes = []
    if prev["app"] != cur["app"]:
        changes.append(f"application changed from {prev['app']} to {cur['app']}")
    if prev["title"] != cur["title"]:
        changes.append("window title changed")
    if prev.get("url") != cur.get("url") and (prev.get("url") or cur.get("url")):
        changes.append("URL changed")
    a, b = {l.lower() for l in prev["lines"]}, {l.lower() for l in cur["lines"]}
    overlap = jaccard(a, b)
    if overlap < 0.35:
        changes.append("visible content mostly replaced")
    elif overlap < 0.85:
        changes.append(f"visible content partly changed ({len(b - a)} new lines, {len(a - b)} gone)")
    pa, pb = prev["interaction"]["pattern"], cur["interaction"]["pattern"]
    if pa != pb:
        changes.append(f"interaction changed from {pa} to {pb}")
    elif prev["interaction"]["total"] and cur["interaction"]["total"]:
        ratio = cur["interaction"]["total"] / prev["interaction"]["total"]
        if ratio >= 2:
            changes.append("interaction intensity increased")
        elif ratio <= 0.5:
            changes.append("interaction intensity decreased")
    if not changes:
        changes.append("no meaningful change")
    return {"overlap": round(overlap, 2), "changes": changes}


def prepare(raw):
    lines = clean_lines(raw.get("lines") or [])
    out = dict(raw)
    out["lines"] = lines
    out["interaction"] = interaction_summary(raw.get("events") or [])
    return out


def gap_text(seconds):
    if seconds < 90:
        return f"{round(seconds)} s"
    return f"{seconds / 60:.1f} min"


def persistent_lines(items):
    if len(items) < 3:
        return []
    count = Counter()
    for item in items:
        count.update({l.lower() for l in item["lines"]})
    keep = {k for k, v in count.items() if v / len(items) >= PERSISTENT_SHARE}
    first = {}
    for item in items:
        for line in item["lines"]:
            if line.lower() in keep and line.lower() not in first:
                first[line.lower()] = line
    return sorted(first.values(), key=lambda l: -salience(l))


def serialize(observations, before=(), after=(), budget=4500):
    items = [prepare(o) for o in sorted(observations, key=lambda o: (o["t_ns"], o["tag"]))]
    if not items:
        return {"text": "", "tags": {}, "deltas": []}
    start = items[0]["t_ns"]
    end = items[-1]["t_ns"]
    out = []
    apps = Counter()
    transitions = []
    for i, item in enumerate(items):
        nxt = items[i + 1]["t_ns"] if i + 1 < len(items) else end
        apps[(item["app"], item["title"])] += max(0, nxt - item["t_ns"])
        label = (item["app"], item["title"])
        if not transitions or transitions[-1] != label:
            transitions.append(label)
    span = (end - start) / S
    out.append(f"WINDOW: {len(items)} observation steps over {gap_text(span)}")
    total = sum(apps.values()) or 1
    for (app, title), ns in apps.most_common(4):
        out.append(f"  active: {app} | {title or 'untitled'} ({round(100 * ns / total)}% of the window)")
    if len(transitions) > 1:
        out.append("  application/document sequence: " + " -> ".join(f"{a} | {t or 'untitled'}" for a, t in transitions[:8]))
    else:
        out.append("  application and document stayed the same throughout")
    urls = []
    for item in items:
        if item.get("url") and item["url"] not in urls:
            urls.append(item["url"])
    if urls:
        out.append("  URLs: " + "; ".join(urls[:3]))
    persistent = persistent_lines(items)
    used = sum(len(l) for l in out)
    if persistent:
        out.append("ON SCREEN THROUGHOUT:")
        for line in persistent[:8]:
            out.append("  " + line)
            used += len(line) + 3
    deltas = []
    for i in range(1, len(items)):
        deltas.append(delta(items[i - 1], items[i]))
    per = max(240, (budget - used - 600) // max(1, len(items)))
    pset = {l.lower() for l in persistent}
    out.append("OBSERVATION SEQUENCE:")
    seen = set()
    tags = {}
    for i, item in enumerate(items):
        tags[item["tag"]] = item.get("ids") or [item["tag"]]
        offset = (item["t_ns"] - start) / S
        out.append(f"[{item['tag']}] t+{gap_text(offset)}  {item['app']} | {item['title'] or 'untitled'}")
        out.append("  interaction: " + describe_interaction(item["interaction"]) + f"  (pattern: {item['interaction']['pattern']})")
        if i:
            d = deltas[i - 1]
            out.append("  change since previous: " + "; ".join(d["changes"]))
        fresh = [l for l in item["lines"] if l.lower() not in pset and l.lower() not in seen]
        fresh.sort(key=lambda l: -salience(l))
        size = 0
        shown = []
        for line in fresh:
            if size + len(line) > per:
                break
            shown.append(line)
            size += len(line) + 3
        seen.update(l.lower() for l in item["lines"])
        for line in sorted(shown, key=item["lines"].index):
            out.append("  text: " + line)
    for label, group in (("BEFORE THE WINDOW", before), ("AFTER THE WINDOW", after)):
        group = [prepare(g) for g in group]
        if not group:
            continue
        out.append(label + " (context only, not part of the window):")
        for g in group[:2]:
            offset = (g["t_ns"] - (start if label.startswith("BEFORE") else end)) / S
            sign = "-" if offset < 0 else "+"
            out.append(f"  {sign}{gap_text(abs(offset))}  {g['app']} | {g['title'] or 'untitled'}  (pattern: {g['interaction']['pattern']})")
    return {"text": "\n".join(out), "tags": tags, "deltas": deltas}
