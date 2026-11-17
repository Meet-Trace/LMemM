"""Build the public Cloudflare Pages directory (dist/) using only runtime files."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "dist"
FILES = (
    "index.html", "hero.css", "hero.js", "brain.svg", "favicon.svg",
    "trace-launch.mp4", "trace-launch-poster.jpg",
    "_headers", "robots.txt", "sitemap.xml",
)
DIRS = ("icons",)


def build():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir()
    for name in FILES:
        shutil.copy2(ROOT / name, DEST / name)
    for name in DIRS:
        shutil.copytree(ROOT / name, DEST / name)
    print(f"Published files prepared in {DEST}")


if __name__ == "__main__":
    build()
