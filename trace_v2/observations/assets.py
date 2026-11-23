"""Screenshot files: stored byte-for-byte as captured, outside the database.

Layout: <assets>/screenshots/YYYY/MM/DD/<observation_id>.<ext> (UTC date of capture). Folders 0700, files 0600.
Writes are create-only (never overwrite), fsynced, then renamed into place. A screenshot whose secret scan (OCR) has not
finished is written under quarantine/ with the same relative name and moved to its final place only when the scan is clean. Paths handed in are always
relative and are checked to stay inside the asset root.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

QUARANTINE = "quarantine"
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

    def thumbnail_path(self, observation_id, captured_at_ns):
        day = datetime.fromtimestamp(captured_at_ns / 1e9, tz=timezone.utc)
        return f"thumbnails/{day:%Y/%m/%d}/{observation_id}.jpg"

    def _resolve(self, relative, base=None):
        base = self.root if base is None else base
        path = (base / relative).resolve()
        if self.root.resolve() not in path.parents:
            raise AssetError(f"asset path escapes the asset root: {relative}")
        return path

    def _locate(self, relative):
        """Where the file is now: the final location, else quarantine. The final path when it exists nowhere."""
        final = self._resolve(relative)
        if final.exists():
            return final
        held = self._resolve(relative, self.root / QUARANTINE)
        return held if held.exists() else final

    def _prepare(self, relative, base=None):
        path = self._resolve(relative, base)
        top = self.root.resolve() if base is None else base.resolve()
        for parent in reversed(path.relative_to(top).parents):
            d = top / parent
            if not d.exists():
                d.mkdir(mode=0o700, parents=True)
        return path

    def _taken(self, relative):
        return self._resolve(relative).exists() or self._resolve(relative, self.root / QUARANTINE).exists()

    def _base(self, quarantined):
        return self.root / QUARANTINE if quarantined else None

    @staticmethod
    def _sync_dir(path):
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def link(self, relative, source_relative, quarantined=False):
        """Give an existing screenshot a second name, so identical pixels are stored once. Never replaces a file.
        `quarantined` puts the new name in quarantine (see write)."""
        source = self._locate(source_relative)
        if self._taken(relative):
            raise AssetError(f"screenshot already exists: {relative}")
        path = self._prepare(relative, self._base(quarantined))
        os.link(source, path)
        self._sync_dir(path)

    def write(self, relative, data, quarantined=False):
        """Create the file with exactly these bytes. Refuses to replace an existing file.

        A quarantined file lives under `quarantine/` with the same relative name. Every read, exists, link and delete
        finds it there, but nothing treats it as a kept screenshot until release() moves it to its final place."""
        if self._taken(relative):
            raise AssetError(f"screenshot already exists: {relative}")
        path = self._prepare(relative, self._base(quarantined))
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
        self._sync_dir(path)

    def is_quarantined(self, relative):
        return not self._resolve(relative).exists() and self._resolve(relative, self.root / QUARANTINE).exists()

    def release(self, relative):
        """Move a quarantined file to its final place. -> True when it was quarantined, False when it already is final or is gone."""
        if not self.is_quarantined(relative):
            return False
        held = self._resolve(relative, self.root / QUARANTINE)
        final = self._prepare(relative)
        os.rename(held, final)
        self._sync_dir(final)
        self._sync_dir(held)
        return True

    def read(self, relative, expected_sha256=None):
        data = self._locate(relative).read_bytes()
        if expected_sha256 is not None and sha256(data) != expected_sha256:
            raise AssetError(f"screenshot checksum mismatch: {relative}")
        return data

    def exists(self, relative):
        return self._locate(relative).exists()

    def delete(self, relative):
        """Remove the file wherever it is (final or quarantine). Returns whether it existed."""
        found = False
        for path in (self._resolve(relative), self._resolve(relative, self.root / QUARANTINE)):
            try:
                path.unlink()
                found = True
            except FileNotFoundError:
                pass
        return found

    def all_relative_paths(self):
        out = set()
        for top in (self.root, self.root / QUARANTINE):
            base = top / "screenshots"
            if base.exists():
                out |= {str(p.relative_to(top)) for p in base.rglob("*") if p.is_file() and not p.name.startswith(".")}
        return out
