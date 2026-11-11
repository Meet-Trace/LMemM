"""Integration and end-to-end tests of the workflow against fake adapters. No real accounts,
no model calls, no real clipboard."""

import json
import threading
import time
import unittest

import handoff_helpers as h
from handoff import adapters as ad
from handoff import coordinator as co
from handoff.model import DENIED, FAILED, PARTIAL, UNSUPPORTED


class FlowTests(unittest.TestCase):
    def test_end_to_end_exact_text_reaches_the_composer_and_nothing_is_sent(self):      # AC-07, AC-08
        c, clip, (cursor, _gpt) = h.build()
        s = h.ready(c)
        c.choose("fake-cursor")
        expected = s.handoff.text
        c.transfer()
        self.assertEqual(s.state, co.DONE)
        self.assertEqual((s.result.ok, s.result.method), (True, "inserted"))
        self.assertEqual(cursor.composer, expected)
        self.assertEqual(clip.writes, 0)                       # insertion path does not touch the clipboard
        self.assertEqual(cursor.insert_calls, 1)
        self.assertEqual(s.result.message, "Context ready in Cursor.")

    def test_clipboard_only_destination_is_labelled_as_such(self):                      # AC-07
        c, clip, (_cursor, gpt) = h.build()
        s = h.ready(c)
        c.choose("fake-chatgpt")
        c.transfer()
        self.assertEqual((s.result.ok, s.result.method), (True, "clipboard"))
        self.assertEqual(clip.text, s.handoff.text)
        self.assertEqual(gpt.insert_calls, 0)
        self.assertEqual(gpt.composer, "")
        self.assertEqual(s.result.message, "Context copied. Paste it into ChatGPT to continue.")

    def test_insert_that_cannot_be_verified_falls_back_to_clipboard(self):
        d = ad.FakeDestination("fake-cursor", "Cursor", insert_ok=False)
        c, clip, _ = h.build(dests=[d])
        s = h.ready(c)
        c.choose("fake-cursor")
        c.transfer()
        self.assertEqual(s.result.method, "clipboard")
        self.assertTrue(s.result.ok)
        self.assertNotEqual(s.result.method, "inserted")
        self.assertEqual(clip.text, s.handoff.text)

    def test_insert_raising_is_a_clipboard_fallback_not_a_crash(self):
        d = ad.FakeDestination("fake-cursor", "Cursor", raises_on_insert=RuntimeError("boom"))
        c, clip, _ = h.build(dests=[d])
        s = h.ready(c)
        c.choose("fake-cursor")
        c.transfer()
        self.assertEqual(s.result.method, "clipboard")

    def test_opening_the_destination_is_not_success(self):
        d = ad.FakeDestination("fake-cursor", "Cursor", can_insert=False, open_ok=True)
        c, clip, _ = h.build(dests=[d])
        s = h.ready(c)
        c.choose("fake-cursor")
        c.transfer()
        self.assertNotEqual(s.result.method, "inserted")
        self.assertEqual(clip.text, s.handoff.text)

    def test_failed_destination_preserves_the_handoff(self):                            # AC-09
        d = ad.FakeDestination("fake-cursor", "Cursor", is_available=False)
        clip = ad.MemoryClipboard()
        clip.write = lambda text: False                        # the clipboard fails too
        c, _, _ = h.build(dests=[d], clipboard=clip)
        s = h.ready(c)
        c.choose("fake-cursor")
        text = s.handoff.text
        c.transfer()
        self.assertEqual(s.state, co.FAILED_STATE)
        self.assertFalse(s.result.ok)
        self.assertEqual(s.result.message, "Couldn't transfer the context to Cursor. Your handoff is still available to copy.")
        self.assertEqual(s.handoff.text, text)
        self.assertEqual(s.text, text)

    def test_unavailable_destination_still_copies_when_it_can(self):
        d = ad.FakeDestination("fake-cursor", "Cursor", is_available=False)
        c, clip, _ = h.build(dests=[d])
        s = h.ready(c)
        c.choose("fake-cursor")
        c.transfer()
        self.assertEqual(clip.text, s.handoff.text)
        self.assertFalse(s.result.ok)                           # the transfer to Cursor did not happen
        self.assertEqual(s.result.method, "clipboard")

    def test_cancel_transfers_nothing(self):                                            # AC-11
        c, clip, (cursor, gpt) = h.build()
        s = h.ready(c)
        c.choose("fake-cursor")
        c.cancel()
        c.transfer()                                            # a late press after cancel must do nothing
        self.assertEqual(s.state, co.CANCELLED)
        self.assertEqual((cursor.opens, cursor.insert_calls, gpt.opens, clip.writes), (0, 0, 0, 0))

    def test_edits_change_the_outgoing_text_only(self):
        c, clip, (cursor, _) = h.build()
        s = h.ready(c)
        c.choose("fake-cursor")
        original = s.handoff.text
        c.edit("my edited handoff")
        c.transfer()
        self.assertEqual(cursor.composer, "my edited handoff")
        self.assertEqual(s.handoff.text, original)
        self.assertEqual(s.conversation.messages, h.conv(h.SYNC).messages)

    def test_back_to_destinations_and_choose_again(self):
        c, _, (cursor, gpt) = h.build()
        s = h.ready(c)
        c.choose("fake-cursor")
        c.back_to_destinations()
        self.assertEqual(s.state, co.READY)
        c.choose("fake-chatgpt")
        self.assertEqual(s.destination.id, "fake-chatgpt")

    def test_repeated_invocation_reuses_one_session(self):                              # FR-1
        c, _, _ = h.build()
        s1 = c.invoke()
        self.assertIs(c.invoke(), s1)
        c.capture(s1)
        self.assertIs(c.invoke(), s1)
        c.cancel()
        self.assertIsNot(c.invoke(), s1)

    def test_source_is_not_offered_as_a_destination(self):
        dests = [ad.FakeDestination("fake-claude", "Claude"), ad.FakeDestination("fake-cursor", "Cursor")]
        c, _, _ = h.build(dests=dests)
        h.ready(c)
        self.assertEqual([d["id"] for d in c.destination_list()], ["fake-cursor"])

    def test_recent_destinations_come_first(self):
        c, _, _ = h.build()
        h.ready(c)
        self.assertEqual(c.destination_list()[0]["name"], "ChatGPT")          # alphabetical before any use
        c.choose("fake-cursor")
        c.transfer()
        s = h.ready(c)
        self.assertEqual(c.destination_list()[0]["name"], "Cursor")


