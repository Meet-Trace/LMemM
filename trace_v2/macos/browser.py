"""Browser URL and private-window state via AppleScript, within a hard time budget.

Needs the Automation permission for the browser. A refusal, timeout or any other failure never blocks capture: the
caller gets no url/private and a short `url_error` code saying why. Safari has no scriptable private flag, so its
private windows are NOT detected here (`private_error: "unsupported_safari"`); only the title gate can catch them.
"""

import subprocess

BUDGET_S = 0.3
CHROMIUM = frozenset({
    "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.dev", "com.google.Chrome.canary",
    "org.chromium.Chromium", "com.brave.Browser", "com.brave.Browser.beta", "com.brave.Browser.nightly",
    "com.microsoft.edgemac", "com.microsoft.edgemac.Beta", "com.microsoft.edgemac.Dev", "com.vivaldi.Vivaldi",
    "com.operasoftware.Opera",
})
SAFARI = frozenset({"com.apple.Safari", "com.apple.SafariTechnologyPreview"})


def _script(bundle_id, chromium):
    if chromium:
        body = ('if (count of windows) is 0 then return ""\n'
                'return (URL of active tab of front window) & linefeed & (mode of front window)')
    else:
        body = 'if (count of windows) is 0 then return ""\nreturn URL of current tab of front window'
    return f'tell application id "{bundle_id}"\n{body}\nend tell'


def _classify_failure(stderr):
    if "-1743" in stderr:
        return "automation_denied"
    if "-600" in stderr or "-1728" in stderr:
        return "application_not_scriptable"
    return "applescript_error"


def browser_state(bundle_id, run=subprocess.run):
    """-> dict to merge into the window: {url?, private?, url_error?, private_error?}. `run` is injectable."""
    chromium = bundle_id in CHROMIUM
    if not chromium and bundle_id not in SAFARI:
        return {}
    extra = {} if chromium else {"private_error": "unsupported_safari"}
    try:
        done = run(["/usr/bin/osascript", "-e", _script(bundle_id, chromium)], capture_output=True, text=True,
                   timeout=BUDGET_S)
    except subprocess.TimeoutExpired:
        return {**extra, "url_error": "automation_timeout"}
    except OSError:
        return {**extra, "url_error": "osascript_unavailable"}
    if done.returncode != 0:
        return {**extra, "url_error": _classify_failure(done.stderr)}
    lines = done.stdout.strip("\n").split("\n")
    url = lines[0].strip()
    if not url:
        return {**extra, "url_error": "no_window"}
    state = {**extra, "url": url}
    if chromium:
        mode = lines[1].strip() if len(lines) > 1 else ""
        if mode in ("incognito", "normal"):
            state["private"] = mode == "incognito"
        else:
            state["private_error"] = "unknown_mode"
    return state
