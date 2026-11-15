"""Where Trace V2 keeps its data, and the guard that keeps it away from the V1 archive.

data_dir() resolution: the TRACE_V2_DATA_DIR environment variable if set, else <repo>/data.
check_data_dir() refuses (LegacyDataError) a directory that is, or is inside, the V1 archive
(~/Library/Application Support/Trace/Legacy-V1), or that already holds V1 files (memory/memory.json,
trail/, moments/), so V2 never reads from or writes into V1 data during normal operation.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ARCHIVE = Path("~/Library/Application Support/Trace/Legacy-V1").expanduser()
V1_MARKERS = ("memory/memory.json", "memory/moments", "trail")


class LegacyDataError(RuntimeError):
    """The chosen V2 data directory overlaps V1 data."""


def data_dir():
    return Path(os.environ.get("TRACE_V2_DATA_DIR") or REPO_ROOT / "data")


def check_data_dir(path, legacy_archive=LEGACY_ARCHIVE):
    real = Path(path).expanduser().resolve()
    archive = Path(legacy_archive).expanduser().resolve()
    if real == archive or archive in real.parents:
        raise LegacyDataError(f"{real} is inside the V1 archive; V2 must not use it")
    found = [m for m in V1_MARKERS if (real / m).exists()]
    if found:
        raise LegacyDataError(f"{real} holds V1 data ({', '.join(found)}); choose an empty directory for V2")
    return real


def database_path(root):
    return Path(root) / "observations.sqlite3"


def assets_dir(root):
    return Path(root) / "observation-assets"
