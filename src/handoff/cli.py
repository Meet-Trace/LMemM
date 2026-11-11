"""`lmemm.py handoff ...` - try the workflow without a window.

    handoff demo                      run the whole flow on a built-in synthetic conversation (any OS)
    handoff make FILE [--to NAME]     build the package from a transcript file ("You:" / "Assistant:" turns)
    handoff probe [SECONDS] [--tree]         after SECONDS (default 4) read the app in front and print what it exposes
    handoff panel [--key ctrl+opt+h]  the real hotkey and panel (macOS only, unverified)
"""

import sys

from . import adapters as ad
from . import coordinator as co
from .model import ASSISTANT, COMPLETE, USER, Conversation, Message

DEMO = [
    Message(USER, "I'm designing the sync service for our notes app. It must work offline and we can't use a paid backend."),
    Message(ASSISTANT, "You could use CRDTs, or maybe a simple last-write-wins. Perhaps start with last-write-wins."),
    Message(USER, "Let's go with CRDTs instead of last-write-wins, I don't want lost edits."),
    Message(ASSISTANT, "Done: I implemented the document merge in sync/merge.py. Still need to write the network layer."),
    Message(USER, "Great. Now write the network layer for sync/transport.py. Never block the UI thread."),
]


def demo():
    clip = ad.MemoryClipboard()
    conv = Conversation("fake-claude", "Claude", "Sync service design", COMPLETE, DEMO)
    dest = ad.FakeDestination("fake-cursor", "Cursor")
    c = co.Coordinator([ad.FakeSource(conversation=conv)], [dest], clip,
                       lambda: ad.SourceInfo("Claude", "com.anthropic.claudefordesktop", "Sync service design"))
    s = c.invoke()
    c.capture(s)
    c.choose("fake-cursor")
    print(s.handoff.text)
    c.transfer()
    print("->", s.result.message, f"(composer holds {len(dest.composer)} chars, nothing was sent)")
    print("timings (ms):", s.record.timings_ms)
    return 0


def make(path, dest):
    with open(path, encoding="utf-8") as f:
        msgs = ad.parse_transcript(f.read())
    from .generator import RuleGenerator
    conv = Conversation("file", "Transcript", path, COMPLETE, msgs, verified=False)
    print(RuleGenerator().generate(conv, dest, 24000).text)
    return 0


def probe(delay, tree=False):
    """Switch to the app you want to test; after `delay` seconds print what Accessibility gives us."""
    import time
    from . import mac
    print(f"Switch to the app now... reading in {delay:g}s", flush=True)
    time.sleep(delay)
    info = mac.front_info()
    print("front:", info)
    import macos
    pid = macos.front()["pid"]
    if tree:
        hist, lines = mac.dump_tree(pid)
        print("roles:", dict(sorted(hist.items(), key=lambda kv: -kv[1])))
        print("\n".join(lines))
        return 0
    snap, diag = mac.read_visible(pid)
    print("diagnostic:", diag)
    if snap:
        print(f"title={snap.title!r} url={snap.url!r} texts={len(snap.texts)}")
        for t in snap.texts[:25]:
            print("  |", t[:100])
    return 0


def main(argv):
    if not argv or argv[0] == "demo":
        return demo()
    if argv[0] == "make" and len(argv) >= 2:
        dest = argv[argv.index("--to") + 1] if "--to" in argv else "the next assistant"
        return make(argv[1], dest)
    if argv[0] == "probe":
        return probe(float(argv[1]) if len(argv) > 1 and argv[1][0].isdigit() else 4.0, "--tree" in argv)
    if argv[0] == "panel":
        from . import hotkey, panel
        return panel.run(argv[argv.index("--key") + 1] if "--key" in argv else hotkey.DEFAULT)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
