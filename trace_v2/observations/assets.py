"""Screenshot files: stored byte-for-byte as captured, outside the database.

Layout: <assets>/screenshots/YYYY/MM/DD/<observation_id>.<ext> (UTC date of capture). Folders 0700, files 0600.
Writes are create-only (never overwrite), fsynced, then renamed into place. Paths handed in are always
relative and are checked to stay inside the asset root.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

EXTENSIONS = {"png": "png", "jpeg": "jpg", "heic": "heic", "tiff": "tiff"}


class AssetError(OSError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def magic_matches(fmt, data):
    if fmt == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if fmt == "jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if fmt == "tiff":
        return data[:4] in (b"II*\x00", b"MM\x00*")
    if fmt == "heic":
        return data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heim", b"heis")
    return False


class ScreenshotStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def relative_path(self, observation_id, captured_at_ns, fmt):
        day = datetime.fromtimestamp(captured_at_ns / 1e9, tz=timezone.utc)
        return f"screenshots/{day:%Y/%m/%d}/{observation_id}.{EXTENSIONS[fmt]}"

    def _resolve(self, relative):
        path = (self.root / relative).resolve()
        if self.root.resolve() not in path.parents:
            raise AssetError(f"asset path escapes the asset root: {relative}")
        return path

    def write(self, relative, data):
        """Create the file with exactly these bytes. Refuses to replace an existing file."""
        path = self._resolve(relative)
        for parent in reversed(path.relative_to(self.root.resolve()).parents):
            d = self.root.resolve() / parent
            if not d.exists():
                d.mkdir(mode=0o700)
        if path.exists():
            raise AssetError(f"screenshot already exists: {relative}")
        temp = path.with_name("." + path.name + ".tmp")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.link(temp, path)          # fails if `path` appeared meanwhile: never overwrites
        finally:
            temp.unlink(missing_ok=True)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def read(self, relative, expected_sha256=None):
        data = self._resolve(relative).read_bytes()
        if expected_sha256 is not None and sha256(data) != expected_sha256:
            raise AssetError(f"screenshot checksum mismatch: {relative}")
        return data

    def exists(self, relative):
        return self._resolve(relative).exists()

    def delete(self, relative):
        """Remove the file. Returns whether it existed."""
        try:
            self._resolve(relative).unlink()
            return True
        except FileNotFoundError:
            return False

    def all_relative_paths(self):
        base = self.root / "screenshots"
        if not base.exists():
            return set()
        return {str(p.relative_to(self.root)) for p in base.rglob("*")
                if p.is_file() and not p.name.startswith(".")}
