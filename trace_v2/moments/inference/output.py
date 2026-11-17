import json
import re
from dataclasses import dataclass, field

from .prompt import EVIDENCE_TYPES

MAX_DESCRIPTION = 200
MAX_EVIDENCE = 3
REF = re.compile(r"^\s*(O\d+)\b")
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass(frozen=True)
class Interpretation:
    status: str
    description: str | None = None
    evidence: tuple = ()
    confidence: float = 0.0
    alternative: str | None = None
    unassigned: tuple = ()
    problems: tuple = field(default_factory=tuple)


def failed(*problems):
    return Interpretation("failed", problems=tuple(problems))


def load_object(raw):
    text = FENCE.sub("", (raw or "").strip())
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def clean_text(value, limit=MAX_DESCRIPTION):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] if value else None


def ref_of(value):
    if not isinstance(value, str):
        return None
    match = REF.match(value)
    return match.group(1) if match else None


def clean_ids(value, valid):
    if not isinstance(value, list):
        return (), 0
    refs = [ref_of(v) for v in value]
    kept = tuple(dict.fromkeys(r for r in refs if r in valid))
    return kept, len([r for r in refs if r not in valid])


def clean_evidence(value, valid):
    kept, dropped = [], 0
    if not isinstance(value, list):
        return (), 0
    for item in value[:MAX_EVIDENCE]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        kind, text = item.get("type"), clean_text(item.get("description"))
        refs, _ = clean_ids(item.get("observations"), valid)
        if kind not in EVIDENCE_TYPES or text is None or not refs:
            dropped += 1
            continue
        kept.append({"type": kind, "description": text, "observations": list(refs)})
    return tuple(kept), dropped


def parse_interpretation(raw, valid_ids):
    valid = frozenset(valid_ids)
    data = load_object(raw)
    if data is None:
        return failed("not_json")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return failed("bad_confidence")
    confidence = max(0.0, min(1.0, float(confidence)))
    description = clean_text(data.get("moment_description"))
    evidence, dropped = clean_evidence(data.get("evidence"), valid)
    unassigned, _ = clean_ids(data.get("unassigned_observations"), valid)
    alternative = clean_text(data.get("alternative_interpretation"))
    problems = []
    if dropped:
        problems.append(f"dropped_{dropped}_ungrounded_evidence")
    insufficient = data.get("insufficient_evidence")
    if not isinstance(insufficient, bool):
        return failed("bad_insufficient_flag")
    if insufficient or description is None:
        return Interpretation("insufficient", None, evidence, min(confidence, 0.4), alternative, unassigned, tuple(problems))
    if not evidence:
        problems.append("no_grounded_evidence")
        return Interpretation("insufficient", None, (), min(confidence, 0.4), alternative, unassigned, tuple(problems))
    return Interpretation("ok", description, evidence, confidence, alternative, unassigned, tuple(problems))
