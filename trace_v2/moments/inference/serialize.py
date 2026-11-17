from datetime import datetime

AREA_ORDER = ("top-left", "top-centre", "top-right", "middle-left", "middle-centre", "middle-right",
              "bottom-left", "bottom-centre", "bottom-right")


def clock(ns):
    return datetime.fromtimestamp(ns / 1e9).strftime("%H:%M:%S")


def span(seconds):
    seconds = float(seconds)
    return f"{int(seconds)}s" if seconds < 120 else f"{seconds / 60:.1f}min"


def application_block(context):
    lines = ["APPLICATION CONTEXT"]
    for segment in context["segments"]:
        app, title = segment["label"]
        first, last = segment["first"], segment["last"]
        where = first if first == last else f"{first}-{last}"
        duration = span((segment["end_ns"] - segment["start_ns"]) / 1e9)
        lines.append(f"- {app or 'unknown app'}: \"{title or 'untitled'}\" [{where}, {duration}]")
    for move in context["transitions"]:
        kind = "application switch" if move["app_changed"] else "window or document change"
        shared = ", ".join(move["shared_terms"]) if move["shared_terms"] else "none"
        lines.append(f"- {kind} {move['from']} -> {move['to']}; shared terms: {shared}; term overlap {move['overlap']}")
    if not context["transitions"] and context["segments"]:
        lines.append("- no application or document change in the window")
    urls = sorted({o["url"] for o in context["observations"] if o.get("url")})
    for url in urls[:3]:
        lines.append(f"- url: {url}")
    return lines


def interaction_block(context):
    lines = ["INTERACTION EVIDENCE (observed counts only)"]
    totals = context["totals_facts"]
    lines.append("- window total: " + ("; ".join(totals) if totals else "no interaction events recorded"))
    for obs in context["observations"]:
        facts = "; ".join(obs["facts"]) if obs["facts"] else "no interaction"
        focus = f"; focused element: {obs['focus']}" if obs.get("focus") else ""
        lines.append(f"- {obs['oid']} {clock(obs['t_ns'])}: {facts}{focus}")
    return lines


def content_block(context, budget):
    lines = ["VISIBLE CONTENT"]
    if context["persistent"]:
        lines.append("- present throughout: " + " | ".join(context["persistent"]))
    used = 0
    for obs in context["observations"]:
        if not obs["lines"]:
            continue
        lines.append(f"- {obs['oid']} new or distinct text:")
        for entry in obs["lines"]:
            text = entry["text"]
            if used + len(text) > budget:
                return lines
            used += len(text)
            prefix = f"[{entry['area']}] " if entry.get("area") else ""
            lines.append(f"    {prefix}{text}")
    return lines


def temporal_block(context):
    lines = ["TEMPORAL CONTEXT"]
    if context["before"]:
        b = context["before"]
        lines.append(f"- before the window ({span(b['gap_s'])} earlier, outside it): {b['app']}: \"{b['title']}\"")
    for obs in context["observations"]:
        delta = obs["delta"]
        if delta is None:
            lines.append(f"- {obs['oid']} {clock(obs['t_ns'])}: first observation in the window")
            continue
        parts = [f"+{span(delta['elapsed_s'])}"]
        parts.append("application changed" if delta["app_changed"] else "same application")
        parts.append("window or document changed" if delta["window_changed"] else "same window")
        parts.append("content " + (delta["content"] or "not comparable") + (f" (similarity {delta['similarity']})" if delta["similarity"] is not None else ""))
        parts.append("interaction " + delta["interaction"])
        lines.append(f"- {obs['oid']} {clock(obs['t_ns'])}: " + "; ".join(parts))
    if context["after"]:
        a = context["after"]
        lines.append(f"- after the window ({span(a['gap_s'])} later, outside it): {a['app']}: \"{a['title']}\"")
    return lines


def serialize(context, content_budget=1400):
    count = context["observation_count"]
    shown = len(context["observations"])
    header = [f"OBSERVATION WINDOW: {count} observation(s)" + (f", {shown} shown" if shown != count else "")
              + (f", {clock(context['start_ns'])} to {clock(context['end_ns'])}" if count else "")
              + f"; evidence level: {context['evidence_level']}"]
    blocks = [header, application_block(context), interaction_block(context), content_block(context, content_budget),
              temporal_block(context)]
    return "\n".join("\n".join(block) for block in blocks)
