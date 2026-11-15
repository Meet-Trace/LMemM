"""Permission state by preflight only: nothing here prompts, opens System Settings or requests access."""

import sys

import ApplicationServices
import Quartz


def report():
    """-> {"screen_recording", "accessibility", "input_monitoring": bool, "automation": "unknown", "process": path}.

    Automation (AppleScript to a browser) is granted per target app and cannot be checked without either prompting or
    naming the app; it shows up at capture time as `window.url_error` (`automation_denied`), so it is reported unknown."""
    return {"screen_recording": bool(Quartz.CGPreflightScreenCaptureAccess()),
            "accessibility": bool(ApplicationServices.AXIsProcessTrusted()),
            "input_monitoring": bool(Quartz.CGPreflightListenEventAccess()),
            "automation": "unknown", "process": sys.executable}
