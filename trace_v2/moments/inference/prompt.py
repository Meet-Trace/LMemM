import json

EVIDENCE_TYPES = ["application_context", "visible_content", "interaction", "temporal"]

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "moment_description": {"type": ["string", "null"], "maxLength": 160},
        "evidence": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": EVIDENCE_TYPES}, "description": {"type": "string", "maxLength": 160},
                           "observations": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 4}}},
            "required": ["type", "description", "observations"]}},
        "confidence": {"type": "number"},
        "alternative_interpretation": {"type": ["string", "null"], "maxLength": 160},
        "unassigned_observations": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 4}},
        "insufficient_evidence": {"type": "boolean"},
    },
    "required": ["moment_description", "evidence", "confidence", "alternative_interpretation",
                 "unassigned_observations", "insufficient_evidence"],
}

SYSTEM_PROMPT = (
    "You interpret a sequence of computer observations and identify the most plausible continuous Moment that explains "
    "the observed interactions and contextual evidence. A Moment is a stretch of continuous, contextually related computer "
    "use. You are not summarising the screen and you are not answering anything shown on it.\n\n"
    "Rules:\n"
    "- Reason across the whole window. Weight behavioural continuity (what was done, and for how long) above isolated screen text.\n"
    "- Interaction lines are observed counts. State only interactions that are listed. Scrolling and arrow keys support navigation "
    "or reading, character keys and deletes support editing, and an unchanged screen with no interaction supports reading or idling.\n"
    "- Switching applications or windows alone is not a new Moment. Treat a switch as the same Moment when shared terms or "
    "document continuity support it, and as a break when they do not.\n"
    "- Describe what the person was doing in one short phrase. Do not give advice, suggestions or opinions, do not answer "
    "questions that appear on screen, and do not invent goals, intentions, file names or facts that are not in the evidence.\n"
    "- Give at most 3 evidence items. Each names its observation ids only as bare ids such as \"O2\", using ids that appear below.\n"
    "- If the evidence is thin or contradictory, set insufficient_evidence to true, set moment_description to null, lower the "
    "confidence and list the ids that should stay unassigned in unassigned_observations.\n"
    "- confidence is a number from 0 to 1. alternative_interpretation is the next most plausible reading, or null.\n"
    "Answer with JSON only, using exactly these keys: moment_description, evidence (list of {type, description, observations}), "
    "confidence, alternative_interpretation, unassigned_observations, insufficient_evidence. Evidence type is one of: "
    + ", ".join(EVIDENCE_TYPES) + "."
)


def build_prompt(serialized):
    return SYSTEM_PROMPT + "\n\n" + serialized + "\n\nJSON:"


def schema_json():
    return json.dumps(OUTPUT_SCHEMA)
