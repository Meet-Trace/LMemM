import json
import re

import numpy as np

from ..descriptions.call import post_json
from ..descriptions.gate import user_active

PROMPT_VERSION = "purpose-match/1"

PURPOSE_PROMPT = """You are given what a person did during one short moment on their computer.

What they did (an action, not a reason):
{action}

State why they were doing it. Name the outcome they are working towards, in plain words, as if finishing the sentence "They are trying to ...". Do not repeat the action. Do not name a goal you cannot infer from the evidence; say "unclear" instead.

Reply with exactly two lines:
PURPOSE: <the outcome they are working towards, at most 20 words>
WHY HERE: <why this app or tool serves that outcome, at most 15 words>"""

MATCH_PROMPT = """Two moments from one person's day. Decide whether they serve the SAME goal, meaning the same specific outcome the person is working towards, so that finishing one would be part of finishing the other.

Being in the same app, using the same tool, mentioning the same word, or looking at related material is NOT enough. Two moments in a terminal can serve different goals. A browser and a terminal can serve the same goal.

Moment A
Action: {action_a}
Purpose: {purpose_a}
Why here: {why_a}

Moment B
Action: {action_b}
Purpose: {purpose_b}
Why here: {why_b}

Reply with exactly two lines:
VERDICT: SAME or DIFFERENT
REASON: <one sentence naming the shared outcome, or the two different outcomes>"""

UNCLEAR = re.compile(r"^\W*(unclear|unknown|not clear|insufficient|n/?a)\b", re.I)


def parse_purpose(text):
    text = text or ""
    purpose = re.search(r"PURPOSE\s*:\s*(.+)", text, re.I)
    why = re.search(r"WHY HERE\s*:\s*(.+)", text, re.I)
    p, w = (purpose.group(1).strip() if purpose else ""), (why.group(1).strip() if why else "")
    return {"purpose": "" if UNCLEAR.match(p) else p, "why": w, "clear": bool(p) and not UNCLEAR.match(p)}


def parse_verdict(text):
    text = text or ""
    verdict = re.search(r"VERDICT\s*:\s*\W*(SAME|DIFFERENT)", text, re.I)
    reason = re.search(r"REASON\s*:\s*(.+)", text, re.I)
    return (verdict.group(1).upper() if verdict else "UNKNOWN"), (reason.group(1).strip() if reason else text.strip()[:160])


def text_action(moment):
    """The vision description when the Moment has one, else the Moment's own summary in words."""
    if moment.get("description"):
        return moment["description"]
    s = moment["summary"]
    apps = ", ".join(a.split(".")[-1] for a in list(s.get("apps", {}))[:3]) or "an application"
    return f"Worked in {apps} for {round(s.get('duration_s', 0))} seconds on: {' '.join(s.get('top_tokens', [])[:10])}"


class OllamaInterpreter:
    prompt_version = PROMPT_VERSION

    def __init__(self, model, host="http://127.0.0.1:11434", embed_model="embeddinggemma", describe=text_action, timeout=300, description_model=None, abort_when=user_active):
        self.description_model, self.abort_when = description_model, abort_when
        self.model, self.host, self.embed_model, self.describe, self.timeout = model, host, embed_model, describe, timeout

    def _post(self, path, body, abort=True):
        return post_json(self.host + path, body, self.timeout, self.abort_when if abort else None)

    def _chat(self, prompt, num_predict):
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": False, "think": False,
                "keep_alive": "2m", "options": {"num_ctx": 4096, "num_predict": num_predict, "temperature": 0}}
        return (self._post("/api/chat", body).get("message", {}).get("content") or "").strip()

    def interpret(self, moment):
        action = self.describe(moment)
        parsed = parse_purpose(self._chat(PURPOSE_PROMPT.format(action=action), 120))
        return {"action": action, **parsed}

    def embed(self, texts):
        # CPU and short: not worth a cancel check (an ioreg run every second)
        reply = self._post("/api/embed", {"model": self.embed_model, "input": texts, "keep_alive": "5m", "options": {"num_gpu": 0}}, abort=False)
        vectors = np.asarray(reply["embeddings"], dtype=np.float32)
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)

    def same(self, a, b):
        prompt = MATCH_PROMPT.format(action_a=a["action"], purpose_a=a["purpose"], why_a=a["why"] or "n/a",
                                     action_b=b["action"], purpose_b=b["purpose"], why_b=b["why"] or "n/a")
        return parse_verdict(self._chat(prompt, 100))
