import re
from urllib.parse import urlparse

BROWSERS = frozenset({
    "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.canary", "com.apple.Safari", "com.apple.SafariTechnologyPreview",
    "org.mozilla.firefox", "com.brave.Browser", "com.microsoft.edgemac", "com.vivaldi.Vivaldi", "company.thebrowser.Browser",
    "com.operasoftware.Opera"})
IDES = ("com.microsoft.VSCode", "com.apple.dt.Xcode", "com.jetbrains", "com.sublimetext", "com.todesktop", "dev.zed.Zed")
CHROME_TEXT = re.compile(
    r"^(bookmarks?|all bookmarks|other bookmarks|memory usage.*|extensions?|search tabs?|new tab|reload|back|forward|"
    r"customi[sz]e.*|reading list|show sidebar|tab overview|share|downloads?|profile.*|\d+\s?mb|\d+\s?tabs?)$", re.I)
TOP_CHROME_SHARE = 0.11
BOTTOM_SHARE = 0.96
TOP_MENU_SHARE = 0.04


def is_browser(bundle, app=""):
    return bundle in BROWSERS or any(k in (app or "").lower() for k in ("chrome", "safari", "firefox", "arc", "brave", "edge"))


def is_ide(bundle):
    return any((bundle or "").startswith(p) for p in IDES)


def host_of(url):
    try:
        return (urlparse(url).hostname or "").lower() or None
    except ValueError:
        return None


def document_type(bundle, app, title, url, has_table):
    host = host_of(url) or ""
    t = (title or "").lower()
    if "docs.google.com" in host and "/spreadsheets" in (url or ""):
        return "spreadsheet (Google Sheets)"
    if "docs.google.com" in host and "/document" in (url or ""):
        return "text document (Google Docs)"
    if "docs.google.com" in host and "/presentation" in (url or ""):
        return "presentation (Google Slides)"
    if host.endswith("youtube.com") or host == "youtu.be":
        return "video site (YouTube)"
    if t.endswith(".pdf") or "preview" in (app or "").lower() and ".pdf" in t:
        return "PDF document"
    if any(k in (app or "").lower() for k in ("excel", "numbers")) or (has_table and "sheet" in t):
        return "spreadsheet"
    if is_ide(bundle):
        return "code editor"
    if "github.com" in host:
        return "code hosting site (GitHub)"
    return None


def in_chrome_zone(box, image_size, browser):
    if not box or not image_size or not image_size[1]:
        return False
    y = box[1] / image_size[1]
    if y < TOP_MENU_SHARE or y > BOTTOM_SHARE:
        return True
    return browser and y < TOP_CHROME_SHARE


def is_chrome_text(text, box, image_size, browser):
    if in_chrome_zone(box, image_size, browser):
        return True
    return browser and bool(CHROME_TEXT.match(text.strip())) and bool(box) and box[1] / (image_size[1] or 1) < 0.2


def continuity(items):
    apps = {i["app"] for i in items}
    titles = {i["title"] for i in items}
    urls = {i.get("url") for i in items if i.get("url")}
    if len(apps) == 1 and len(titles) == 1 and len(urls) <= 1:
        return "same application and document throughout"
    if len(apps) == 1:
        return f"same application ({next(iter(apps))}), {len(titles)} different window titles"
    return f"{len(apps)} applications used: " + ", ".join(sorted(apps))
