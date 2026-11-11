"""Contract tests: what every source and destination adapter owes the coordinator.
The same checks run against the fakes and the clipboard source; a new integration adds its own
subclass here (with a fake backend) before it is advertised."""

import time
import unittest

import handoff_helpers as h
from handoff import adapters as ad
from handoff import coordinator as co
from handoff import hotkey, mac as mac_rules
from handoff.model import COMPLETE, DENIED, EMPTY, FAILED, UNKNOWN, UNSUPPORTED, USER


class SourceContract:
    """Mixin: subclasses supply make_ok(), info_ok, info_other."""

    def test_identifies_its_application(self):                                      # AC-02
        a = self.make_ok()
        self.assertTrue(a.matches(self.info_ok))
        self.assertFalse(a.matches(self.info_other))

    def test_successful_extraction_has_correct_metadata(self):
        c = self.make_ok().extract(self.info_ok)
        self.assertEqual(c.status, COMPLETE)
        self.assertTrue(c.readable)
        self.assertTrue(c.source)
        self.assertTrue(all(m.role in ("user", "assistant", "unknown") for m in c.messages))

    def test_declares_capabilities(self):
        self.assertIsInstance(self.make_ok().capabilities, ad.Capabilities)


class FakeSourceContract(SourceContract, unittest.TestCase):
    info_ok = h.CLAUDE
    info_other = ad.SourceInfo("Notes", "com.apple.Notes")

    def make_ok(self):
        return ad.FakeSource(conversation=h.conv(h.SYNC))

    def test_failure_statuses_are_reported_not_raised(self):
        for status in (EMPTY, DENIED, UNSUPPORTED, FAILED):
            c = ad.FakeSource(status=status, reason="why").extract(h.CLAUDE)
            self.assertEqual((c.status, c.readable, c.reason), (status, False, "why"))


class ClipboardSourceContract(SourceContract, unittest.TestCase):
    info_ok = ad.SourceInfo("Anything", "x.y")
    info_other = None

    def make_ok(self):
        return ad.ClipboardSource(ad.MemoryClipboard("You: Please write a parser.\nAssistant: Sure."))

    def test_identifies_its_application(self):                                      # matches every app, by design
        self.assertTrue(self.make_ok().matches(self.info_ok))

    def test_is_marked_unverified(self):
        self.assertFalse(self.make_ok().extract(self.info_ok).verified)

    def test_empty_clipboard_is_reported(self):
        c = ad.ClipboardSource(ad.MemoryClipboard("   ")).extract(self.info_ok)
        self.assertEqual(c.status, EMPTY)
        self.assertTrue(c.reason)

    def test_text_without_turn_markers_is_one_unattributed_message(self):
        c = ad.ClipboardSource(ad.MemoryClipboard("just some notes\nmore notes")).extract(self.info_ok)
        self.assertEqual([m.role for m in c.messages], [UNKNOWN])

    def test_turn_parsing(self):
        msgs = ad.parse_transcript("You: one\ncontinued\nClaude: two\nUser: three")
        self.assertEqual([(m.role, m.text) for m in msgs],
                         [("user", "one\ncontinued"), ("assistant", "two"), ("user", "three")])


class DestinationContract:
    def test_reports_capabilities(self):
        self.assertIsInstance(self.dest().capabilities, ad.Capabilities)

    def test_availability_open_and_insert(self):
        d = self.dest()
        self.assertEqual(d.available()[0], True)
        self.assertTrue(d.open())
        if d.capabilities.can_insert:
            self.assertTrue(d.insert("hello"))

    def test_has_no_way_to_send(self):                                              # AC-08, structural
        d = self.dest()
        for name in ("send", "submit", "press_return", "enter", "post"):
            self.assertFalse(hasattr(d, name), name)


class FakeDestinationContract(DestinationContract, unittest.TestCase):
    def dest(self):
        return ad.FakeDestination()

    def test_unavailable_destination_says_why(self):
        ok, why = ad.FakeDestination(is_available=False).available()
        self.assertFalse(ok)
        self.assertIn("not installed", why)

    def test_insert_lands_in_the_composer_only(self):
        d = ad.FakeDestination()
        d.insert("package")
        self.assertEqual(d.composer, "package")


class HotkeyParseTests(unittest.TestCase):
    def test_parse_and_describe(self):
        self.assertEqual(hotkey.parse("ctrl+opt+h"), (4, 4096 | 2048))
        self.assertEqual(hotkey.describe("cmd+shift+space"), "⇧⌘Space")

    def test_unusable_shortcuts_are_rejected(self):
        for bad in ("", "h", "shift+h", "ctrl+opt+f13", "hyper+h", "ctrl+opt"):
            with self.assertRaises(ValueError, msg=bad):
                hotkey.parse(bad)

    def test_register_reports_bad_spec_without_touching_carbon(self):
        hk = hotkey.Hotkey(lambda: None, "nope")
        self.assertEqual(hk.register(), hotkey.FAILED)
        self.assertIn("unknown", hk.error)


class PrivacyGateTests(unittest.TestCase):
    def test_private_apps_sites_and_windows_are_never_read(self):
        for info in (ad.SourceInfo("1Password", "com.1password.1password"),
                     ad.SourceInfo("Chrome", "x", "Claude", "https://www.paypal.com/x"),
                     ad.SourceInfo("Chrome", "x", "Claude - Incognito (private)", "")):
            self.assertTrue(mac_rules.is_private(info), info)
        self.assertFalse(mac_rules.is_private(h.CLAUDE))


if __name__ == "__main__":
    unittest.main()
