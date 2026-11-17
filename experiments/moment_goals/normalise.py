import math
from collections import Counter

import norm_app
import norm_ax
import norm_interaction
import norm_ocr

S = 1_000_000_000
LEVELS = ({"rows": 6, "blocks": 3, "controls": 8, "blockchars": 160}, {"rows": 4, "blocks": 2, "controls": 5, "blockchars": 120},
          {"rows": 3, "blocks": 1, "controls": 3, "blockchars": 90}, {"rows": 2, "blocks": 1, "controls": 0, "blockchars": 60})


def stamp(seconds):
    seconds = max(0, round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def span(tags):
    numbers = sorted(int(t[1:]) for t in tags)
    out, start, prev = [], None, None
    for n in numbers:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
    if start is not None:
        out.append((start, prev))
    return ", ".join(f"O{a}" if a == b else f"O{a}–O{b}" for a, b in out)


def table_key(table):
    return "T:" + "|".join((table["header"] or table["rows"][0])).lower()


def table_signature(table):
    return tuple(tuple(r) for r in table["rows"][:3])


def observation_of(record):
    bundle, app = record.get("bundle"), record["app"]
    browser = norm_app.is_browser(bundle, app)
    ocr = record.get("ocr") or {}
    image_size = ocr.get("image_size")
    lines = ocr.get("lines") or []
    structure = norm_ocr.structure_of(lines, image_size, browser)
    items, _ = norm_ocr.prepare(lines, image_size, browser)
    text = " ".join(i["text"] for i in items)
    ax = norm_ax.summarize(record.get("ax"), text)
    interaction = norm_interaction.summarize(record.get("events") or [], record.get("ax"), items, record.get("scale") or 1.0)
    keys = {}
    for h in structure["headings"]:
        keys["H:" + h.lower()] = {"kind": "heading", "text": h}
    for t in structure["tables"]:
        keys[table_key(t)] = {"kind": "table", "table": t}
    for b in structure["blocks"]:
        keys["B:" + b[:60].lower()] = {"kind": "block", "text": b}
    for x in structure["technical"]:
        keys["X:" + x[:60].lower()] = {"kind": "technical", "text": x}
    for l in structure["labels"]:
        keys["L:" + l.lower()] = {"kind": "label", "text": l}
    return {"tag": record["tag"], "ids": record.get("ids") or [record["tag"]], "t_ns": record["t_ns"], "app": app, "bundle": bundle,
            "title": record.get("title"), "url": record.get("url"), "structure": structure, "ax": ax, "interaction": interaction,
            "keys": keys, "browser": browser}


def table_lines(table, rows, indent="  "):
    out = []
    if table["header"]:
        out.append(indent + "| " + " | ".join(table["header"]) + " |")
    for row in table["rows"][:rows]:
        out.append(indent + "| " + " | ".join(row) + " |")
    hidden = table["total_rows"] - min(rows, len(table["rows"]))
    if hidden > 0:
        out.append(indent + f"({hidden} more rows visible)")
    return out


def diff(prev, cur):
    changes = []
    if prev["app"] != cur["app"]:
        changes.append(f"application changed from {prev['app']} to {cur['app']}")
    if prev["title"] != cur["title"]:
        changes.append(f"window title changed to '{(cur['title'] or 'untitled')[:70]}'")
    if prev["url"] != cur["url"] and (prev["url"] or cur["url"]):
        changes.append("URL changed")
    pk, ck = set(prev["keys"]), set(cur["keys"])
    for key in sorted(pk & ck):
        a, b = prev["keys"][key], cur["keys"][key]
        if a["kind"] == "table" and table_signature(a["table"]) != table_signature(b["table"]):
            changes.append("visible table rows changed (different rows now shown)")
    added, removed = len(ck - pk), len(pk - ck)
    if added or removed:
        changes.append(f"visible content: {added} new elements, {removed} no longer visible")
    if cur["ax"] and prev["ax"] and cur["ax"].get("focused") != prev["ax"].get("focused") and cur["ax"].get("focused"):
        f = cur["ax"]["focused"]
        changes.append("focus moved to " + (f"'{f['label']}' ({f['role']})" if f.get("label") else str(f.get("role"))))
    return changes


def render(observations, before, after, level):
    n = len(observations)
    first = observations[0]
    start = first["t_ns"]
    out = []
    apps = Counter(o["app"] for o in observations)
    titles = Counter(o["title"] for o in observations if o["title"])
    urls = list(dict.fromkeys(o["url"] for o in observations if o["url"]))
    out.append("APPLICATION CONTEXT")
    out.append("  Application: " + ", ".join(f"{a} ({next(o['bundle'] for o in observations if o['app'] == a)})" if
                                             next(o['bundle'] for o in observations if o['app'] == a) else a for a in apps))
    if titles:
        out.append("  Active document: " + "; ".join(f"{t[:80]} ({span([o['tag'] for o in observations if o['title'] == t])})" for t, _ in titles.most_common(3)))
    if urls:
        out.append("  URL: " + "; ".join(f"{u[:100]}" for u in urls[:3]))
    has_table = any(o["structure"]["tables"] for o in observations)
    doc_type = norm_app.document_type(first["bundle"], first["app"], first["title"], first["url"], has_table)
    if doc_type:
        out.append("  Document type: " + doc_type)
    out.append("  Context continuity: " + norm_app.continuity([{"app": o["app"], "title": o["title"], "url": o["url"]} for o in observations]))
    tab_titles = [o["title"] for o in observations if o["browser"] and o["title"]]
    if len(set(tab_titles)) > 1:
        switches = []
        for prev, cur in zip(observations, observations[1:]):
            if prev["browser"] and cur["browser"] and prev["title"] != cur["title"]:
                switches.append(f"{cur['tag']}: '{(prev['title'] or '')[:40]}' -> '{(cur['title'] or '')[:40]}'")
        if switches:
            out.append("  Tab or page switches observed: " + "; ".join(switches[:4]))

    seen_in = {}
    for o in observations:
        for key, item in o["keys"].items():
            seen_in.setdefault(key, {"item": item, "tags": []})["tags"].append(o["tag"])
    threshold = 1 if n <= 2 else math.ceil(n / 2)
    persistent = {k for k, v in seen_in.items() if len(v["tags"]) >= threshold and v["item"]["kind"] != "label"}
    cfg = LEVELS[level]
    out.append("")
    out.append("DOCUMENT STRUCTURE")
    shown = False
    heads = [(k, v) for k, v in seen_in.items() if k in persistent and v["item"]["kind"] == "heading"]
    if heads:
        out.append("  Headings: " + "; ".join(f"{v['item']['text']} ({span(v['tags'])})" for _, v in heads[:6]))
        shown = True
    for k, v in seen_in.items():
        if k in persistent and v["item"]["kind"] == "table":
            out.append(f"  Table ({span(v['tags'])}), rows as first seen in {v['tags'][0]}:")
            out.extend(table_lines(v["item"]["table"], cfg["rows"], "    "))
            shown = True
    blocks = [(k, v) for k, v in seen_in.items() if k in persistent and v["item"]["kind"] == "block"][:cfg["blocks"]]
    for _, v in blocks:
        out.append(f"  Text ({span(v['tags'])}): " + v["item"]["text"][:cfg["blockchars"]])
        shown = True
    for k, v in seen_in.items():
        if k in persistent and v["item"]["kind"] == "technical":
            out.append(f"  Code or technical text ({span(v['tags'])}): " + v["item"]["text"][:cfg["blockchars"]])
            shown = True
    controls = []
    for o in observations:
        for c in (o["ax"] or {}).get("controls", []):
            if c not in [x[0] for x in controls]:
                controls.append((c, [o["tag"]]))
            else:
                next(x for x in controls if x[0] == c)[1].append(o["tag"])
    if controls and cfg["controls"]:
        out.append("  Interface elements not visible as text: " + "; ".join(f"{c} ({span(t)})" for c, t in controls[:cfg["controls"]]))
        shown = True
    if not shown:
        out.append("  No document structure could be extracted")

    out.append("")
    out.append("OBSERVATION SEQUENCE")
    for i, o in enumerate(observations):
        prev = observations[i - 1] if i else None
        out.append(f"{o['tag']} — t+{stamp((o['t_ns'] - start) / S)}")
        context = []
        if prev is None or prev["app"] != o["app"] or prev["title"] != o["title"]:
            context.append(f"{o['app']} | {(o['title'] or 'untitled')[:70]}")
        fresh = [(k, v) for k, v in o["keys"].items() if k not in persistent and (prev is None or k not in prev["keys"])]
        for k, item in fresh[:4]:
            if item["kind"] == "heading":
                context.append("heading: " + item["text"][:80])
            elif item["kind"] == "table":
                context.append("table: " + (" | ".join(item["table"]["header"] or [])[:80] or "grid of values") + f" ({item['table']['total_rows']} rows visible)")
            elif item["kind"] in ("block", "technical"):
                context.append(item["kind"] + ": " + item["text"][:cfg["blockchars"]])
            else:
                context.append("label: " + item["text"][:60])
        for k in persistent:
            item = seen_in[k]["item"]
            mine = o["keys"].get(k)
            if item["kind"] == "table" and mine and table_signature(mine["table"]) != table_signature(item["table"]):
                rows = mine["table"]["rows"][:2]
                context.append("table rows now shown: " + " / ".join(" | ".join(r) for r in rows))
        focused = (o["ax"] or {}).get("focused")
        if focused:
            bits = [focused["role"] or "element"]
            if focused.get("label"):
                bits.append(f"'{focused['label']}'")
            if focused.get("selected"):
                bits.append(f"with selected text '{focused['selected'][:40]}'")
            context.append("focused: " + " ".join(bits))
        if context:
            out.append("  Visible context: " + "; ".join(context))
        else:
            out.append("  Visible context: same as DOCUMENT STRUCTURE")
        out.append("  Interaction: " + "; ".join(o["interaction"]["phrases"]))
        if prev is not None:
            changes = diff(prev, o)
            out.append(f"  Observable changes since {prev['tag']}: " + ("; ".join(changes) if changes else "none"))

    out.append("")
    out.append("TEMPORAL SUMMARY")
    consistent = [f"{len(persistent)} content element{'s' if len(persistent) != 1 else ''} visible in at least half of the observations"] if persistent else []
    if len(apps) == 1:
        consistent.insert(0, f"application stayed {next(iter(apps))}")
    if len(set(o["title"] for o in observations)) == 1:
        consistent.append("window title unchanged")
    if consistent:
        out.append("  Remained consistent: " + "; ".join(consistent))
    changed = []
    for prev, cur in zip(observations, observations[1:]):
        for c in diff(prev, cur):
            changed.append(f"{cur['tag']}: {c}")
    if changed:
        out.append("  Visibly changed: " + "; ".join(changed[:6]))
    else:
        out.append("  Visibly changed: nothing")
    progression = []
    for o in observations:
        label = " + ".join(o["interaction"]["kinds"]).replace("none", "no interaction")
        if progression and progression[-1][0] == label:
            progression[-1][1].append(o["tag"])
        else:
            progression.append((label, [o["tag"]]))
    out.append("  Interaction progression: " + " -> ".join(f"{label} ({span(tags)})" for label, tags in progression))
    for title, group, ref in (("BEFORE THE WINDOW", before, start), ("AFTER THE WINDOW", after, observations[-1]["t_ns"])):
        if not group:
            continue
        out.append("")
        out.append(title + " (context only, not evidence for this window)")
        for g in group[:2]:
            offset = (g["t_ns"] - ref) / S
            out.append(f"  {'-' if offset < 0 else '+'}{stamp(abs(offset))}  {g['app']} | {(g['title'] or 'untitled')[:70]}")
    return "\n".join(out)


def normalise(records, before=(), after=(), budget=4500):
    observations = [observation_of(r) for r in sorted(records, key=lambda r: (r["t_ns"], r["tag"]))]
    if not observations:
        return {"text": "", "tags": {}, "observations": []}
    text = ""
    for level in range(len(LEVELS)):
        text = render(observations, list(before), list(after), level)
        if len(text) <= budget:
            break
    tags = {o["tag"]: o["ids"] for o in observations}
    return {"text": text, "tags": tags, "observations": observations, "level": level}
