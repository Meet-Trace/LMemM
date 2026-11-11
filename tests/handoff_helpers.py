"""Shared fixtures for the handoff tests: synthetic conversations only, no real accounts."""

from handoff import adapters as ad
from handoff import coordinator as co
from handoff.model import ASSISTANT, COMPLETE, PARTIAL, UNKNOWN, USER, Conversation, Message

U = lambda t: Message(USER, t)
A = lambda t: Message(ASSISTANT, t)


def conv(messages, status=COMPLETE, **kw):
    kw.setdefault("title", "Sync service design")
    kw.setdefault("app", "Claude")
    return Conversation("fake-claude", status=status, messages=list(messages), **kw)


SYNC = [
    U("I'm designing the sync service for our notes app. It must work offline and we can't use a paid backend."),
    A("You could use CRDTs, or maybe a simple last-write-wins. Perhaps start with last-write-wins."),
    U("Let's go with CRDTs instead of last-write-wins, I don't want lost edits."),
    A("Done: I implemented the document merge in sync/merge.py. Still need to write the network layer."),
    U("Great. Now write the network layer for sync/transport.py. Never block the UI thread."),
]

CLAUDE = ad.SourceInfo("Claude", "com.anthropic.claudefordesktop", "Sync service design")


def build(messages=None, status=COMPLETE, dests=None, source=None, clipboard=None, **kw):
    """A coordinator wired to fakes. Returns (coordinator, clipboard, destinations)."""
    clip = clipboard or ad.MemoryClipboard()
    src = source or ad.FakeSource(conversation=conv(SYNC if messages is None else messages, status))
    dests = dests or [ad.FakeDestination("fake-cursor", "Cursor"), ad.FakeDestination("fake-chatgpt", "ChatGPT", can_insert=False)]
    c = co.Coordinator([src], dests, clip, lambda: CLAUDE, **kw)
    return c, clip, dests


def ready(c):
    s = c.invoke()
    c.capture(s)
    return s
