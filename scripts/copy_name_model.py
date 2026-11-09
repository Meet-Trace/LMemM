#!/usr/bin/env python3
"""Copy a model you already have in Ollama into LMemM's own Ollama folder. Downloads nothing.

    python3 scripts/copy_name_model.py                 # shows what it would copy
    python3 scripts/copy_name_model.py --copy          # copies it

Copies the model's manifest, the manifest list and every blob they name that exists in the source,
from ~/.ollama/models into <data>/embedding-models (the folder the app's Ollama on port 11455 uses).
Files already there are left alone. Nothing is moved or deleted.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def digests(path):
    """Every sha256 digest a manifest or manifest list names."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    found = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "digest" and isinstance(v, str) and v.startswith("sha256:"):
                    found.append(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(doc)
    return found


def plan(src, dst, name, tag):
    v1 = Path("manifests/registry.ollama.ai/library") / name / tag
    v2 = Path("manifests-v2/ollama.com/library") / name / tag
    files, links = [], []
    if (src / v1).is_file():
        files.append(v1)
    blobs, todo = set(), list(digests(src / v1))
    if (src / v2).is_symlink():
        target = os.readlink(src / v2)
        links.append((v2, target))
        todo += ["sha256:" + Path(target).name.split("sha256-")[-1]]
    while todo:
        d = todo.pop()
        b = Path("blobs") / d.replace(":", "-")
        if b in blobs or not (src / b).is_file():
            continue
        blobs.add(b)
        if (src / b).stat().st_size < 1_000_000:            # a manifest or manifest list: follow it
            todo += digests(src / b)
    files += sorted(blobs)
    return [f for f in files if not (dst / f).exists()], [l for l in links if not (dst / l[0]).exists()]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="qwen3.5:0.8b")
    p.add_argument("--source", type=Path, default=Path.home() / ".ollama" / "models")
    p.add_argument("--dest", type=Path, default=None, help="default: <data>/embedding-models")
    p.add_argument("--copy", action="store_true")
    args = p.parse_args(argv)
    if args.dest is None:
        import config
        args.dest = Path(config.paths().data_dir) / "embedding-models"
    name, tag = args.model.split(":")
    files, links = plan(args.source, args.dest, name, tag)
    size = sum((args.source / f).stat().st_size for f in files)
    print(f"{args.model}: {len(files)} files ({size / 1e9:.2f} GB) and {len(links)} link(s) to copy into {args.dest}")
    if not files and not links:
        print("Nothing to copy: is the model in the source? (ollama list)")
        return
    if not args.copy:
        print("Run again with --copy to copy them.")
        return
    for f in files:
        (args.dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.source / f, args.dest / f)
        os.chmod(args.dest / f, 0o644)
    for link, target in links:
        (args.dest / link).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, args.dest / link)
    print("Copied. Quit and reopen LMemM so its Ollama sees the new model.")


if __name__ == "__main__":
    main()
