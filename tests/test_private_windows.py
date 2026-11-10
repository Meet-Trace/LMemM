"""Private browser contexts must never reach screenshot, AX, or retained evidence paths."""
import tempfile
import collections
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import config
import macos
import ax
import tracker
import trail_engine
import trail_mac
from trail_store import TrailStore
from frames import fake_frame


FRONT = {'app': 'Brave Browser', 'bundle_id': 'com.brave.Browser', 'pid': 42,
         'win_id': 123, 'window': 'An ordinary page title',
         'bounds': {'X': 0, 'Y': 0, 'Width': 900, 'Height': 700}}


class PrivateWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        paths = config.use_paths(self.temp.name)
        paths.__enter__()
        self.addCleanup(paths.__exit__, None, None, None)
        self.capture = tracker.Tracker(show_widget=False, trail=False)

    def test_browser_probe_failure_is_unknown_not_public(self):
        script = SimpleNamespace(executeAndReturnError_=lambda _arg: (None, {'error': 'not authorized'}))
        with patch.dict(macos._SCRIPTS, {'Brave Browser': script}):
            self.assertIsNone(macos.browser_info('Brave Browser')[2])

    def test_private_background_window_blocks_browser_even_when_front_is_public(self):
        class Result:
            def numberOfItems(self): return 4
            def descriptorAtIndex_(self, index):
                values = ['https://example.org', 'Public page', 'normal', 'incognito']
                return SimpleNamespace(stringValue=lambda: values[index - 1])
        script = SimpleNamespace(executeAndReturnError_=lambda _arg: (Result(), None))
        with patch.dict(macos._SCRIPTS, {'Brave Browser': script}):
            self.assertEqual(macos.browser_info('Brave Browser'), (None, None, True))

    def test_click_hit_testing_never_reads_a_background_private_window(self):
        reader = SimpleNamespace(AS=SimpleNamespace())
        with patch.object(trail_mac, 'front', return_value=FRONT):
            self.assertEqual(trail_mac.click_target(reader, 40, 40), (None, None))

    def test_browser_input_is_never_queued_even_during_fast_mode_switches(self):
        queue = collections.deque()
        tap = trail_mac.Tap(queue)
        tap.enqueue('key', 1.0, None, None, FRONT)
        tap.enqueue('click', 1.1, None, 'left', FRONT)
        self.assertEqual(list(queue), [])
        tap.enqueue('key', 1.2, None, None, {'pid': 7, 'app': 'Notes', 'bundle_id': 'com.apple.Notes'})
        self.assertEqual(list(queue), [('key', 1.2, None, None, 7)])

    def test_nonbrowser_click_retains_no_background_label(self):
        info = {'pid': 7, 'app': 'Notes', 'bundle_id': 'com.apple.Notes'}
        store = TrailStore(self.temp.name, 'test')
        instance = trail_mac.Trail.__new__(trail_mac.Trail)
        instance.queue = collections.deque([('click', 1.0, None, 'left', 7)])
        instance._drain_context = (7, 'com.apple.Notes')
        instance.paused = lambda: False
        instance.poke = lambda _kind: None
        instance.engine = trail_engine.Engine(store, lambda: info, None)
        instance.engine.tracker.current = {'key': 'notes', 'bundle_id': 'com.apple.Notes'}
        with patch.object(trail_mac, 'front', return_value=info):
            instance.drain(1.1)
        rows = list(store.read())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'click')
        self.assertIsNone(rows[0].get('target'))

    def test_rejected_private_read_leaves_no_cached_url(self):
        reader = trail_mac.AXReader.__new__(trail_mac.AXReader)
        reader.url_cache = {}
        reader.trusted = lambda: True
        reader.app_element = lambda *_args: object()
        light = SimpleNamespace(title='Shared title', url='')
        full = SimpleNamespace(url='https://private.example')
        with patch.object(macos, 'privacy_reason', side_effect=[None, 'private window']), \
             patch.object(trail_mac.trail_ax, 'element_attr', side_effect=lambda _el, attr: object() if attr=='AXFocusedWindow' else None), \
             patch.object(trail_mac.trail_ax, 'LiveNode', side_effect=lambda el: el), \
             patch.object(trail_mac.trail_ax, 'collect', side_effect=[light, full]):
            self.assertIsNone(reader.read(FRONT, False))
        self.assertEqual(reader.url_cache, {})

    def test_private_and_unverifiable_browser_never_capture(self):
        for status in (True, None):
            with self.subTest(status=status), patch.object(macos, 'front', return_value=FRONT), \
                 patch.object(macos, 'browser_info', return_value=(None, None, status)), \
                 patch.object(macos, 'grab', side_effect=AssertionError('private pixels read')):
                self.assertIsNone(self.capture.capture('timer'))
                self.assertTrue(self.capture.q.empty())

    def test_timer_drops_frame_if_public_window_turns_private_during_capture(self):
        frame = fake_frame()
        screen = SimpleNamespace(size=SimpleNamespace(width=900, height=700))
        with patch.object(macos, 'front', return_value=FRONT), \
             patch.object(macos, 'browser_info', side_effect=[('https://example.org', 'Example', False), (None, None, True)]), \
             patch.object(macos, 'display_for', return_value=(1, screen)), \
             patch.object(macos, 'input_ages', return_value={}), \
             patch.object(macos, 'grab', return_value=frame):
            self.assertIsNone(self.capture.capture('timer'))
        self.assertTrue(self.capture.q.empty())
        self.assertIsNone(frame.cg)
        self.assertIsNone(frame.gray)

    def test_verified_public_window_still_captures_without_other_windows(self):
        screen = SimpleNamespace(size=SimpleNamespace(width=900, height=700))
        with patch.object(macos, 'front', return_value=FRONT), \
             patch.object(macos, 'browser_info', return_value=('https://example.org', 'Example', False)), \
             patch.object(macos, 'display_for', return_value=(1, screen)), \
             patch.object(macos, 'input_ages', return_value={}), \
             patch.object(macos, 'grab', return_value=fake_frame()) as grab:
            meta = self.capture.capture('timer')
        self.assertEqual(meta['capture_scope'], 'window')
        self.assertFalse(meta['browser_private'])
        self.assertEqual(meta['url'], 'https://example.org')
        self.assertFalse(self.capture.q.empty())
        grab.assert_called_once_with(1, window_id=123)

    def test_private_context_never_reaches_accessibility_tree(self):
        with patch.object(macos, 'browser_info', return_value=(None, None, True)), \
             patch.object(ax.AS, 'AXIsProcessTrusted', return_value=True), \
             patch.object(ax.AS, 'AXUIElementCreateApplication', side_effect=AssertionError('private AX read')):
            self.assertIsNone(ax.read(42, 'Brave Browser'))

    def test_private_context_never_starts_quick_pixel_read(self):
        with patch.object(macos, 'browser_info', return_value=(None, None, True)), \
             patch.object(self.capture, 'start_read', side_effect=AssertionError('private quick read')):
            self.capture.identify_now(FRONT)
        self.assertNotIn('Brave Browser', self.capture.ocr_apps)

    def test_trail_gate_rejects_private_context_before_any_reader(self):
        self.assertIsNotNone(trail_engine.Gate().app({**FRONT, 'privacy_blocked': True}))

    def test_isolated_capture_requires_a_window_id(self):
        # Missing identity must not silently fall back to a whole-display shot.
        with patch.object(macos, '_frame_from_cg', side_effect=AssertionError('display screenshot used')):
            self.assertIsNone(macos.grab(1, window_id=None))
            self.assertIsNone(macos.grab_with_screencapture(1, window_id=None))

if __name__ == '__main__':
    unittest.main()
