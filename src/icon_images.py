"""The real icon of an app or web page, as an NSImage, found once and remembered.

Used wherever a remembered thing is drawn (the window rows, the project suggestion card). Returns None
when nothing is found; the caller picks its own placeholder. macOS only (AppKit).
"""
from AppKit import NSImage, NSWorkspace
from Foundation import NSData

import apps
import favicons

_cache = {}
_app_paths = None


def favicon(url):
    """The page's icon from the browser's own store, found once per site."""
    host = favicons.host_of(url)
    if host is None:
        return None
    key = "web:" + host
    if key not in _cache:
        image = None
        try:
            data = favicons.icon_bytes(url)
            if data:
                image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
        except Exception:
            image = None
        _cache[key] = image
    return _cache[key]


def app_image(app):
    """The installed app's own icon, or None."""
    global _app_paths
    if app not in _cache:
        image = None
        try:
            path = NSWorkspace.sharedWorkspace().fullPathForApplication_(app)
            if not path:
                if _app_paths is None:
                    _app_paths = {a["name"]: a["path"] for a in apps.installed()}
                path = _app_paths.get(app)
            image = NSWorkspace.sharedWorkspace().iconForFile_(path) if path else None
        except Exception:
            image = None
        _cache[app] = image
    return _cache[app]


def best(app, kind="", url=None):
    """(image, is_favicon): the app's icon, else the page's favicon, else (None, False)."""
    url = favicons.address(app, kind, url)
    image = app_image(app)
    if image is not None:
        return image, False
    image = favicon(url) if url else None
    return image, image is not None