class SourceProblemTests(unittest.TestCase):
    def test_unsupported_source_gives_a_clear_fallback(self):                            # AC-05
        c, _, _ = h.build()
        c.front = lambda: ad.SourceInfo("Notes", "com.apple.Notes")
        s = h.ready(c)
        self.assertIsNone(s.source)
        self.assertEqual(s.conversation.status, UNSUPPORTED)
        self.assertIn("couldn't identify a supported conversation", s.fallback)
        self.assertIn("Copy the conversation", s.fallback)
        self.assertIsNone(c.choose("fake-cursor").handoff)                 # nothing is fabricated

    def test_permission_denied_is_explained(self):
        src = ad.FakeSource(status=DENIED, reason="Accessibility access is off for LMemM.")
        c, _, _ = h.build(source=src)
        s = h.ready(c)
        self.assertIn("Accessibility access is off", s.fallback)
        c.choose("fake-cursor")
        self.assertIsNone(s.handoff)

    def test_source_that_raises_is_a_failure_not_a_crash(self):
        c, _, _ = h.build(source=ad.FakeSource(raises=RuntimeError("ax exploded")))
        s = h.ready(c)
        self.assertEqual(s.conversation.status, FAILED)
        self.assertNotIn("ax exploded", s.fallback)                         # no internals in the UI text

    def test_slow_source_times_out(self):
        stop = threading.Event()
        src = ad.FakeSource(delay=lambda cancelled: stop.wait(2))
        c, _, _ = h.build(source=src, extract_timeout=0.05)
        t0 = time.monotonic()
        s = h.ready(c)
        stop.set()
        self.assertLess(time.monotonic() - t0, 1)
        self.assertIn("took too long", s.fallback)

    def test_cancel_during_capture_is_honoured(self):
        seen = {}
        src = ad.FakeSource(conversation=h.conv(h.SYNC), delay=lambda cancelled: seen.update(c=cancelled()))
        c, _, _ = h.build(source=src)
        s = c.invoke()
        c.cancel(s)
        c.capture(s)
        self.assertEqual(s.state, co.CANCELLED)

    def test_private_app_is_never_read(self):
        src = ad.FakeSource(conversation=h.conv(h.SYNC))
        c, _, _ = h.build(source=src, is_private=lambda info: True)
        s = h.ready(c)
        self.assertEqual(src.calls, 0)
        self.assertIn("private", s.fallback)

    def test_manual_clipboard_fallback_completes_the_flow(self):
        c, clip, (cursor, _) = h.build()
        c.front = lambda: ad.SourceInfo("Notes", "com.apple.Notes")
        s = h.ready(c)
        manual = ad.ClipboardSource(ad.MemoryClipboard(
            "You: Build a login form. It must support keyboard navigation.\nAssistant: Done: markup finished."))
        c.use_manual(manual)
        self.assertTrue(s.conversation.readable)
        c.choose("fake-cursor")
        c.transfer()
        self.assertIn("keyboard navigation", cursor.composer)
        self.assertTrue(any("pasted or inferred" in n for n in s.handoff.notes))

    def test_partial_extraction_reaches_the_handoff(self):                              # AC-10
        c, _, _ = h.build(status=PARTIAL)
        s = h.ready(c)
        c.choose("fake-cursor")
        self.assertTrue(s.handoff.partial)


