from ..features import tokens
from . import content, interaction

PERSISTENT_SHARE = 0.6
SIMILAR = 0.85
CHANGED = 0.5


def title_terms(record):
    return set(tokens(record.get("title") or "")) | set(tokens(record.get("url") or ""))


def jaccard(a, b):
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def label_of(record):
    return (record.get("app"), record.get("title") or "")


def segments(records, ids):
    out = []
    for index, (record, oid) in enumerate(zip(records, ids)):
        label = label_of(record)
        if out and out[-1]["label"] == label:
            out[-1]["last"] = oid
            out[-1]["end_ns"] = record["t_ns"]
            out[-1]["count"] += 1
            out[-1]["indices"].append(index)
            continue
        out.append({"label": label, "first": oid, "last": oid, "start_ns": record["t_ns"], "end_ns": record["t_ns"],
                    "count": 1, "indices": [index]})
    return out


def segment_terms(records, ids, segment, lines_by_id):
    terms = {}
    for i in segment["indices"]:
        for term in title_terms(records[i]):
            terms[term] = terms.get(term, 0) + 2
        for entry in lines_by_id[ids[i]][:8]:
            for term in tokens(entry["text"]):
                terms[term] = terms.get(term, 0) + 1
    return terms


def transitions(records, ids, lines_by_id):
    parts = segments(records, ids)
    spans = [segment_terms(records, ids, segment, lines_by_id) for segment in parts]
    out = []
    for index in range(1, len(parts)):
        a, b = spans[index - 1], spans[index]
        shared = sorted(set(a) & set(b), key=lambda t: (-(a[t] + b[t]), t))[:5]
        out.append({"from": parts[index - 1]["last"], "to": parts[index]["first"],
                    "app_changed": parts[index - 1]["label"][0] != parts[index]["label"][0],
                    "window_changed": parts[index - 1]["label"] != parts[index]["label"],
                    "overlap": round(jaccard(set(a), set(b)) or 0.0, 2), "shared_terms": shared})
    return parts, out


def observation_lines(record):
    lines = [{"text": e["text"], "area": e.get("area")} for e in record.get("ocr", [])]
    lines.extend({"text": text, "area": None} for text in record.get("ax", []))
    return content.salient(lines, title_terms(record))


def evidence_level(records, lines_by_id, ids, totals):
    has_content = any(lines_by_id[oid] for oid in ids)
    has_titles = any(r.get("title") for r in records)
    has_interaction = any(interaction.level(r["interaction"]) != "none" for r in records)
    if not has_content and not has_titles and not has_interaction:
        return "none"
    if len(records) < 2 or (not has_content and not has_interaction):
        return "weak"
    return "ok"


def sample_indices(count, limit):
    if count <= limit:
        return set(range(count))
    return {round(i * (count - 1) / (limit - 1)) for i in range(limit)}


def build_context(records, before=None, after=None, lines_per_observation=4, persistent_limit=6, max_observations=16):
    records = sorted(records, key=lambda r: r["t_ns"])
    ids = [f"O{i + 1}" for i in range(len(records))]
    id_map = dict(zip(ids, (r["id"] for r in records)))
    lines_by_id = {oid: observation_lines(r) for oid, r in zip(ids, records)}
    keysets = {oid: {e["key"] for e in lines} for oid, lines in lines_by_id.items()}
    persistent = []
    if len(records) >= 3:
        counts = {}
        for keys in keysets.values():
            for key in keys:
                counts[key] = counts.get(key, 0) + 1
        persistent_keys = {k for k, n in counts.items() if n / len(records) > PERSISTENT_SHARE}
        shown = {}
        for lines in lines_by_id.values():
            for entry in lines:
                if entry["key"] in persistent_keys and entry["key"] not in shown:
                    shown[entry["key"]] = entry
        persistent = [e["text"] for e in sorted(shown.values(), key=lambda e: (-e["score"], e["key"]))[:persistent_limit]]
    else:
        persistent_keys = set()
    observations, previous = [], None
    shown_indices = sample_indices(len(records), max_observations)
    for index, (oid, record) in enumerate(zip(ids, records)):
        if index not in shown_indices:
            continue
        shown_lines = [e for e in lines_by_id[oid] if e["key"] not in persistent_keys]
        if previous is not None:
            shown_lines = [e for e in shown_lines if e["key"] not in keysets[previous["oid"]]]
        summary = record["interaction"]
        entry = {"oid": oid, "t_ns": record["t_ns"], "app": record.get("app"), "title": record.get("title") or "",
                 "url": record.get("url"), "focus": record.get("focus"), "interaction": summary,
                 "level": interaction.level(summary), "facts": interaction.describe(summary),
                 "lines": [{"text": e["text"], "area": e["area"]} for e in shown_lines[:lines_per_observation]],
                 "delta": None}
        if previous is not None:
            similarity = jaccard(keysets[previous["oid"]], keysets[oid])
            entry["delta"] = {
                "elapsed_s": round((record["t_ns"] - previous["t_ns"]) / 1e9, 1),
                "app_changed": previous["app"] != record.get("app"),
                "window_changed": (previous["app"], previous["title"]) != label_of(record),
                "content": None if similarity is None else ("unchanged" if similarity >= SIMILAR else "changed" if similarity < CHANGED else "partly changed"),
                "similarity": None if similarity is None else round(similarity, 2),
                "interaction": f"{previous['level']} -> {entry['level']}" if previous["level"] != entry["level"] else f"{entry['level']} (same)",
            }
        observations.append(entry)
        previous = {"oid": oid, "t_ns": record["t_ns"], "app": record.get("app"), "title": record.get("title") or "", "level": entry["level"]}
    parts, moves = transitions(records, ids, lines_by_id)
    totals = interaction.empty()
    for record in records:
        summary = record["interaction"]
        for key in ("clicks", "double_clicks", "keys_total", "deletes", "modifiers", "scroll_events", "scroll_net_dy",
                    "scroll_abs_dy", "scroll_gestures", "cursor_moves", "cursor_path_px"):
            totals[key] += summary[key]
        for key, value in summary["keys"].items():
            totals["keys"][key] = totals["keys"].get(key, 0) + value
    return {
        "ids": id_map, "observation_count": len(records), "start_ns": records[0]["t_ns"] if records else None, "end_ns": records[-1]["t_ns"] if records else None,
        "observations": observations, "observation_count": len(records), "segments": [{k: (list(v) if k == "label" else v) for k, v in s.items() if k != "indices"} for s in parts], "transitions": moves,
        "persistent": persistent, "totals": totals, "totals_facts": interaction.describe(totals),
        "evidence_level": evidence_level(records, lines_by_id, ids, totals) if records else "none",
        "before": brief(before, records[0]["t_ns"] if records else None, -1), "after": brief(after, records[-1]["t_ns"] if records else None, 1),
    }


def brief(record, anchor_ns, sign):
    if not record or anchor_ns is None:
        return None
    return {"app": record.get("app"), "title": record.get("title") or "",
            "gap_s": round(sign * (record["t_ns"] - anchor_ns) / 1e9, 1)}
