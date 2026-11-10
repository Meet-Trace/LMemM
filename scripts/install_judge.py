"""Put the taught judge into Ollama, so the app's project finder can use it.

    python3 scripts/install_judge.py --adapter ~/LMemM-shadow-scratch/judge_v3 \\
        --convert-script ~/LMemM-shadow-scratch/llamacpp-v0.9.0-src/x/convert_hf_to_gguf.py \\
        --python ~/LMemM-shadow-scratch/gguf-venv/bin/python --name lmemm-judge-cpu

What it does (about a minute on an M2; it needs mlx-lm in this Python, and writes only into --work and
Ollama's own model store):
    1. fuses the LoRA adapter from train_judge.py into Qwen3-Reranker-0.6B, unquantised (bf16);
    2. imports that into Ollama, in one of two ways:
         without --convert-script: from the safetensors, quantised to 8 bits (Ollama 0.40 takes int8, not
             q8_0): about 790 MB. Ollama runs such a model on its "mlx" engine, which uses the GPU whatever
             num_gpu says;
         with --convert-script (llama.cpp's convert_hf_to_gguf.py, run by --python, which needs torch,
             transformers, numpy, sentencepiece and gguf): converted to a Q8_0 GGUF of about 640 MB, which Ollama
             runs on llama.cpp, so it honours num_gpu 0 and runs on the CPU;
    3. asks the new model one question it should say yes to and one it should say no to (asked for the CPU).

The finder uses two of these (config.FINDER_JUDGE and config.FINDER_WORK_JUDGE): the grouping judge (adapter
judge_v3), and a judge also taught "is this deliberate work, or idle looking around?" (adapter judge_v4) that
answers only that question; without them looks are skipped. Install each with its adapter and --name.
The judge_v3 build fails the "no" check below (it says yes to the vet / failing build pair, as the GPU build
does); the judge_v4 one passes.

It does nothing when the model is already there, unless --force. It never touches another model.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import project_reasoner_ai as ai  # noqa: E402
import tiny_scorer as ts  # noqa: E402

BASE = "mlx-community/Qwen3-Reranker-0.6B-4bit"
CHECKS = (("Booked the vet for the dog's vaccinations.", "Ordered a dog bed and a new leash.", True),
          ("Booked the vet for the dog's vaccinations.", "Fixed the failing build in the payments service.", False))


def check(name, url):
    """The judge's answers to CHECKS on the CPU: [(expected, log-odds)]."""
    judge = ai.Judge(ts.RerankScorer(name, url, timeout=300, extra=ts.cpu_only(2), keep_alive=0))
    return [(want, judge(ai.INSTRUCT_PAIR, a, b)) for a, b, want in CHECKS]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--adapter", type=Path, required=True, help="adapter folder written by train_judge.py")
    p.add_argument("--name", default="lmemm-judge", help="the Ollama model to create")
    p.add_argument("--base", default=BASE)
    p.add_argument("--convert-script", type=Path, default=None,
                   help="llama.cpp's convert_hf_to_gguf.py: import a Q8_0 GGUF so Ollama runs the judge on the CPU")
    p.add_argument("--python", default=sys.executable, help="the Python that runs --convert-script")
    p.add_argument("--work", type=Path, default=Path.home() / "LMemM-shadow-scratch" / "judge_fused",
                   help="where the fused model is written before the import")
    p.add_argument("--url", default="http://127.0.0.1:11434")
    p.add_argument("--force", action="store_true", help="replace the model if Ollama already has it")
    args = p.parse_args(argv)

    have = ts.installed(args.url)
    if args.name in have and not args.force:
        print(f"Ollama already has {args.name}; pass --force to build it again.")
    else:
        if not (args.adapter / "adapters.safetensors").exists():
            p.error(f"no adapters.safetensors in {args.adapter}")
        subprocess.run([sys.executable, "-m", "mlx_lm", "fuse", "--model", args.base, "--adapter-path",
                        str(args.adapter), "--save-path", str(args.work), "--dequantize"], check=True)
        if args.convert_script:
            gguf = args.work / f"{args.name}-q8.gguf"
            subprocess.run([args.python, str(args.convert_script), str(args.work), "--outtype", "q8_0",
                            "--outfile", str(gguf)], check=True)
            (args.work / "Modelfile").write_text(f"FROM {gguf}\nPARAMETER num_ctx 2048\n")
            subprocess.run(["ollama", "create", args.name, "-f", "Modelfile"], cwd=args.work, check=True)
        else:
            (args.work / "Modelfile").write_text("FROM .\n")
            subprocess.run(["ollama", "create", args.name, "-f", "Modelfile", "--quantize", "int8"],
                           cwd=args.work, check=True)
    results = check(args.name, args.url)
    for (a, b, _), (want, score) in zip(CHECKS, results):
        print(f"{'yes' if want else 'no ':3}  expected, model says {score:+.2f}  ({a} / {b})")
    ok = all((score > 0) == want for want, score in results)
    print("The judge answers as expected." if ok else "The judge did not answer as expected.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
