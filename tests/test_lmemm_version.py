"""lmemm.py --version: prints the name and version from one constant, and starts nothing."""

import contextlib
import io
import unittest
from unittest.mock import patch

import lmemm


def run(*argv):
    """lmemm.main() with these arguments; returns (stdout, stderr, exit code or None)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with patch("sys.argv", ["lmemm.py", *argv]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            lmemm.main()
        except SystemExit as exit_:
            code = exit_.code
    return out.getvalue(), err.getvalue(), code


class VersionFlagTests(unittest.TestCase):
    def test_version_prints_name_and_version(self):
        out, err, code = run("--version")
        self.assertEqual(out, f"lmemm.py {lmemm.__version__}\n")
        self.assertEqual(err, "")
        self.assertIn(code, (None, 0))

    def test_version_is_built_from_the_constant(self):
        with patch.object(lmemm, "__version__", "9.9.9"):
            out, _, _ = run("--version")
        self.assertEqual(out, "lmemm.py 9.9.9\n")

    def test_version_constant_value(self):
        self.assertEqual(lmemm.__version__, "0.1.0")

    def test_version_does_not_start_anything(self):
        for argv in (["--version"], ["--version", "--every", "5"]):
            with self.subTest(argv=argv), patch.object(lmemm, "cmd_start") as start, \
                 patch.object(lmemm, "cmd_setup") as setup, patch.object(lmemm, "interactive_menu") as menu:
                out, _, code = run(*argv)
                self.assertEqual(out, f"lmemm.py {lmemm.__version__}\n")
                self.assertIn(code, (None, 0))
                start.assert_not_called()
                setup.assert_not_called()
                menu.assert_not_called()

    def test_other_leading_flags_still_go_to_start(self):
        with patch.object(lmemm, "cmd_start") as start:
            run("--every", "5")
        start.assert_called_once_with(["--every", "5"])

    def test_an_unknown_command_still_exits_with_usage(self):
        _, _, code = run("no-such-command")
        self.assertEqual(code, lmemm.USAGE)

    def test_usage_mentions_version(self):
        self.assertIn("--version", lmemm.USAGE)
        self.assertIn("--version", lmemm.__doc__)


if __name__ == "__main__":
    unittest.main()
