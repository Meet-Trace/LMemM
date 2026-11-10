"""A known browser URL must suppress old-page notes before the next screen capture."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import identity
import tracker

class PageNoteTests(unittest.TestCase):
    def test_unknown_page_with_focused_field_does_not_show_previous_page_notes(self):
        instance = tracker.Tracker.__new__(tracker.Tracker)
        instance.running = True
        instance.trail = None
        instance.watch_apps = None
        instance.trail_state = lambda *args: None
        instance.lock = threading.Lock()
        instance.nsapp = None
        instance.last_sig = {'pid': 42, 'app': 'Google Chrome', 'window': 'Shared title'}
        instance.now = {'raw': ('Google Chrome', 'Shared title'), 'item': 'previous'}
        instance.events = []
        instance.cur_place = ('Google Chrome', 'Shared title')
        st = identity.page_state('Google Chrome', 'https://example.com/a', 'Shared title')
        ref = tracker.understand.ref(st)
        instance.items = {'previous': {'id': 'previous', 'app': 'Google Chrome', 'kind': 'web_page',
            'title': 'Shared title', 'doing': 'Reading a page', 'last_seen': '2026-11-09T10:00:00',
            'ref': ref, 'refs': [ref], 'notes': [{'at': '2026-11-09T10:00:00', 'text': 'Note for page A'}]}}
        instance.ax_now = None
        app = SimpleNamespace(processIdentifier=lambda: 42, localizedName=lambda: 'Google Chrome',
                              bundleIdentifier=lambda: 'com.google.Chrome')
        def front():
            instance.running = False
            return app
        workspace = SimpleNamespace(sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=front))
        sig = {'doc': 'https://example.com/b', 'title': 'Shared title', 'label': 'Search'}
        with patch.object(tracker.macos, 'NSWorkspace', workspace), patch.object(tracker.ax, 'read', return_value=sig), patch.object(tracker.time, 'sleep'):
            instance.ax_loop()
        self.assertIsNotNone(instance.ax_now)
        self.assertIsNone(instance.ax_now['item'])
        self.assertEqual(instance._card_here()['left'], [])
