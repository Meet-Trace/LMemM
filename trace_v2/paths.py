"""Where Trace V2 keeps its data, and the guard that keeps it away from the V1 archive.

data_dir() resolution: the TRACE_V2_DATA_DIR environment variable if set, else a folder of V2's own:
    macOS   ~/Library/Application Support/Trace/V2   (beside, never inside, Trace/Legacy-V1)
    other   $XDG_DATA_HOME/trace/v2, or ~/.local/share/trace/v2
It never defaults to <repo>/data, which is V1's default data folder.
check_data_dir() refuses (LegacyDataError) a directory that is, or is inside, the V1 archive
(~/Library/Application Support/Trace/Legacy-V1), or that already holds V1 files (memory/memory.json,
trail/, moments/), so V2 never reads from or writes into V1 data during normal operation.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ARCHIVE = Path("~/Library/Application Support/Trace/Legacy-V1").expanduser()
V1_STANDALONE = Path("~/Library/Application Support/LMemM").expanduser()   # a packaged V1 app's data
V1_MARKERS = ("memory/memory.json", "memory/moments", "trail")


class LegacyDataError(RuntimeError):
    """The chosen V2 data directory overlaps V1 data."""


def default_data_dir(platform=None, environ=None):
    platform, environ = platform or sys.platform, os.environ if environ is None else environ
    if platform == "darwin":
        return Path("~/Library/Application Support/Trace/V2").expanduser()
    base = environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "trace" / "v2"


def data_dir(environ=None):
    environ = os.environ if environ is None else environ
    return Path(environ["TRACE_V2_DATA_DIR"]) if environ.get("TRACE_V2_DATA_DIR") else default_data_dir(environ=environ)


def check_data_dir(path, legacy_archive=LEGACY_ARCHIVE):
    real = Path(path).expanduser().resolve()
    archive = Path(legacy_archive).expanduser().resolve()
    if real == archive or archive in real.parents:
        raise LegacyDataError(f"{real} is inside the V1 archive; V2 must not use it")
    for v1 in ((REPO_ROOT / "data").resolve(), V1_STANDALONE.resolve()):
        if real == v1 or v1 in real.parents:
            raise LegacyDataError(f"{real} is a V1 data folder; V2 keeps its own")
    found = [m for m in V1_MARKERS if (real / m).exists()]
    if found:
        raise LegacyDataError(f"{real} holds V1 data ({', '.join(found)}); choose an empty directory for V2")
    return real


def database_path(root):
    return Path(root) / "observations.sqlite3"


def assets_dir(root):
    return Path(root) / "observation-assets"
