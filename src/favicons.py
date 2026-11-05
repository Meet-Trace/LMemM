"""A web page's own icon, read from the browser's local favicon store.

    icon_bytes(url) -> PNG bytes | None

Chromium browsers (Chrome, Brave, Edge, Chromium, Arc) keep the icon of every page you visited
in a SQLite file called "Favicons" inside each profile. Reading it is local and private: nothing
is fetched from the site or from an icon service. Safari, or a page the browser never drew an icon
for, gives None and the caller shows a generic web icon instead.

The file is locked while the browser runs, so it is copied to a temp file and read from there.
"""

import glob
import os
import shutil
import sqlite3
import tempfile
from urllib.parse import urlparse

SUPPORT = "~/Library/Application Support"
STORES = ("Google/Chrome", "BraveSoftware/Brave-Browser", "Microsoft Edge", "Chromium", "Arc/User Data")
WANT_PX = 64                       # drawn ~20-26 pt on a 2x screen: take the smallest bitmap that covers 64, else the largest there is
PNG = b"\x89PNG"


def host_of(url):
    """The host of an http(s) URL, or None for anything else."""
    try:
        u = urlparse(url or "")
    except ValueError:
        return None
    return u.hostname if u.scheme in ("http", "https") and u.hostname else None


def stores(support=None):
    """Every Favicons file we know of, the most recently used first."""
    base = os.path.expanduser(support or SUPPORT)
    found = []
    for store in STORES:
        found += glob.glob(os.path.join(base, store, "*", "Favicons"))
    return sorted(found, key=lambda p: os.path.getmtime(p), reverse=True)


def _read(path, url, origin):
    tmp = tempfile.mkdtemp(prefix="lmemm-fav-")
    try:
        copy = os.path.join(tmp, "Favicons")
        shutil.copy2(path, copy)
        db = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            for where, arg in (("m.page_url = ?", url), ("m.page_url LIKE ?", origin + "/%")):
                rows = db.execute(
                    "SELECT b.width, b.image_data FROM icon_mapping m JOIN favicon_bitmaps b ON b.icon_id = m.icon_id "
                    f"WHERE {where} AND b.image_data IS NOT NULL", (arg,)).fetchall()
                rows = [r for r in rows if r[1] and bytes(r[1][:4]) == PNG]
                if rows:
                    big = [r for r in rows if r[0] >= WANT_PX]
                    best = min(big, key=lambda r: r[0]) if big else max(rows, key=lambda r: r[0])
                    return bytes(best[1])
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


def icon_bytes(url, support=None):
    """PNG bytes of the page's icon from the first browser profile that has one, else None."""
    if not host_of(url):
        return None
    u = urlparse(url)
    origin = f"{u.scheme}://{u.netloc}"                  # includes the port: localhost:8000 is not localhost
    url = u._replace(fragment="").geturl()               # the browser files an icon under the page without its #part
    for path in stores(support):
        try:
            data = _read(path, url, origin)
        except (OSError, sqlite3.Error):
            continue                      # a locked, missing or different-version store is just "no icon here"
        if data:
            return data
    return None


def is_web(app, kind, url):
    """Whether a remembered thing lives on a web page, so a missing app icon should be the web's."""
    import understand
    return bool(host_of(url)) or kind == "web_page" or app in understand.SITES.values()


def page_url(item):
    """The address of a remembered thing, if it has one: the page state, else its latest saved text's source."""
    url = (item.get("state") or {}).get("url")
    if host_of(url):
        return url
    for excerpt in reversed((item.get("content") or {}).get("excerpts", [])):
        url = (excerpt.get("source") or {}).get("url")
        if host_of(url):
            return url
    return None


def address(app, kind, url):
    """The page address to look an icon up by: the thing's own, else its well-known site
    (a WhatsApp chat has no page address of its own, but lives on web.whatsapp.com)."""
    if host_of(url):
        return url
    import understand
    for host, name in understand.SITES.items():
        if name == app and is_web(app, kind, None):
            return f"https://{host}/"
    return None


if __name__ == "__main__":      # python3 src/favicons.py http://localhost:8000/  -> which stores exist, and whether the page has an icon
    import sys
    print("stores:", *stores(), sep="\n  ")
    for arg in sys.argv[1:]:
        print(arg, "->", len(icon_bytes(arg) or b""), "bytes")
