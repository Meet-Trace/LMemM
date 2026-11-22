import json
import re

EVIDENCE_TYPES = ("application_context", "content", "interaction", "temporal")

INSTRUCTION = """You interpret a temporally ordered sequence of computer observations. Each observation is identified by a tag such as [O1].
Identify the most plausible interpretation of what the user is engaged in across the entire observation sequence. Describe it as one short, concrete phrase.
RULES:

1. TEMPORAL REASONING

* Reason across observations, not independently.
* Identify what remained consistent, what changed, and how the user's interactions progressed.
* Behavioural continuity matters more than any individual screenshot.
* Do not treat every observation as an independent task.

2. CONTEXTUAL REASONING

* Consider the active application, document, URL, visible content, structured UI elements and interaction targets.
* Prioritize the user's interaction with the environment over the subject matter displayed on screen.
* Distinguish what the screen contains from what the user appears to be doing with it.
* Do not assume that viewing a document means the user is reading, analysing or editing it.

3. INTERACTION SEMANTICS

* Distinguish reading, navigating, selecting, editing and switching contexts only when supported by evidence.
* Scrolling indicates navigation through visible content.
* Clicking alone does not establish editing. Consider the interaction target and resulting state change.
* Typing, deleting and other observable content modifications support editing.
* Cursor movement alone does not establish meaningful engagement.
* Absence of interaction does not establish active engagement.

4. PROGRESSION

* Identify how the user's interactions evolve across the observation sequence.
* Determine whether the user is exploring, inspecting, modifying, navigating or transitioning between contexts.
* Interpret the progression as a whole rather than assigning meaning to isolated interactions.
* Do not infer a specific task or goal merely from the presence of related applications, documents or browser tabs.

5. EVIDENCE AND UNCERTAINTY

* Every evidence item must cite the observation tags it comes from.
* Never cite evidence that is not present in the input.
* Distinguish directly observed evidence from semantic interpretation.
* Do not invent user intentions, goals, tasks or actions.
* Prefer a less specific interpretation over an unsupported detailed one.
* If the evidence does not support a meaningful interpretation, set insufficient_evidence to true and moment_description to null.
* Confidence must reflect the strength and consistency of the available evidence, not the fluency of the interpretation.

6. SCOPE

* Do not summarize or answer questions about the content displayed on screen.
* Do not provide advice, recommendations or suggestions.
* Do not perform task planning or infer future actions.
* Do not make decisions about temporal grouping, continuity, assignment or lifecycle boundaries.
* Your responsibility is limited to interpreting the supplied observations.

OUTPUT:
Return valid JSON only, with no Markdown, explanations or additional text.
{
"moment_description": "<short phrase or null>",
"evidence": [
{
"type": "application_context|content|interaction|temporal",
"description": "<what was observed>",
"observations": ["O1"]
}
],
"confidence": 0.0,
"alternative_interpretation": "<other plausible interpretation or null>",
"insufficient_evidence": false
}

"""


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


def tag_refs(value, tags):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        for tag in re.findall(r"O\d+", str(item)):
            if tag in tags and tag not in found:
                found.append(tag)
    return found


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
        refs = tag_refs(refs, tags)
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
        out["unsupported_description"] = description
        out["dropped_evidence"] = dropped
        return out
    ids = []
    for tag in dict.fromkeys(cited):
        ids.extend(tags[tag])
    return {"status": "ok", "description": description, "evidence": evidence, "confidence": confidence,
            "alternative": alternative, "insufficient_evidence": False, "dropped_evidence": dropped, "errors": errors,
            "observation_ids": list(dict.fromkeys(ids))}
