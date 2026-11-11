"""UI behaviour through the panel's view model: what is shown, and what every key does."""

import unittest

import handoff_helpers as h
from handoff import adapters as ad
from handoff import coordinator as co
from handoff.model import DENIED, PARTIAL
from handoff.panel_model import DOWN, ENTER, ESC, KEY_C, KEY_R, RETURN, UP, PanelModel


def open_panel(**kw):
    c, clip, dests = h.build(**kw)
    p = PanelModel(c)
    return c, p, clip, dests


class PanelTests(unittest.TestCase):
    def test_hotkey_opens_the_panel_in_a_loading_state_then_the_source_view(self):     # AC-01
        c, p, _, _ = open_panel()
        self.assertEqual(p.view()["kind"], "closed")
        s = c.invoke()
        v = p.view()
        self.assertEqual((v["kind"], v["title"], v["source"]), ("loading", "Preparing handoff", "Claude"))
        c.capture(s)
        v = p.view()
        self.assertEqual(v["kind"], "source")
        self.assertEqual((v["app"], v["conversation"], v["context_status"]), ("Claude", "Sync service design", "Retrieved"))
        self.assertEqual([d["name"] for d in v["destinations"]], ["ChatGPT", "Cursor"])

    def test_keyboard_only_flow_from_source_to_transfer(self):                          # AC-06
        c, p, _, (cursor, _) = open_panel()
        h.ready(c)
        self.assertTrue(p.press(DOWN))                       # ChatGPT -> Cursor
        self.assertEqual(p.view()["selected"], 1)
        self.assertTrue(p.press(RETURN))
        v = p.view()
        self.assertEqual((v["kind"], v["destination"], v["primary"]), ("confirm", "Cursor", "Continue in Cursor"))
        self.assertEqual(cursor.composer, "")                # nothing transferred yet
        self.assertTrue(p.press(ENTER))
        self.assertEqual(p.view()["kind"], "result")
        self.assertEqual(cursor.composer, c.session.handoff.text)

    def test_selection_stops_at_both_ends(self):
        c, p, _, _ = open_panel()
        h.ready(c)
        for _ in range(5):
            p.press(DOWN)
        self.assertEqual(p.view()["selected"], 1)
        for _ in range(5):
            p.press(UP)
        self.assertEqual(p.view()["selected"], 0)

    def test_escape_dismisses_and_transfers_nothing(self):                              # AC-11
        c, p, clip, (cursor, _) = open_panel()
        h.ready(c)
        p.press(DOWN)
        p.press(RETURN)
        self.assertTrue(p.press(ESC))
        self.assertEqual(p.view()["kind"], "closed")
        self.assertEqual((cursor.opens, cursor.insert_calls, clip.writes), (0, 0, 0))

    def test_escape_while_loading_cancels(self):
        c, p, _, _ = open_panel()
        c.invoke()
        p.press(ESC)
        self.assertEqual(p.view()["kind"], "closed")

    def test_escape_in_review_goes_back_first(self):
        c, p, _, _ = open_panel()
        h.ready(c)
        p.press(RETURN)
        p.press(KEY_R)
        self.assertEqual(p.view()["kind"], "review")
        p.press(ESC)
        self.assertEqual(p.view()["kind"], "confirm")
        p.press(ESC)
        self.assertEqual(p.view()["kind"], "closed")

    def test_confirm_view_is_short_and_states_the_method(self):
        c, p, _, _ = open_panel()
        h.ready(c)
        p.press(RETURN)                                      # ChatGPT: clipboard-only
        v = p.view()
        self.assertFalse(v["inserts"])
        self.assertLessEqual(len(v["task"]), 140)
        self.assertEqual((v["messages"], v["secondary"]), (5, "Review context"))

    def test_edits_in_review_apply_to_the_outgoing_text_only(self):
        c, p, _, (cursor, _) = open_panel()
        h.ready(c)
        p.press(DOWN); p.press(RETURN); p.press(KEY_R)
        self.assertEqual(p.view()["text"], c.session.handoff.text)
        c.edit("trimmed")
        self.assertEqual(p.view()["text"], "trimmed")
        self.assertNotEqual(c.session.handoff.text, "trimmed")
        p.back()
        self.assertEqual(p.view()["kind"], "source")
        p.press(RETURN)                                      # choosing again starts from a fresh package
        self.assertEqual(c.session.text, c.session.handoff.text)

    def test_copy_key_copies_without_transferring(self):
        c, p, clip, (cursor, _) = open_panel()
        h.ready(c)
        p.press(DOWN); p.press(RETURN); p.press(KEY_C)
        self.assertEqual(clip.text, c.session.handoff.text)
        self.assertEqual(cursor.insert_calls, 0)

    def test_unsupported_source_shows_fallback_and_choosing_does_nothing(self):          # AC-05
        c, p, _, _ = open_panel()
        c.front = lambda: ad.SourceInfo("Notes", "com.apple.Notes")
        h.ready(c)
        v = p.view()
        self.assertFalse(v["supported"])
        self.assertIn("couldn't identify a supported conversation", v["fallback"])
        self.assertEqual(v["context_status"], "Not retrieved")
        p.press(RETURN)
        self.assertEqual(p.view()["kind"], "source")

    def test_unreadable_source_can_use_copied_text_by_key(self):
        c, p, clip, (cursor, _) = open_panel()
        c.front = lambda: ad.SourceInfo("Notes", "com.apple.Notes")
        h.ready(c)
        clip.text = "You: Build a login form. It must support keyboard navigation.\nAssistant: Done: markup finished."
        self.assertIn("manual", p.view())
        p.press(KEY_C)
        v = p.view()
        self.assertTrue(v["readable"])
        self.assertEqual(v["context_status"], "Retrieved")
        p.press(DOWN); p.press(RETURN)
        self.assertIn("keyboard navigation", c.session.handoff.text)
        self.assertEqual(cursor.composer, "")                 # still waiting for Continue

    def test_empty_clipboard_keeps_the_fallback_message(self):
        c, p, _, _ = open_panel()
        c.front = lambda: ad.SourceInfo("Notes", "com.apple.Notes")
        h.ready(c)
        p.press(KEY_C)
        self.assertFalse(p.view()["readable"])
        self.assertIn("Copy the conversation", p.view()["fallback"])

    def test_partial_context_is_shown_on_source_and_confirm_views(self):               # AC-10 (UI)
        c, p, _, _ = open_panel(status=PARTIAL)
        h.ready(c)
        self.assertTrue(p.view()["context_status"].startswith("Partial"))
        p.press(RETURN)
        v = p.view()
        self.assertTrue(v["partial"])
        self.assertTrue(any("partial" in n for n in v["notes"]))

    def test_permission_denied_error_state(self):
        c, p, _, _ = open_panel(source=ad.FakeSource(status=DENIED, reason="Accessibility access is off for LMemM."))
        h.ready(c)
        self.assertIn("Accessibility access is off", p.view()["fallback"])

    def test_unavailable_destination_cannot_be_chosen(self):
        d = [ad.FakeDestination("fake-cursor", "Cursor", is_available=False)]
        c, p, _, _ = open_panel(dests=d)
        h.ready(c)
        self.assertFalse(p.view()["destinations"][0]["available"])
        p.press(RETURN)
        self.assertEqual(p.view()["kind"], "source")

    def test_failure_view_keeps_the_handoff_copyable(self):
        d = [ad.FakeDestination("fake-cursor", "Cursor", is_available=False)]
        clip = ad.MemoryClipboard()
        clip.write = lambda t: False
        c, p, _, _ = open_panel(dests=d, clipboard=clip)
        h.ready(c)
        c.choose("fake-cursor"); c.transfer()
        v = p.view()
        self.assertEqual((v["kind"], v["ok"], v["can_copy"]), ("result", False, True))
        self.assertTrue(v["text"])

    def test_repeated_invocation_does_not_reset_the_open_panel(self):                  # FR-1
        c, p, _, _ = open_panel()
        h.ready(c)
        p.press(DOWN)
        c.invoke()
        self.assertEqual(p.view()["selected"], 1)

    def test_long_titles_are_shortened(self):
        c, p, _, _ = open_panel(source=ad.FakeSource(conversation=h.conv(h.SYNC, title="A" * 300)))
        h.ready(c)
        self.assertLessEqual(len(p.view()["conversation"]), 48)

    def test_very_long_handoff_text_is_bounded_and_whole_in_review(self):
        msgs = [h.U(f"Rule {i}: the service must never call module {i} twice in one request.") for i in range(500)]
        msgs.append(h.U("Please audit the call graph."))
        c, p, _, _ = open_panel(messages=msgs)
        h.ready(c)
        p.press(RETURN); p.press(KEY_R)
        v = p.view()
        self.assertEqual(v["kind"], "review")
        self.assertLessEqual(len(v["text"]), co.MAX_CHARS["chatgpt"])

    def test_keys_do_nothing_when_closed(self):
        c, p, _, _ = open_panel()
        self.assertFalse(p.press(RETURN))


if __name__ == "__main__":
    unittest.main()
