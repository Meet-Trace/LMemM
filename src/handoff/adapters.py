"""Source and destination adapter contracts, plus the fakes the tests and `handoff demo` use.

Both contracts are tiny on purpose. A destination adapter has no `send`/`submit` method at all:
the product never submits a prompt, and the interface makes that structural, not a promise.
"""

import re
from dataclasses import dataclass

from .model import (ASSISTANT, COMPLETE, DENIED, EMPTY, FAILED, UNKNOWN, UNSUPPORTED, USER, Conversation, Message)

# how a destination can receive the package
INSERT, CLIPBOARD = "insert", "clipboard"


@dataclass(frozen=True)
class SourceInfo:
    """Who is in front. `app` is the display name; `bundle_id` the stable id."""
    app: str = ""
    bundle_id: str = ""
    window: str = ""
    url: str = ""


@dataclass(frozen=True)
class Capabilities:
    """What an integration can do, declared by the adapter, never assumed by the UI."""
    can_open: bool = True
    can_insert: bool = False         # put text in the composer (without sending)
    can_new_conversation: bool = False
    notes: str = ""                  # known limitations, shown in docs and the compatibility matrix


class SourceAdapter:
    """Reads the conversation of one kind of app."""
    id = "source"
    name = "Source"
    capabilities = Capabilities()

    def matches(self, info):
        """True when `info` (a SourceInfo) is this adapter's app."""
        raise NotImplementedError

    def extract(self, info, cancelled=lambda: False):
        """A Conversation. Never raises for expected failures: report them in `status`/`reason`."""
        raise NotImplementedError


class DestinationAdapter:
    """Opens one assistant and hands it the package."""
    id = "dest"
    name = "Destination"
    capabilities = Capabilities()

    def available(self):
        """(ok, reason)."""
        raise NotImplementedError

    def open(self):
        """Open or focus the app. True on success. Does not claim the context arrived."""
        raise NotImplementedError

    def insert(self, text):
        """Put `text` in the composer without sending. True only when it verifiably landed."""
        raise NotImplementedError


class Clipboard:
    """The pasteboard, behind an interface so tests never touch the real one."""
    def write(self, text):
        raise NotImplementedError

    def read(self):
        raise NotImplementedError


class MemoryClipboard(Clipboard):
    def __init__(self, text=""):
        self.text = text
        self.writes = 0

    def write(self, text):
        self.text, self.writes = text, self.writes + 1
        return True

    def read(self):
        return self.text


# ---------------------------------------------------------------- manual-context fallback

_TURN = re.compile(r"^\s*(you|user|me|human|assistant|claude|chatgpt|gpt|gemini|ai|cursor)\s*[:>]\s*(.*)$", re.I)
_USER_NAMES = {"you", "user", "me", "human"}


def parse_transcript(text):
    """Split pasted text on 'You:' / 'Assistant:' style turns. Text with no turn markers is one
    message of unknown speaker, never guessed."""
    msgs, role, buf = [], None, []

    def flush():
        if role is not None and "".join(buf).strip():
            msgs.append(Message(role, "\n".join(buf).strip()))
    for line in (text or "").splitlines():
        m = _TURN.match(line)
        if m:
            flush()
            role = USER if m.group(1).lower() in _USER_NAMES else ASSISTANT
            buf = [m.group(2)]
        else:
            if role is None and line.strip():
                role = UNKNOWN
            buf.append(line)
    flush()
    return msgs


class ClipboardSource(SourceAdapter):
    """Manual-context fallback: the user copied the conversation. Matches any app, last in the
    registry, and always says the content was pasted (verified=False)."""
    id = "clipboard"
    name = "Copied text"
    capabilities = Capabilities(can_open=False, notes="Reads the clipboard; works for any app.")

    def __init__(self, clipboard):
        self.clipboard = clipboard

    def matches(self, info):
        return True

    def extract(self, info, cancelled=lambda: False):
        msgs = parse_transcript(self.clipboard.read())
        if not msgs:
            return Conversation(self.id, info.app, status=EMPTY, verified=False,
                                reason="Copy the conversation first, then try again.")
        return Conversation(self.id, info.app or "Copied text", title=info.window, status=COMPLETE,
                            messages=msgs, verified=False)


# ---------------------------------------------------------------- fakes

class FakeSource(SourceAdapter):
    def __init__(self, id="fake-claude", name="Claude", bundle_ids=("com.anthropic.claudefordesktop",),
                 conversation=None, status=None, reason="", delay=None, raises=None):
        self.id, self.name, self.bundle_ids = id, name, set(bundle_ids)
        self.conversation, self.status, self.reason = conversation, status, reason
        self.delay, self.raises = delay, raises
        self.calls = 0

    def matches(self, info):
        return info.bundle_id in self.bundle_ids

    def extract(self, info, cancelled=lambda: False):
        self.calls += 1
        if self.delay:
            self.delay(cancelled)
        if self.raises:
            raise self.raises
        if self.status in (DENIED, UNSUPPORTED, FAILED, EMPTY):
            return Conversation(self.id, self.name, status=self.status, reason=self.reason)
        conv = self.conversation
        if conv is None:
            return Conversation(self.id, self.name, status=EMPTY, reason="No conversation found.")
        return conv


class FakeDestination(DestinationAdapter):
    """A composer that records what was inserted. There is no way to 'send' to it."""
    def __init__(self, id="fake-cursor", name="Cursor", can_insert=True, is_available=True,
                 open_ok=True, insert_ok=True, raises_on_insert=None):
        self.id, self.name = id, name
        self.capabilities = Capabilities(can_open=True, can_insert=can_insert)
        self._available, self.open_ok, self.insert_ok = is_available, open_ok, insert_ok
        self.raises_on_insert = raises_on_insert
        self.composer = ""
        self.opens = 0
        self.insert_calls = 0

    def available(self):
        return (True, "") if self._available else (False, f"{self.name} is not installed.")

    def open(self):
        self.opens += 1
        return self.open_ok

    def insert(self, text):
        self.insert_calls += 1
        if self.raises_on_insert:
            raise self.raises_on_insert
        if not self.insert_ok:
            return False
        self.composer = text
        return True
