import json

from ...observations.queries import _decode
from . import content, interaction

AX_KEYS = ("title", "value", "description", "label", "help")
AX_LIMIT = 300
AX_DEPTH = 12
AX_NODES = 400


def latest_raw(db, oid, reader):
    row = db.execute("SELECT raw_encoding, raw FROM extractions WHERE observation_id=? AND reader=? AND status='succeeded' "
                     "ORDER BY revision DESC LIMIT 1", (oid, reader)).fetchone()
    return _decode(*row) if row and row[1] is not None else None


def ocr_lines(raw):
    if not raw:
        return []
    size = raw.get("image_size")
    out = []
    for line in raw.get("lines", []):
        text = (line.get("text") or "").strip()
        if text:
            out.append({"text": text, "area": content.area_of(line.get("box"), size)})
    return out


def ax_walk(node, strings, state, depth=0):
    if not isinstance(node, dict) or depth > AX_DEPTH or state["nodes"] > AX_NODES:
        return
    state["nodes"] += 1
    if node.get("focused") and state["focus"] is None:
        state["focus"] = node.get("role")
    for key in AX_KEYS:
        value = node.get(key)
        if isinstance(value, str) and len(value.strip()) >= 4:
            strings.append(value.strip()[:AX_LIMIT])
    for child in node.get("children") or []:
        ax_walk(child, strings, state, depth + 1)


def ax_content(raw):
    strings, state = [], {"nodes": 0, "focus": None}
    ax_walk(raw, strings, state)
    return strings, state["focus"]


def events_of(db, oid):
    rows = db.execute("SELECT type, payload_json FROM interaction_events WHERE observation_id=? ORDER BY seq", (oid,))
    return [(kind, json.loads(payload)) for kind, payload in rows]


def load_record(db, oid):
    row = db.execute("SELECT captured_at_ns, app_name, window_title, context_json FROM observations WHERE observation_id=?",
                     (oid,)).fetchone()
    if row is None:
        return None
    t_ns, app, title, context = row
    window = (json.loads(context).get("window") or {}) if context else {}
    strings, focus = ax_content(latest_raw(db, oid, "accessibility"))
    return {"id": oid, "t_ns": t_ns, "app": app, "title": title or window.get("title") or "", "url": window.get("url"),
            "ocr": ocr_lines(latest_raw(db, oid, "ocr")), "ax": strings, "focus": focus,
            "interaction": interaction.summarize(events_of(db, oid))}


def neighbour(db, t_ns, before):
    order, cmp = ("DESC", "<") if before else ("ASC", ">")
    row = db.execute(f"SELECT observation_id FROM observations WHERE captured_at_ns {cmp} ? ORDER BY captured_at_ns {order} LIMIT 1",
                     (t_ns,)).fetchone()
    return load_record(db, row[0]) if row else None


def load_window(db, observation_ids):
    records = [r for r in (load_record(db, oid) for oid in observation_ids) if r]
    records.sort(key=lambda r: r["t_ns"])
    if not records:
        return records, None, None
    return records, neighbour(db, records[0]["t_ns"], True), neighbour(db, records[-1]["t_ns"], False)
