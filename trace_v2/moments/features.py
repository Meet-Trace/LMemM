import json
import re
from collections import Counter
from urllib.parse import urlparse

from ..observations.queries import _decode

ALGORITHM = "moment-features/1"
GRID = (12, 8)
MAX_TOKENS = 200
MIN_TOKENS = 3
STOP = frozenset("""the and for with that this from have are not you but was all can has had its our out
their they them then than there these those what when where which who will would your about after again also
been before being between both could does doing down during each few into just more most other over own same
should some such only very while why how any because until under here once off""".split())
WORD = re.compile(r"\w{3,32}", re.UNICODE)


def tokens(text):
    out = Counter()
    for w in WORD.findall((text or "").lower()):
        if w.isdigit() or w in STOP:
            continue
        out[w] += 1
    return out


def title_tokens(title, app_name):
    base = tokens(title)
    for w in tokens(app_name):
        base.pop(w, None)
    return sorted(base)


def url_parts(url):
    if not url or not isinstance(url, str):
        return None, []
    try:
        parsed = urlparse(url)
    except ValueError:
        return None, []
    host = (parsed.hostname or "").lower() or None
    return host, sorted(tokens(parsed.path.replace("/", " ").replace("-", " ").replace("_", " ")))


def layout_cells(structure_raw):
    if not structure_raw:
        return [], [], [0, 0]
    w, h = structure_raw["source"]["image_size"]
    gx, gy = GRID

    def cell(box):
        cx = min(gx - 1, max(0, int((box[0] + box[2] / 2) / w * gx)))
        cy = min(gy - 1, max(0, int((box[1] + box[3] / 2) / h * gy)))
        return cy * gx + cx
    text = sorted({cell(t["bbox"]) for t in structure_raw.get("text", [])})
    elems = sorted({cell(e["bbox"]) for e in structure_raw.get("elements", [])})
    lines = structure_raw.get("lines", [])
    return text, elems, [sum(1 for l in lines if l["orientation"] == "h"), sum(1 for l in lines if l["orientation"] == "v")]


def read_inputs(db, oid):
    obs = db.execute("SELECT captured_at_ns, app_bundle_id, app_name, window_title, context_json, privacy_json "
                     "FROM observations WHERE observation_id=?", (oid,)).fetchone()
    if obs is None:
        return None
    shot = db.execute("SELECT status FROM screenshots WHERE observation_id=?", (oid,)).fetchone()
    ocr = db.execute("SELECT extraction_id, status, text FROM extractions WHERE observation_id=? AND reader='ocr' "
                     "ORDER BY revision DESC LIMIT 1", (oid,)).fetchone()
    structure = db.execute("SELECT extraction_id, status, raw_encoding, raw FROM extractions WHERE observation_id=? "
                           "AND reader='structure' ORDER BY revision DESC LIMIT 1", (oid,)).fetchone()
    events = dict(db.execute("SELECT type, count(*) FROM interaction_events WHERE observation_id=? GROUP BY type", (oid,)))
    return {"obs": obs, "shot": shot[0] if shot else None, "ocr": ocr, "structure": structure, "events": events}


def extract(inputs):
    t, bundle, app_name, window_title, context_json, privacy_json = inputs["obs"]
    privacy = json.loads(privacy_json)
    if privacy.get("withheld"):
        return None, "withheld"
    context = json.loads(context_json)
    window = context.get("window") or {}
    host, path = url_parts(window.get("url"))
    ocr = inputs["ocr"]
    text = ocr[2] if ocr and ocr[1] == "succeeded" else ""
    tok = tokens(text)
    for w in title_tokens(window_title, app_name):
        tok[w] += 2
    for w in path:
        tok[w] += 2
    top = dict(sorted(tok.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TOKENS])
    structure = inputs["structure"]
    raw = _decode(structure[2], structure[3]) if structure and structure[1] == "succeeded" else None
    text_cells, elem_cells, lines = layout_cells(raw)
    events = inputs["events"]
    event_total = sum(v for k, v in events.items() if k != "cursor_move") + (1 if events.get("cursor_move") else 0)
    features = {
        "t": t, "app": bundle, "title": title_tokens(window_title, app_name), "host": host,
        "tok": top, "n_tok": len(tok), "text_cells": text_cells, "elem_cells": elem_cells, "lines": lines,
        "events": events, "event_total": event_total,
    }
    if features["n_tok"] < MIN_TOKENS and event_total == 0:
        return features, "no_evidence"
    return features, None
