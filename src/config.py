"""LMemM - every tunable, privacy rule and data path in one place."""

import contextlib
import os
import re
from dataclasses import dataclass, replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- capture schedule

EVERY = 5               # seconds between captures while you're active on one window
BACKOFF_CAP = 30        # ...doubling up to this while nothing changes and you give no input
BACKOFF_CAP_CHAT = 15   # chats and AI chats: text can arrive with no input from you
SETTLE = 1.0            # debounce: wait this long after the last trigger
MAX_SETTLE = 3.0        # ...but never longer than this after the first one
MIN_GAP = 2.0           # never two captures closer than this (pinned excepted)
POLL = 0.5              # seconds between window/tab title checks
FIND_PROJECTS = True    # notice projects in what you did and suggest them (project_finder.py); never files anything
FIND_EVERY = 30 * 60    # seconds between looks, taken only while you are idle
NAME_MODEL = "qwen3.5:0.8b"   # a tiny local model names suggested projects (group_names.py); None: code names
NAME_MODEL_URL = "http://127.0.0.1:11455"  # the app's own Ollama; if it is not running, code names are used
IDLE = 60               # no input for this long -> pause
MAX_QUEUE = 8           # resolver backlog above which timer captures are skipped
MAX_PX = 1600           # a captured screen wider than this is scaled down to it

# ---------------------------------------------------------------- cost control

FAST_CONTINUATION = True    # fast OCR for later frames of the same window; accurate for the first look
THIN_RATIO = 0.5            # fast OCR that finds under this share of the previous frame's lines is redone accurately
THUMB_PX = 480              # kept screenshots are thumbnails with this long edge...
THUMB_QUALITY = 60          # ...at this JPEG quality (~20-30 KB)
SCREENSHOT_DAYS = 7         # a thing's thumbnail is deleted this long after it was taken...
                            # ...unless the thing is pinned or has an open note
SAVE_EVERY = 5              # seconds: write memory files at most this often while running
RETENTION_SWEEP = 3600      # seconds between screenshot-expiry sweeps
RSS_WARN_MB = 800           # warn in the terminal when LMemM itself uses more memory than this
CPU_WARN_PCT = 60           # ...or more than this share of one core over the last 30 s

# ---------------------------------------------------------------- event trail (trail_*.py)

TRAIL_RETENTION_HOURS = 48      # raw events stay on this Mac this long, then are deleted
TRAIL_MAX_MB = 50               # hard cap on the trail folder; oldest day goes first
TRAIL_READ_FIELD_TEXT = False   # read the text INSIDE the focused text box (an editor's document)? off: only that one exists
TRAIL_TEXT_EVERY = 2.0          # seconds: least time between two text reads of one place
TRAIL_FULL_MS = 90              # a full tree read stops after this long (Chromium apps cost ~0.5 ms a node)
TRAIL_SETTLE_MS = 150           # a shaky place must hold this long before it counts as a switch
TRAIL_POLL = 1.0                # seconds: the cheap fallback check when an app sends no notifications
TRAIL_IDLE = 60                 # no input for this long -> idle event, reads slow right down
TRAIL_VISION = True             # fall back to a screenshot read when an app exposes too little text
TRAIL_VISION_EVERY = 20         # seconds: least time between screenshot reads of one place

# ---------------------------------------------------------------- memory

KEEP_TEXT = 15          # newest lines kept per activity category on an item
RESURFACE_COOLDOWN = 600  # seconds: remind about an item's pending edits at most this often

# ---------------------------------------------------------------- privacy: never captured

BROWSERS = {"Google Chrome", "Google Chrome Canary", "Brave Browser", "Microsoft Edge", "Arc", "Safari"}
CHROMIUM = BROWSERS - {"Safari"}
SKIP_APPS = {
    "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop",
    "com.apple.keychainaccess", "com.apple.Passwords", "com.lastpass.LastPass",
    "com.dashlane.dashlanephonefinal", "org.keepassxc.keepassxc", "com.nordpass.macos.NordPass",
}
BUILTIN_SKIP_APPS = frozenset(SKIP_APPS)          # always skipped; the Privacy settings add the user's own on top


def set_skip_apps(extra=()):
    """SKIP_APPS = the built-in apps plus the ones you chose to never remember. Changed in place, so
    everything holding the set (the event trail's gate) sees it at once."""
    SKIP_APPS.clear()
    SKIP_APPS.update(BUILTIN_SKIP_APPS)
    SKIP_APPS.update(i for i in extra if isinstance(i, str) and i)


SKIP_SITES = re.compile(
    r"bank|netbanking|onlinesbi|hdfc|icici|axisbank|kotak|paypal\.|wise\.com|stripe\.com"
    r"|razorpay\.com/(app|dashboard)|paytm|phonepe|zerodha|groww|coinbase|binance"
    r"|accounts\.google\.com|appleid\.apple\.com|/login|/signin|password", re.I)
SKIP_TITLES = re.compile(r"private browsing|incognito|inprivate|password|\bprivate (?:tab|window)\b|\(private\)", re.I)
LOCK_APPS = {"loginwindow", "ScreenSaverEngine"}   # frontmost while locked / screensaver


# ---------------------------------------------------------------- data paths

@dataclass(frozen=True)
class Paths:
    data_dir: str                 # screenshots + their capture metadata
    pidfile: str                  # running tracker; .control.json / .status.json beside it

    @property
    def memory_dir(self):
        return os.path.join(self.data_dir, "memory")

    @property
    def items_file(self):         # the one memory file: source of truth + readable view
        return os.path.join(self.memory_dir, "memory.json")

    @property
    def semantic_file(self):
        return os.path.join(self.memory_dir, "semantic.sqlite3")

    @property
    def trail_dir(self):          # the event trail: one JSONL file per day
        return os.path.join(self.data_dir, "trail")

    @property
    def semantic_dir(self):
        return os.path.join(self.data_dir, "project-memory")

    @property
    def projects_file(self):      # the project tree (projects.py); small, so it is read fresh each time
        return os.path.join(self.memory_dir, "projects.json")

    @property
    def sessions_dir(self):
        return os.path.join(self.memory_dir, "sessions")

    @property
    def onboarding_file(self):    # where first-run setup stands (step, answers); beside the pid file's data
        return os.path.join(self.data_dir, "onboarding.json")

    @property
    def control_file(self):
        return os.path.splitext(self.pidfile)[0] + ".control.json"

    @property
    def status_file(self):
        return os.path.splitext(self.pidfile)[0] + ".status.json"


PATHS = Paths(data_dir=os.path.join(ROOT, "data"), pidfile=os.path.join(ROOT, ".lmemm.pid"))
import sys
_resource_dir=os.environ.get('RESOURCEPATH',os.path.join(os.path.dirname(sys.executable),'..','Resources'))
if getattr(sys,'frozen',False) and os.path.isfile(os.path.join(_resource_dir,'standalone.json')):
    _installed_data=os.path.expanduser('~/Library/Application Support/LMemM/data')
    PATHS=Paths(data_dir=_installed_data,pidfile=os.path.join(_installed_data,'.lmemm.pid'))


@contextlib.contextmanager
def use_paths(data_dir, pidfile=None):
    """Point every module at another data directory (tests, replays)."""
    global PATHS
    old = PATHS
    PATHS = replace(old, data_dir=str(data_dir),
                    pidfile=str(pidfile) if pidfile else os.path.join(str(data_dir), ".lmemm.pid"))
    try:
        yield PATHS
    finally:
        PATHS = old


def paths():
    return PATHS