class ObservabilityTests(unittest.TestCase):
    def test_record_has_every_stage_and_no_conversation_text(self):                     # FR-6, AC-14
        records = []
        c, _, _ = h.build(log=records.append)
        s = h.ready(c)
        c.choose("fake-cursor")
        c.transfer()
        r = records[-1]
        self.assertEqual((r.source_detection, r.extraction, r.generation, r.destination, r.transfer_method, r.final),
                         ("fake-claude", "complete", "ok", "fake-cursor", "inserted", "ok"))
        self.assertEqual(set(r.timings_ms), {"detect", "extract", "generate", "transfer"})
        blob = json.dumps(r.__dict__, default=str)
        for m in h.SYNC:
            self.assertNotIn(m.text[:25], blob)
        self.assertNotIn("CRDT", blob)

    def test_failure_reason_is_recorded(self):
        records = []
        d = ad.FakeDestination("fake-cursor", "Cursor", is_available=False)
        clip = ad.MemoryClipboard()
        clip.write = lambda t: False
        c, _, _ = h.build(dests=[d], clipboard=clip, log=records.append)
        h.ready(c)
        c.choose("fake-cursor")
        c.transfer()
        self.assertEqual(records[-1].final, "failed")
        self.assertIn("not installed", records[-1].failure)

    def test_stages_are_timed_with_the_injected_clock(self):
        ticks = iter(range(0, 1000, 10))
        c, _, _ = h.build(clock=lambda: next(ticks) / 1000)
        h.ready(c)
        self.assertGreater(c.session.record.timings_ms["extract"], 0)

    def test_clear_drops_the_session(self):
        c, _, _ = h.build()
        h.ready(c)
        c.clear()
        self.assertIsNone(c.session)

    def test_the_coordinator_cannot_send_by_construction(self):                         # AC-08
        for name in ("send", "submit", "press_return"):
            self.assertFalse(hasattr(co.Coordinator, name))



class PerformanceTests(unittest.TestCase):
    def test_preview_from_a_long_conversation_is_fast(self):                             # performance target: < 2 s
        msgs = []
        for i in range(300):
            msgs += [h.U(f"Step {i}: the module must keep step {i} idempotent."), h.A(f"Done: step {i} finished in mod{i}.py.")]
        c, _, _ = h.build(messages=msgs)
        s = h.ready(c)
        c.choose("fake-cursor")
        self.assertLess(s.record.timings_ms["generate"], 2000)


if __name__ == "__main__":
    unittest.main()
