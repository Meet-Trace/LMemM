import json
import re

EVIDENCE_TYPES = ("application_context", "content", "interaction", "temporal")

INSTRUCTION = (
    "You interpret a sequence of computer observations. Each observation is tagged like [O1]. "
    "Identify the most plausible single continuous Moment that explains the observed interactions and context: "
    "what the person appears to be doing across the whole window, in one short phrase.\n"
    "Rules:\n"
    "- Reason from evidence across time: which application and document stayed active, what interaction was observed, "
    "how the visible content changed. Behavioural continuity matters more than any single screen.\n"
    "- Distinguish reading, navigating, editing and switching only when the interaction evidence supports it. "
    "Scrolling or clicking is navigation, not editing. Typing, deleting and returns support editing. No interaction means the content was only visible.\n"
    "- Switching application alone does not start a new Moment.\n"
    "- Do not summarise or answer questions about the content on screen. Do not give advice or suggestions. "
    "Do not invent intentions, goals or tasks the evidence does not show.\n"
    "- Every evidence item must cite the observation tags it comes from. Never cite evidence that is not in the input.\n"
    "- If the evidence does not support a meaningful interpretation, set insufficient_evidence to true and moment_description to null.\n"
    "Answer with JSON only in this shape:\n"
    '{"moment_description": "<short phrase or null>", "evidence": [{"type": "application_context|content|interaction|temporal", '
    '"description": "<what was observed>", "observations": ["O1"]}], "confidence": <0 to 1>, '
    '"alternative_interpretation": "<other plausible reading or null>", "insufficient_evidence": <true|false>}\n\n'
)


def build_prompt(context_text):
    return INSTRUCTION + context_text


def _json_of(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            return None
    return None


def empty(status, errors):
    return {"status": status, "description": None, "evidence": [], "confidence": 0.0, "alternative": None,
            "insufficient_evidence": status == "insufficient", "dropped_evidence": 0, "errors": errors, "observation_ids": []}


def parse_output(text, tags):
    data = _json_of(text)
    if not isinstance(data, dict):
        return empty("invalid", ["response is not a JSON object"])
    errors = []
    insufficient = data.get("insufficient_evidence") is True
    description = data.get("moment_description")
    description = description.strip() if isinstance(description, str) and description.strip() else None
    if description is not None and description.lower() in ("null", "none", "unknown", "n/a"):
        description = None
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        errors.append("confidence missing or not a number")
        confidence = 0.0
    confidence = max(0.0, min(1.0, float(confidence)))
    evidence, dropped, cited = [], 0, []
    raw_items = data.get("evidence")
    if not isinstance(raw_items, list):
        errors.append("evidence is not a list")
        raw_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        kind, note = item.get("type"), item.get("description")
        refs = item.get("observations")
        refs = [r for r in (refs if isinstance(refs, list) else []) if isinstance(r, str) and r in tags]
        if kind not in EVIDENCE_TYPES or not isinstance(note, str) or not note.strip() or not refs:
            dropped += 1
            continue
        evidence.append({"type": kind, "description": note.strip(), "observations": refs})
        cited.extend(refs)
    if dropped:
        errors.append(f"{dropped} evidence items dropped (untraceable or malformed)")
    alternative = data.get("alternative_interpretation")
    alternative = alternative.strip() if isinstance(alternative, str) and alternative.strip() and alternative.strip().lower() != "null" else None
    if insufficient or description is None:
        out = empty("insufficient", errors)
        out["alternative"] = alternative
        out["dropped_evidence"] = dropped
        return out
    if not evidence:
        out = empty("insufficient", errors + ["no traceable evidence for the description"])
        out["dropped_evidence"] = dropped
        return out
    ids = []
    for tag in dict.fromkeys(cited):
        ids.extend(tags[tag])
    return {"status": "ok", "description": description, "evidence": evidence, "confidence": confidence,
            "alternative": alternative, "insufficient_evidence": False, "dropped_evidence": dropped, "errors": errors,
            "observation_ids": list(dict.fromkeys(ids))}
