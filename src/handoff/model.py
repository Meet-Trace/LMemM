"""The data the handoff workflow passes around."""

from dataclasses import dataclass, field

# who said it. "unknown" is text read off the screen that could not be attributed to a speaker.
USER, ASSISTANT, UNKNOWN = "user", "assistant", "unknown"
ROLES = {USER, ASSISTANT, UNKNOWN}

# how much of the conversation the source could read
COMPLETE, PARTIAL, EMPTY, DENIED, UNSUPPORTED, FAILED = (
    "complete", "partial", "empty", "denied", "unsupported", "failed")
READABLE = {COMPLETE, PARTIAL}


@dataclass(frozen=True)
class Message:
    role: str
    text: str
    ts: str = ""                    # ISO time, when the source has one


@dataclass
class Conversation:
    """What a source adapter read. `status` says how much of it; nothing is implied beyond it."""
    source: str                     # adapter id, e.g. "claude"
    app: str = ""                   # display name of the source app
    title: str = ""                 # conversation name, when identifiable
    status: str = EMPTY
    messages: list = field(default_factory=list)
    reason: str = ""                # why it is not complete / not readable
    verified: bool = True           # False: inferred (e.g. pasted text), not read from the app
    artifacts: list = field(default_factory=list)       # files or repos reliably tied to it

    @property
    def readable(self):
        return self.status in READABLE and bool(self.messages)


@dataclass(frozen=True)
class Item:
    """One line of the handoff plus where it came from: indexes into Conversation.messages."""
    text: str
    refs: tuple = ()
    confirmed: bool = True          # False: from an unattributed or speculative source


@dataclass
class Handoff:
    task: str = ""
    current_state: str = ""
    context: list = field(default_factory=list)         # constraints and facts
    decisions: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    completed: list = field(default_factory=list)
    outstanding: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    next_action: str = ""
    artifacts: list = field(default_factory=list)
    source_app: str = ""
    source_title: str = ""
    destination: str = ""
    messages_used: int = 0
    messages_total: int = 0
    partial: bool = False           # built from part of the source conversation
    notes: list = field(default_factory=list)           # material limitations shown to the user
    redactions: int = 0
    truncated: bool = False
    text: str = ""                  # the rendered package; what the user may edit and what is sent
