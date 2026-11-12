# LLM Activity inference experiment (isolated; not production)

One question: does semantic reasoning from a small local model materially improve Activity inference over the deterministic
engine, while staying accurate, fast and memory-efficient enough for Trace?

Nothing under `src/`, no schema, no Moment, Visit, annotation or evaluation file is changed. Everything is written to the OUT
folder you name (outside the repo). Standard library only. Local only: the model runs in a separate Ollama server on
127.0.0.1:11436, nothing is sent anywhere. Keep OUT local: the cache contains short quotes from your screen text.

## Sequence on your Mac (use the dataset `~/Desktop/annot-nov11/dataset`)

1. Get the code: `cd ~/Desktop/LMemM && git pull origin claude/project-thread-y53y53`
2. Restart the Mac. Do not open LMemM or other apps. In Terminal:
   `python3 -I experiments/llm_activity/llm_activity.py env ~/Desktop/llm-exp --label after-restart`
3. In a second Terminal window leave this running (a separate server; it does not touch your other Ollama):
   `OLLAMA_HOST=127.0.0.1:11436 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_LLM_LIBRARY=cpu ollama serve`
4. Verify CPU-only, empirically (loads gemma3:1b, runs 3 generations, samples GPU, CPU and memory):
   `python3 -I experiments/llm_activity/llm_activity.py cpucheck ~/Desktop/llm-exp --model gemma3:1b`
   Do not continue unless it prints `VERDICT: CPU-only confirmed`.
5. Candidate retrieval and candidate recall (no model):
   `python3 -I experiments/llm_activity/llm_activity.py retrieve ~/Desktop/annot-nov11/dataset ~/Desktop/llm-exp`
5b. Retrieval diagnosis (no model; read-only; baseline `retrieve` is untouched):
   `python3 -I experiments/llm_activity/retrieval_diagnosis.py ~/Desktop/annot-nov11/dataset ~/Desktop/llm-exp --private > ~/Desktop/retrieval-diagnosis.txt`
   Why related pairs were missed, connectivity through intermediate Visits, and six fixed label-free expansions compared with the
   baseline. Exploratory: one day, labels never used to build candidates, settings fixed in advance.
6. Timing trial (about 60 pairs), then the full run. Resumable and cached: Ctrl-C is safe, rerun continues.
   `... judge ~/Desktop/annot-nov11/dataset ~/Desktop/llm-exp --model gemma3:1b --max-pairs 60`
   `... judge ~/Desktop/annot-nov11/dataset ~/Desktop/llm-exp --model gemma3:1b`
7. Group-level questions and the B and C reconstructions: `... build ~/Desktop/annot-nov11/dataset ~/Desktop/llm-exp --model gemma3:1b`
8. Report (add `--private` to hide window titles when you paste it):
   `... report ~/Desktop/annot-nov11/dataset ~/Desktop/llm-exp --model gemma3:1b > ~/Desktop/llm-report.txt`
9. Secondary model on a representative subset (same subset for both models):
   `... judge ... --model qwen2.5:0.5b --subset 150` then `... report ... --model qwen2.5:0.5b --subset 150` and
   `... report ... --model gemma3:1b --subset 150`
   (restart the server between models if memory is tight; run the model's own `cpucheck` first)

## What each approach is
- A: existing inference, defaults.
- B: model pair answers (both orders unless the first says different) + group-level questions for merges between established
  clusters with one direct link. No engine evidence or cannot-links.
- C: the same cached answers with deterministic validation: cannot-links, evidence completeness, cited evidence must be
  checkable in the cards, merges refused when judged-different cross pairs outnumber same links; same bridge rules.
- Bridge rules (both): a singleton attaches through a direct link; a singleton linked equally to two clusters attaches to neither;
  two established clusters merge only with two vertex-disjoint direct links or a group-level "same" in both orders.
- Model confidence is recorded and shown descriptively; it is never used as a probability or to choose an answer.
- The `UB` line is an upper bound (a perfect judge on the same candidates), not a result.
