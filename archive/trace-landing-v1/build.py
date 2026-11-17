"""Build the public Cloudflare Pages directory using only runtime files."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "dist"
RUNTIME = ("index.html", "styles.css", "identity.css", "app.js", "scenarios.js", "_headers", "robots.txt", "sitemap.xml")

def build():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir()
    for name in RUNTIME:
        shutil.copy2(ROOT / name, DEST / name)
    shutil.copytree(ROOT / "assets", DEST / "assets")
    print(f"Published assets prepared in {DEST}")

if __name__ == "__main__":
    build()
