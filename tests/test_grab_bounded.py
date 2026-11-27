"""macos.grab never holds the caller longer than GRAB_WAIT_S, and backs off after a stuck grab (the app hung 30 s once)."""
import sys, os, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
try:
    import macos
except (Exception, SystemExit):                    # not macOS
    macos = None


@unittest.skipIf(macos is None, "macos module needs macOS")
class GrabBounded(unittest.TestCase):
    def tearDown(self):
        macos._grab_stuck_until = 0.0

    def test_a_stuck_grab_returns_none_fast_then_backs_off(self):
        calls = []
        macos.GRAB_WAIT_S = 0.2
        macos._grab = lambda display: calls.append(1) or time.sleep(2) or "frame"
        t = time.monotonic()
        self.assertIsNone(macos.grab(1))
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertIsNone(macos.grab(1))
        self.assertEqual(len(calls), 1)               # no second helper thread while backing off

    def test_a_good_grab_is_returned(self):
        macos._grab = lambda display: "frame"
        self.assertEqual(macos.grab(1), "frame")


if __name__ == "__main__":
    unittest.main()
