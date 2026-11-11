"""LMemM - Universal AI Handoff: move ongoing work from one AI assistant to another.

One workflow: capture the conversation in front, build a short task-oriented package, hand it
to a destination assistant's composer or the clipboard. The user always presses send.

Everything here except `mac.py`, `hotkey.py` and `panel.py` is plain Python with no macOS
imports, so the whole workflow is tested on any OS with fake adapters.
"""
