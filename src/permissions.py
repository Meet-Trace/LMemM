"""LMemM - what macOS lets this app do, and the ways to change it. The Mac side of onboarding.py.

Nothing here prompts by itself except ask_voice(). The facts are read with calls that only
look (CGPreflightScreenCaptureAccess, AXIsProcessTrusted, the speech helper's --status), on a
small background thread, so the window never waits on macOS.

Screen Recording has a catch macOS imposes: after the switch is turned on, the running process
keeps being refused until it restarts. So we ask twice: this process, and a brand-new one. A
new process that is allowed while this one is not means "turned on, restart needed".

PyObjC is imported inside the functions that need it, so the module imports anywhere.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

PANES = {"screen": "Privacy_ScreenCapture", "ax": "Privacy_Accessibility",
         "mic": "Privacy_Microphone", "speech": "Privacy_SpeechRecognition"}
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?{}"
_FRESH = ("import ctypes;l=ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics');"
          "l.CGPreflightScreenCaptureAccess.restype=ctypes.c_bool;print(int(l.CGPreflightScreenCaptureAccess()))")


def settings_url(pane):
    return SETTINGS_URL.format(PANES[pane])


def screen_allowed_here():
    """Can this process capture the screen? (No prompt.)"""
    import ctypes
    lib = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    lib.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    return bool(lib.CGPreflightScreenCaptureAccess())


def screen_allowed_fresh():
    """Would a brand-new process be allowed? True with screen_allowed_here() False means the
    switch is on but LMemM must restart."""
    try:
        from semantic_memory.runtime_environment import worker_argv
        out = subprocess.run(worker_argv('permission-probe'), capture_output=True, text=True, timeout=5)
        return out.returncode == 0 and out.stdout.strip() == "1"
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def accessibility_trusted():
    import ctypes
    lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    lib.AXIsProcessTrusted.restype = ctypes.c_bool
    return bool(lib.AXIsProcessTrusted())


def voice_helper(mode, timeout=75):
    """Run the speech helper (LMemM Listen) in "--status" or "--authorize" mode and return
    {"mic": ..., "speech": ...} (words from macOS), or None when it could not run.
    --status never prompts; --authorize lets macOS ask for Speech Recognition, then Microphone."""
    import dictation
    if not dictation.ensure_listener():
        return None
    out = os.path.join(tempfile.mkdtemp(prefix="lmemm-perm-"), "answer.json")
    try:
        subprocess.run(["open", "-W", "-n", "-g", dictation.LISTEN_APP, "--args", mode, out],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        with open(out, encoding="utf-8") as fh:
            answer = json.load(fh)
        return answer if isinstance(answer, dict) else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(out)
            os.rmdir(os.path.dirname(out))
        except OSError:
            pass


class MacSystem:
    """The `system` onboarding.Setup talks to, on a real Mac."""

    def __init__(self):
        self._facts = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._voice_busy = False

    # -- facts, kept fresh in the background

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def request_refresh(self):
        """Wake the existing worker; never request or grant a permission."""
        self._facts = {**self._facts, "checking": True}
        self._wake.set()

    def facts(self):
        return dict(self._facts)

    def _loop(self):
        last_voice = 0.0
        while not self._stop.is_set():
            self._wake.clear()
            check_voice = not self._voice_busy and time.time() - last_voice > 2.0
            self._read_facts(check_voice)
            if check_voice:
                last_voice = time.time()
            self._wake.wait(1.0)

    def _read_facts(self, check_voice=False):
        facts = dict(self._facts)
        errors = {}
        # A failed screen probe must not prevent the independent AX check.
        for key, probe in (("screen", screen_allowed_here), ("ax", accessibility_trusted)):
            try:
                facts[key] = probe()
            except Exception as error:
                facts[key] = None
                errors[key] = type(error).__name__
        try:
            facts["screen_fresh"] = facts.get("screen") or screen_allowed_fresh()
        except Exception as error:
            facts["screen_fresh"] = None
            errors["screen_fresh"] = type(error).__name__
        if check_voice:
            try:
                answer = voice_helper("--status", timeout=10)
                if answer:
                    facts["mic"], facts["speech"] = answer.get("mic"), answer.get("speech")
            except Exception as error:
                errors["voice"] = type(error).__name__
        self._facts = {**facts, "errors": errors, "checking": False}

    # -- doing things

    def ask_voice(self):
        """Let macOS ask for Speech Recognition, then Microphone, for "LMemM Listen"."""
        if self._voice_busy:
            return

        def run():
            self._voice_busy = True
            try:
                answer = voice_helper("--authorize", timeout=120)
                if answer:
                    self._facts = {**self._facts, "mic": answer.get("mic"), "speech": answer.get("speech")}
            finally:
                self._voice_busy = False
        threading.Thread(target=run, daemon=True).start()

    def _register(self, perm):
        """macOS lists an app under Screen Recording or Accessibility only after the app has
        asked once. Ask (macOS shows its own short prompt), so the app is in the list to switch on."""
        try:
            if perm == "screen":
                import ctypes
                lib = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
                lib.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
                lib.CGRequestScreenCaptureAccess()
            elif perm == "ax":
                import ApplicationServices as AS
                AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
        except Exception:
            pass

    def open_settings(self, perm):
        if perm in ("screen", "ax") and not self._facts.get(perm):
            self._register(perm)
        if perm == "voice":
            from onboarding import _status
            pane = "speech" if _status(self._facts.get("mic")) == "granted" else "mic"
        else:
            pane = perm
        subprocess.Popen(["open", settings_url(pane)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def relaunch(self):
        """Resume saved setup in a fresh native app process, preserving registration."""
        from AppKit import NSApplication
        self._stop.set()
        from semantic_memory.runtime_environment import launch_argv
        subprocess.Popen(launch_argv(sys.argv[1:]))
        NSApplication.sharedApplication().terminate_(None)

    # -- the Mac's account

    def language(self):
        try:
            import Foundation
            return str(Foundation.NSLocale.currentLocale().localeIdentifier()).replace("_", "-")
        except Exception:
            return None

    def time_zone(self):
        try:
            import Foundation
            return str(Foundation.NSTimeZone.localTimeZone().name())
        except Exception:
            return time.tzname[0] if time.tzname else None

    def save_user(self, record):
        import store
        store.save_user(record)
