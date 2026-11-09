"""scripts/install_judge.py builds the judge's Ollama model: from the safetensors (int8, the MLX engine) or,
with llama.cpp's converter, from a Q8_0 GGUF that Ollama runs on the CPU. Ollama and the converter are faked."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import install_judge  # noqa: E402


class InstallJudgeTest(unittest.TestCase):
    def run_main(self, *extra, have=()):
        with tempfile.TemporaryDirectory() as tmp:
            adapter, work = Path(tmp) / "adapter", Path(tmp) / "fused"
            adapter.mkdir()
            (adapter / "adapters.safetensors").write_bytes(b"x")
            work.mkdir()
            calls, modelfiles = [], []

            def fake_run(cmd, **kw):
                calls.append((list(map(str, cmd)), kw))
                if cmd[0] == "ollama":
                    modelfiles.append((Path(kw["cwd"]) / "Modelfile").read_text())

            with mock.patch.object(install_judge.subprocess, "run", fake_run), \
                    mock.patch.object(install_judge.ts, "installed", lambda url: list(have)), \
                    mock.patch.object(install_judge, "check",
                                      lambda name, url: [(True, 2.0), (False, -2.0)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = install_judge.main(["--adapter", str(adapter), "--work", str(work), "--name", "j", *extra])
            return code, calls, modelfiles, work

    def test_without_the_converter_it_imports_the_safetensors_as_int8(self):
        code, calls, modelfiles, _ = self.run_main()
        self.assertEqual(code, 0)
        create = calls[-1][0]
        self.assertEqual(create[:3], ["ollama", "create", "j"])
        self.assertEqual(create[-2:], ["--quantize", "int8"])
        self.assertEqual(modelfiles, ["FROM .\n"])

    def test_with_the_converter_it_imports_a_q8_gguf_unquantised_for_the_cpu(self):
        code, calls, modelfiles, work = self.run_main("--convert-script", "/x/convert_hf_to_gguf.py", "--python", "/x/py")
        self.assertEqual(code, 0)
        convert = next(c for c, _ in calls if c[0] == "/x/py")
        self.assertEqual(convert[1:], ["/x/convert_hf_to_gguf.py", str(work), "--outtype", "q8_0", "--outfile",
                                       str(work / "j-q8.gguf")])
        create = calls[-1][0]
        self.assertNotIn("--quantize", create)
        self.assertEqual(modelfiles, [f"FROM {work / 'j-q8.gguf'}\nPARAMETER num_ctx 2048\n"])

    def test_a_model_that_is_already_there_is_left_alone(self):
        code, calls, _, _ = self.run_main(have=["j"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
