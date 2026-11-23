import json

from ..observations.queries import _decode, raw_of
from .normalise import normalise

SAMPLE = 8


def ax_ok(conn, oid, reader):
    row = conn.execute("SELECT extraction_id, raw_encoding, raw FROM extractions WHERE observation_id=? AND reader=? "
                       "AND status='succeeded' ORDER BY revision DESC LIMIT 1", (oid, reader)).fetchone()
    return raw_of(conn, *row) if row else None


def ocr_of(conn, oid):
    raw = ax_ok(conn, oid, "ocr") or {}
    return {"lines": [{"text": l.get("text", ""), "box": l.get("box")} for l in raw.get("lines", [])], "image_size": raw.get("image_size")}


def record_of(conn, oid):
    row = conn.execute("SELECT captured_at_ns, app_name, window_title, context_json, app_bundle_id FROM observations WHERE observation_id=?",
                       (oid,)).fetchone()
    if row is None:
        return None
    scale, url = 1.0, None
    try:
        context = json.loads(row[3]) or {}
        url = (context.get("window") or {}).get("url")
        scale = float((context.get("display") or {}).get("scale") or 1.0)
    except (ValueError, TypeError):
        pass
    events = [{"type": kind, "t_ns": t, "payload": json.loads(payload)} for t, kind, payload in conn.execute(
        "SELECT occurred_at_ns, type, payload_json FROM interaction_events WHERE observation_id=? ORDER BY seq", (oid,))]
    return {"t_ns": row[0], "app": row[1] or "unknown", "title": row[2], "url": url, "events": events, "bundle": row[4], "scale": scale}


def core_members(conn, moment_id):
    row = conn.execute("SELECT members_encoding, members FROM moment_revisions WHERE moment_id=? ORDER BY revision DESC LIMIT 1",
                       (moment_id,)).fetchone()
    return [m["o"] for m in _decode(*row) if m["role"] == "core"] if row else []


def neighbours(conn, start_ns, end_ns, members, count=1):
    marks = ",".join("?" * len(members))
    before = conn.execute(f"SELECT observation_id FROM observations WHERE captured_at_ns<? AND observation_id NOT IN ({marks}) "
                          "ORDER BY captured_at_ns DESC LIMIT ?", (start_ns, *members, count)).fetchall()
    after = conn.execute(f"SELECT observation_id FROM observations WHERE captured_at_ns>? AND observation_id NOT IN ({marks}) "
                         "ORDER BY captured_at_ns LIMIT ?", (end_ns, *members, count)).fetchall()
    return [r[0] for r in reversed(before)], [r[0] for r in after]


def signature(conn, oid):
    row = conn.execute("SELECT app_name, window_title FROM observations WHERE observation_id=?", (oid,)).fetchone() or ()
    record = record_of(conn, oid)
    words = {w for part in (*row, record and record["url"]) if part for w in str(part).lower().split()}
    words.update(w.lower() for line in ocr_of(conn, oid)["lines"] for w in line["text"].split() if len(w) > 3)
    return words


def segments(conn, members, k):
    """-> k (start, end) index ranges; cuts sit where the screen changed most between neighbouring observations."""
    n = len(members)
    if k <= 1 or n <= k:
        return [(i * n // k, (i + 1) * n // k) for i in range(k)]
    sigs = [signature(conn, oid) for oid in members]
    gaps = []
    for i in range(1, n):
        union = sigs[i - 1] | sigs[i]
        gaps.append((1 - len(sigs[i - 1] & sigs[i]) / len(union) if union else 0.0, i))
    cuts = sorted(i for _, i in sorted(gaps, key=lambda g: (-g[0], g[1]))[:k - 1])
    edges = [0, *cuts, n]
    return list(zip(edges, edges[1:]))


def steps(conn, members, sample=SAMPLE):
    n = len(members)
    k = max(1, min(sample, n))
    out = []
    for lo, hi in segments(conn, members, k):
        chunk = members[lo:hi]
        records = [r for r in (record_of(conn, oid) for oid in chunk) if r]
        if not records:
            continue
        middle = chunk[len(chunk) // 2]
        rep = record_of(conn, middle) or records[0]
        out.append({"tag": f"O{len(out) + 1}", "ids": chunk, "t_ns": records[0]["t_ns"], "app": rep["app"], "title": rep["title"],
                    "url": rep["url"], "events": [e for r in records for e in r["events"]], "bundle": rep["bundle"], "scale": rep["scale"],
                    "ocr": ocr_of(conn, middle), "ax": ax_ok(conn, middle, "accessibility")})
    return out


def packet(conn, moment_id, start_ns, end_ns, budget=4500, sample=SAMPLE):
    """-> {"text", "tags"} the normalised account of a Moment, or None when it has too little to describe."""
    members = core_members(conn, moment_id)
    if len(members) < 2:
        return None
    sequence = steps(conn, members, sample)
    before, after = neighbours(conn, start_ns, end_ns, members)
    around = lambda ids: [dict(r, tag="B", lines=[]) for r in (record_of(conn, i) for i in ids) if r]
    built = normalise(sequence, around(before), around(after), budget)
    return {"text": built["text"], "tags": built["tags"]} if built["text"] else None
