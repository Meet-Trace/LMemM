"""scripts/copy_name_model.py: copies a model's manifests and blobs between Ollama folders, nothing else."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path


def load():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "copy_name_model.py")
    spec = importlib.util.spec_from_file_location("copy_name_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CopyTest(unittest.TestCase):
    def test_copies_manifests_list_and_blobs_once(self):
        mod = load()
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "src", Path(d) / "dst"
            blobs = src / "blobs"
            blobs.mkdir(parents=True)
            (blobs / "sha256-aa").write_text("weights")
            (blobs / "sha256-bb").write_text(json.dumps({"layers": [{"digest": "sha256:aa"}]}))     # variant manifest
            (blobs / "sha256-cc").write_text(json.dumps({"manifests": [{"digest": "sha256:bb"}, {"digest": "sha256:zz"}]}))
            v1 = src / "manifests/registry.ollama.ai/library/qwen3.5/0.8b"
            v1.parent.mkdir(parents=True)
            v1.write_text(json.dumps({"layers": [{"digest": "sha256:aa"}]}))
            v2 = src / "manifests-v2/ollama.com/library/qwen3.5/0.8b"
            v2.parent.mkdir(parents=True)
            os.symlink("../../../../blobs/sha256-cc", v2)
            (dst / "blobs").mkdir(parents=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                mod.main(["--source", str(src), "--dest", str(dst)])
            self.assertFalse((dst / "blobs/sha256-aa").exists())          # a dry run copies nothing
            with contextlib.redirect_stdout(out):
                mod.main(["--source", str(src), "--dest", str(dst), "--copy"])
            for f in ("blobs/sha256-aa", "blobs/sha256-bb", "blobs/sha256-cc",
                      "manifests/registry.ollama.ai/library/qwen3.5/0.8b"):
                self.assertTrue((dst / f).is_file(), f)
            self.assertEqual(os.readlink(dst / "manifests-v2/ollama.com/library/qwen3.5/0.8b"), "../../../../blobs/sha256-cc")
            self.assertTrue((src / "blobs/sha256-aa").exists())           # nothing moved
            with contextlib.redirect_stdout(out):
                mod.main(["--source", str(src), "--dest", str(dst), "--copy"])
            self.assertIn("Nothing to copy", out.getvalue())


if __name__ == "__main__":
    unittest.main()
