"""AI summaries of a window's on-screen text: a short title and a one-sentence summary of what the person was doing.

The narrator (config.FINDER_NARRATOR, the same small model the project finder uses, through your own
Ollama) reads what LMemM stored for a window and writes two short lines. They replace the raw text on
the thing page (the raw text stays one tap away in the viewer). Nothing here decides anything about
projects: the lines are only shown.

Contract
  summarize(item, narrator) -> {"title", "summary", "of", "model", "v"} or None when there is nothing to read
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
import notes
import project_reasoner_ai as ai
import project_reasoner_graph as graph
import tiny_scorer as ts

READ_CHARS = 1500            # how much of one window the model reads
SUMMARY_TOKENS = 110
SYSTEM = ("You read what was on a person's computer screen and say, plainly, what it shows and what the person "
          f"seems to be trying to do. {ai.UNTRUSTED}")
VERSION = 3                  # 1 described the screen; 2 a title and an intent; 3 a title and a summary that reads the notes
PROMPT = ("Window:\n{window}\n{notes}\nAnswer in exactly two lines.\n"
          "Title: what the person was doing, in 3 to 7 words, starting with a verb ending in -ing "
          "(for example: Updating the outreach tracker). Do not describe the screen.\n"
          "Summary: one sentence of at most 25 words, speaking to the person (\"You were ...\"), saying what they were "
          "doing and, only when the window or their notes show it, what they meant to do next. Never guess a reason.\n"
          "Say only what the window and the notes show.")
_LINE = re.compile(r"^\W*(title|summary)\W*[:\-]\s*(.*)$", re.I)
NOTES_SHOWN, NOTE_CHARS = 3, 140


def readable(item):
    """Whether LMemM stored any text for this window (beyond its app and title)."""
    texts = graph._texts(item)
    return any(texts[f] for f in ("typed", "received", "notes", "read", "screen"))


def note_lines(item):
    """What the person wrote on this thing and has not finished: the newest few notes, shortened."""
    mine = [n["text"] for n in item.get("notes", []) if isinstance(n, dict) and isinstance(n.get("text"), str)
            and not notes.is_done(item, n)]
    return [" ".join(t.split())[:NOTE_CHARS] for t in mine[-NOTES_SHOWN:]]


def source_hash(item):
    return hashlib.sha1((ai.source_text(item) + "\x1f" + "\x1f".join(note_lines(item))).encode("utf-8", "replace")).hexdigest()[:16]


def stale(item):
    """True when the item has text to read and no summary written for that text by the current model."""
    if not readable(item):
        return False
    done = item.get("summary")
    return not (isinstance(done, dict) and done.get("of") == source_hash(item) and done.get("model") == config.FINDER_NARRATOR
                and done.get("v") == VERSION)


def parse(answer):
    """The two lines out of the model's answer: {"title", "summary"}. Without markers the whole answer is
    the title (cut to its first sentence). An "unclear" summary is dropped."""
    found = {}
    for line in re.split(r"\s*(?=\b(?:Title|Summary)\s*:)", answer or ""):
        m = _LINE.match(line.strip())
        if m and m.group(2).strip():
            found.setdefault(m.group(1).lower(), m.group(2).strip())
    if not found and (answer or "").strip():
        found["title"] = re.split(r"(?<=[.!?])\s", answer.strip())[0]
    if re.match(r"^\W*unclear\b", found.get("summary", ""), re.I):
        del found["summary"]
    return found


def summarize(item, narrator):
    if not readable(item):
        return None
    window = ai.window_text(item, READ_CHARS)
    written = note_lines(item)
    notes_text = ("Notes the person wrote here:\n" + "\n".join(f"- {t}" for t in written) + "\n") if written else ""
    answer = narrator(SYSTEM, PROMPT.format(window=window, notes=notes_text), SUMMARY_TOKENS)
    lines = parse(answer)
    haystack = ai.source_text(item) + " " + " ".join(written)
    out = {k: ai.ground(v, haystack)[0] for k, v in lines.items()}
    out = {k: v for k, v in out.items() if v}
    if "title" not in out:
        return None
    return {"title": out["title"].rstrip(".").strip(), "summary": out.get("summary", ""), "of": source_hash(item),
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
