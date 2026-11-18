"""Build the public Cloudflare Pages directory (dist/) using only runtime files."""
from pathlib import Path
import hashlib
import re
import shutil

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "dist"
FILES = (
    "index.html", "hero.css", "hero.js", "brain.svg", "favicon.svg",
    "trace-launch.mp4", "trace-launch-poster.jpg", "og.jpg",
    "_headers", "robots.txt", "sitemap.xml",
)
DIRS = ("icons", "moodboard")


def build():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir()
    for name in FILES:
        shutil.copy2(ROOT / name, DEST / name)
    for name in DIRS:
        shutil.copytree(ROOT / name, DEST / name)
    version_assets()
    print(f"Published files prepared in {DEST}")


def version_assets():
    """Stamp hero.css / hero.js in dist/index.html with a hash of their contents,
    so a changed file always gets a new URL and never pairs new HTML with a stale cached asset."""
    page = DEST / "index.html"
    html = page.read_text()
    for name in ("hero.css", "hero.js"):
        digest = hashlib.sha256((DEST / name).read_bytes()).hexdigest()[:10]
        html = re.sub(rf'{re.escape(name)}(\?v=[0-9a-f]+)?"', f'{name}?v={digest}"', html)
    page.write_text(html)


if __name__ == "__main__":
    build()
