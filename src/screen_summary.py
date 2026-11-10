"""AI summaries of a window's on-screen text: a short title for what the person was doing, and what they seem to be trying to do.

The narrator (config.FINDER_NARRATOR, the same small model the project finder uses, through your own
Ollama) reads what LMemM stored for a window and writes two short lines. They replace the raw text on
the thing page (the raw text stays one tap away in the viewer). Nothing here decides anything about
projects: the lines are only shown.

Contract
  summarize(item, narrator) -> {"title", "intent", "of", "model", "v"} or None when there is nothing to read
      or the model wrote nothing usable. Never raises for a model that answers badly.
  stale(item) -> whether the stored summary is missing or was written for older text.
  Capitalised names the model wrote that the window never mentions are cut (project_reasoner_ai.ground),
  so a small model cannot invent a client or a place.
The summary is kept on the item as item["summary"]; "of" is a hash of the text it read, so it is
written again only when that text changed.
"""

import hashlib
import re

import config
import project_reasoner_ai as ai
import project_reasoner_graph as graph
import tiny_scorer as ts

READ_CHARS = 1500            # how much of one window the model reads
SUMMARY_TOKENS = 90
SYSTEM = ("You read what was on a person's computer screen and say, plainly, what it shows and what the person "
          f"seems to be trying to do. {ai.UNTRUSTED}")
VERSION = 2                  # 1 wrote a description of the screen; 2 writes a title and an intent
PROMPT = ("Window:\n{window}\n\nAnswer in exactly two lines.\n"
          "Title: what the person was doing, in 3 to 7 words, starting with a verb ending in -ing "
          "(for example: Updating the outreach tracker). Do not describe the screen.\n"
          "Intent: what the person seems to be trying to do, in one sentence of at most 20 words. "
          "If the window does not show it, write: Intent: unclear.\n"
          "Say only what the window shows.")
_LINE = re.compile(r"^\W*(title|intent)\W*[:\-]\s*(.*)$", re.I)


def readable(item):
    """Whether LMemM stored any text for this window (beyond its app and title)."""
    texts = graph._texts(item)
    return any(texts[f] for f in ("typed", "received", "notes", "read", "screen"))


def source_hash(item):
    return hashlib.sha1(ai.source_text(item).encode("utf-8", "replace")).hexdigest()[:16]


def stale(item):
    """True when the item has text to read and no summary written for that text by the current model."""
    if not readable(item):
        return False
    done = item.get("summary")
    return not (isinstance(done, dict) and done.get("of") == source_hash(item) and done.get("model") == config.FINDER_NARRATOR
                and done.get("v") == VERSION)


def parse(answer):
    """The two lines out of the model's answer: {"title", "intent"}. Without markers the whole answer is
    the title (cut to its first sentence). "unclear" intents are dropped."""
    found = {}
    for line in re.split(r"\s*(?=\b(?:Title|Intent)\s*:)", answer or ""):
        m = _LINE.match(line.strip())
        if m and m.group(2).strip():
            found.setdefault(m.group(1).lower(), m.group(2).strip())
    if not found and (answer or "").strip():
        found["title"] = re.split(r"(?<=[.!?])\s", answer.strip())[0]
    if re.match(r"^\W*unclear\b", found.get("intent", ""), re.I):
        del found["intent"]
    return found


def summarize(item, narrator):
    if not readable(item):
        return None
    window = ai.window_text(item, READ_CHARS)
    answer = narrator(SYSTEM, PROMPT.format(window=window), SUMMARY_TOKENS)
    lines = parse(answer)
    haystack = ai.source_text(item)
    out = {k: ai.ground(v, haystack)[0] for k, v in lines.items()}
    out = {k: v for k, v in out.items() if v}
    if "title" not in out:
        return None
    return {"title": out["title"].rstrip(".").strip(), "intent": out.get("intent", ""), "of": source_hash(item),
            "model": config.FINDER_NARRATOR, "v": VERSION}


def narrator(url=config.FINDER_URL):
    """The narrator through your own Ollama, or None when Ollama or the model is missing."""
    try:
        if config.FINDER_NARRATOR not in ts.installed(url):
            return None
    except OSError:
        return None
    return ai.Narrator(ts.ChatScorer(config.FINDER_NARRATOR, url, timeout=300, extra=ts.cpu_only(config.FINDER_THREADS),
                                     keep_alive=0), {}, name=f"summary={config.FINDER_NARRATOR}")


def pending(items, limit):
    """The ids of up to `limit` things that need a summary, the most recently seen first."""
    order = sorted(items.values(), key=lambda i: i.get("last_seen") or "", reverse=True)
    return [i["id"] for i in order if stale(i)][:limit]
