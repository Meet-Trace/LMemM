"""What the handoff panel shows and how keys move it. Pure functions of the coordinator's session,
so every state and key is tested without a window.

Views (spec states A-F): loading, source, confirm, review, result.
"""

from . import adapters as ad
from . import coordinator as co
from .model import PARTIAL

ESC, RETURN, ENTER, UP, DOWN, TAB = 53, 36, 76, 126, 125, 48
KEY_R, KEY_C = 15, 8                       # R: review, C: copy (from the confirm view)
MAX_TITLE = 48


def _short(t, n=MAX_TITLE):
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


class PanelModel:
    def __init__(self, coordinator):
        self.co = coordinator
        self.selected = 0                  # row in the destination list
        self.reviewing = False

    @property
    def session(self):
        return self.co.session

    # ---------------------------------------------------------------- view

    def view(self):
        s = self.session
        if s is None or s.state == co.IDLE:
            return {"kind": "closed"}
        if s.state == co.CAPTURING:
            return {"kind": "loading", "title": "Preparing handoff",
                    "source": s.info.app or "", "can_cancel": True}
        if s.state == co.CANCELLED:
            return {"kind": "closed"}
        if s.state in (co.DONE, co.FAILED_STATE):
            r = s.result
            return {"kind": "result", "ok": r.ok, "message": r.message, "method": r.method,
                    "can_copy": bool(s.handoff), "text": s.text}
        if s.state in (co.PREPARED, co.TRANSFERRING):
            h = s.handoff
            if self.reviewing:
                return {"kind": "review", "text": s.text, "sources": h.messages_used,
                        "notes": list(h.notes), "destination": s.destination.name}
            return {"kind": "confirm", "destination": s.destination.name, "task": _short(h.task, 140),
                    "messages": h.messages_used, "notes": list(h.notes), "partial": h.partial,
                    "primary": f"Continue in {s.destination.name}", "secondary": "Review context",
                    "busy": s.state == co.TRANSFERRING,
                    "inserts": s.destination.capabilities.can_insert}
        c = s.conversation
        rows = self.co.destination_list()
        v = {"kind": "source", "app": s.info.app, "supported": s.source is not None,
             "conversation": _short(c.title) if c else "", "destinations": rows,
             "selected": min(self.selected, max(len(rows) - 1, 0)), "fallback": s.fallback,
             "manual": "Use copied text (copy the conversation first)"}
        if c is not None:
            v["context_status"] = ("Retrieved" if c.status == "complete" else
                                   "Partial: " + (c.reason or "not all of the conversation could be read")
                                   if c.status == PARTIAL else "Not retrieved")
            v["readable"] = c.readable
        return v

    # ---------------------------------------------------------------- keys

    def press(self, code):
        """Handle a key. Returns True when the panel handled it."""
        s = self.session
        if s is None:
            return False
        if code == ESC:
            if self.reviewing:
                self.reviewing = False
            else:
                self.co.cancel()
            return True
        v = self.view()
        if v["kind"] == "source":
            rows = v["destinations"]
            if code == DOWN and rows:
                self.selected = min(v["selected"] + 1, len(rows) - 1)
            elif code == UP and rows:
                self.selected = max(v["selected"] - 1, 0)
            elif code == KEY_C and not v.get("readable"):
                self.use_clipboard()
            elif code in (RETURN, ENTER) and rows and v.get("readable") and rows[v["selected"]]["available"]:
                self.co.choose(rows[v["selected"]]["id"])
            else:
                return code in (DOWN, UP, RETURN, ENTER)
            return True
        if v["kind"] == "confirm":
            if code in (RETURN, ENTER) and not v["busy"]:
                self.co.transfer()
            elif code == KEY_R:
                self.reviewing = True
            elif code == KEY_C:
                self.co.copy()
            else:
                return False
            return True
        return False

    def use_clipboard(self):
        """Manual fallback: build the handoff from the conversation the user copied."""
        s = self.session
        if s is not None and s.state == co.READY:
            self.selected = 0
            self.co.use_manual(ad.ClipboardSource(self.co.clipboard), s)

    def choose_row(self, index):
        rows = self.co.destination_list()
        if 0 <= index < len(rows) and rows[index]["available"]:
            self.selected = index
            self.co.choose(rows[index]["id"])

    def review(self):
        self.reviewing = True

    def back(self):
        self.reviewing = False
        self.co.back_to_destinations()
