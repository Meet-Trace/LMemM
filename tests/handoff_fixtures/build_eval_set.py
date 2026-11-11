"""Builds conversations-v1.json: 55 synthetic conversations with the facts a handoff must keep.

    python3 tests/handoff_fixtures/build_eval_set.py          # rewrite the JSON
    python3 tests/handoff_fixtures/build_eval_set.py --check  # fail if the JSON is out of date

Every conversation is invented. Change the set by editing this file and bumping VERSION; the
committed JSON is what the evaluation test reads, and it must match this script's output.

Each fixture's `expect` lists substrings (case-insensitive) that must appear in the named part of
the handoff, `forbid_*` lists substrings that must not, and `cite` maps a substring to the message
number (1-based) that its line must reference.
"""

import json
import os
import sys

VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "conversations-v1.json")


def fx(cat, n, msgs, **expect):
    return {"id": f"{cat}-{n:02d}", "category": cat,
            "messages": [{"role": r, "text": t} for r, t in msgs], "expect": expect}


U, A = "user", "assistant"
out = []

# --- software development (7)
DEV = [
    ("a sync service", "work offline", "use a paid backend", "CRDTs", "polling", "the document merge", "sync/merge.py", "the network layer", "sync/transport.py", "block the UI thread"),
    ("a CLI importer", "stream large files", "load a whole file into memory", "iterators", "slurping", "the argument parser", "cli/args.py", "the progress bar", "cli/progress.py", "print secrets"),
    ("a billing webhook", "be idempotent", "drop events", "queues", "cron", "the signature check", "billing/verify.py", "the retry worker", "billing/retry.py", "log card numbers"),
    ("a search index", "rebuild in under a minute", "add a new database", "SQLite", "Elasticsearch", "the tokenizer", "search/tokens.py", "the ranking pass", "search/rank.py", "change the public API"),
    ("a mobile login flow", "support biometrics", "store passwords on the device", "keychain", "cookies", "the form layout", "ui/login.swift", "the lockout timer", "ui/lockout.swift", "skip accessibility labels"),
    ("a static site generator", "run without network access", "depend on Node", "templates", "plugins", "the markdown reader", "gen/reader.py", "the sitemap writer", "gen/sitemap.py", "break existing URLs"),
    ("a metrics exporter", "add under 5 ms of overhead", "block the request path", "sampling", "tracing", "the counter registry", "metrics/registry.go", "the HTTP endpoint", "metrics/http.go", "allocate in the hot loop"),
]
for i, (thing, c1, c2, a, b, part, f1, todo, f2, c3) in enumerate(DEV, 1):
    out.append(fx("dev", i, [
        (U, f"I'm building {thing}. It must {c1} and we can't {c2}."),
        (A, f"You could use {a}, or maybe {b}. Perhaps start with {b}."),
        (U, f"Let's go with {a} instead of {b}."),
        (A, f"Done: I implemented {part} in {f1}. Still need to write {todo}."),
        (U, f"Great. Now write {todo} for {f2}. Never {c3}."),
    ], task=[todo], constraints=[f"must {c1}", f"can't {c2}", f"Never {c3}"], decisions=[a], rejected=[b],
        completed=[part], outstanding=[todo], artifacts=[f1, f2], forbid_in_decisions=["Perhaps", "maybe"],
        cite={f"must {c1}": 1, f"Never {c3}": 5}))

# --- research and literature review (6)
RES = [("battery chemistry", 2019, "Okafor", 12), ("coral bleaching", 2018, "Lindqvist", 9), ("sleep and memory", 2020, "Hartmann", 15),
       ("urban heat islands", 2017, "Sato", 8), ("gut microbiome", 2021, "Mbeki", 11), ("quantum error correction", 2016, "Novak", 14)]
for i, (topic, year, who, n) in enumerate(RES, 1):
    out.append(fx("research", i, [
        (U, f"I'm reviewing papers on {topic}. The review must cover only studies after {year}."),
        (A, f"I found three candidates. Maybe include the {who} study, it might be relevant."),
        (U, f"We decided against the {who} study, it is outside the scope."),
        (A, f"Finished: summarised {n} papers in notes/summary.md. Still need to compare their methods."),
        (U, f"Next, compare the methods across the papers. Don't cite preprints."),
    ], task=["compare the methods"], constraints=[f"only studies after {year}", "Don't cite preprints"],
        rejected=[who], completed=[f"summarised {n} papers"], outstanding=["compare their methods"], artifacts=["notes/summary.md"],
        forbid_in_decisions=[who], cite={"Don't cite preprints": 5}))

# --- product strategy (6)
STR = [("Atlas", "50k dollars", "students", "agencies", "freemium", "competitors"), ("Beacon", "80k dollars", "clinics", "hospitals", "usage-based", "pricing history"),
       ("Cobalt", "20k dollars", "freelancers", "studios", "seat-based", "unreleased features"), ("Drift", "120k dollars", "retailers", "wholesalers", "tiered", "competitors"),
       ("Ember", "35k dollars", "teachers", "districts", "freemium", "student data"), ("Fable", "60k dollars", "farmers", "co-ops", "annual", "supplier names")]
for i, (prod, bud, segA, segB, model, avoid) in enumerate(STR, 1):
    out.append(fx("strategy", i, [
        (U, f"We're planning the {prod} launch. The budget must stay under {bud}."),
        (A, f"We could target {segA} or perhaps {segB}. Maybe both."),
        (U, f"Let's go with {segA}."),
        (A, f"Agreed? We will use a {model} model."),
        (U, "Yes."),
        (U, f"Draft the pricing page copy for {prod}. Avoid mentioning {avoid}."),
    ], task=["pricing page copy"], constraints=[f"stay under {bud}", f"Avoid mentioning {avoid}"], decisions=[segA, model],
        forbid_in_decisions=[segB, "Maybe"], cite={f"stay under {bud}": 1}))

# --- consulting deliverables (6)
CON = [("Northwind", "10", "financial section"), ("Contoso", "8", "risk section"), ("Fabrikam", "12", "roadmap section"),
       ("Litware", "6", "cost model"), ("Tailspin", "15", "market sizing"), ("Wingtip", "9", "implementation plan")]
for i, (client, n, sec) in enumerate(CON, 1):
    out.append(fx("consulting", i, [
        (U, f"I need a board deck for {client}. It has to be under {n} slides."),
        (A, "Maybe open with the market overview. You might add an appendix."),
        (U, "We decided against an appendix. Let's use the executive summary first."),
        (A, f"Completed: drafted the executive summary. Remaining: the {sec}."),
        (U, f"Write the {sec} now. Never include client names."),
    ], task=[sec], constraints=[f"under {n} slides", "Never include client names"], rejected=["appendix"],
        decisions=["executive summary"], completed=["drafted the executive summary"], outstanding=[sec],
        forbid_in_decisions=["Maybe", "might"], cite={f"under {n} slides": 1}))

# --- multi-step troubleshooting (6)
TRB = [("billing service", "E1042", "connection pool", "billing/db.py"), ("auth proxy", "E77", "token cache", "proxy/cache.py"),
       ("media encoder", "E310", "thread limit", "encode/pool.py"), ("mail relay", "E550", "queue depth", "relay/queue.py"),
       ("map tiler", "E9", "memory ceiling", "tiler/mem.py"), ("chat gateway", "E404", "socket timeout", "gateway/ws.py")]
for i, (svc, code, res, f) in enumerate(TRB, 1):
    out.append(fx("troubleshoot", i, [
        (U, f"The {svc} crashes on startup with error {code}. It must not lose data."),
        (A, f"It might be the config. Perhaps the {res} is too low."),
        (U, f"I checked: the config is fine. We ruled out the {res}."),
        (A, f"Fixed: the null check in {f}. Still need to rerun the integration tests."),
        (U, "Rerun the integration tests and tell me what fails. Don't restart the production database."),
    ], task=["integration tests"], constraints=["must not lose data", "Don't restart the production database"],
        rejected=[res], completed=["null check"], outstanding=["rerun the integration tests"], artifacts=[f],
        forbid_in_decisions=["Perhaps", "might"], cite={"must not lose data": 1}))

# --- long-running tasks (6): the early constraint must survive 30+ turns of filler
LONG = [("billing tables", "reversible"), ("user records", "resumable"), ("image bucket", "verifiable by checksum"),
        ("search shards", "online"), ("audit logs", "append-only"), ("session store", "zero-downtime")]
for i, (thing, prop) in enumerate(LONG, 1):
    msgs = [(U, f"We're migrating the {thing}. The migration must be {prop}.")]
    for k in range(1, 17):
        msgs += [(U, f"Step {k}: run the migration batch {k} for the {thing}."), (A, f"Batch {k} of the migration ran for the {thing}.")]
    msgs.append((U, f"Continue the migration of the {thing} with the final batch. Never run it during business hours."))
    out.append(fx("long", i, msgs, task=["final batch"], constraints=[f"must be {prop}", "Never run it during business hours"],
                  cite={f"must be {prop}": 1}, max_chars=6000))

# --- conflicting / superseded requirements (6)
SUP = [("PostgreSQL", "SQLite", "single-user app"), ("tabs", "spaces", "team style guide"), ("REST", "gRPC", "streaming needs"),
       ("Jenkins", "GitHub", "fewer moving parts"), ("MongoDB", "DynamoDB", "managed hosting"), ("Webpack", "Vite", "faster builds")]
for i, (old, new, why) in enumerate(SUP, 1):
    out.append(fx("superseded", i, [
        (U, f"Let's use {old} for this project."),
        (A, f"Okay, {old} it is."),
        (U, f"Actually, let's switch from {old} to {new}, because of the {why}."),
        (U, f"Now set up the project skeleton with {new}."),
    ], task=["project skeleton"], decisions=[new], rejected=[old], forbid_in_decisions=[f"use {old}"]))

# --- incomplete work (6)
INC = [("login page markup", "form validation", "error banners"), ("data loader", "schema checks", "retry logic"),
       ("export button", "CSV writer", "download toast"), ("nav bar", "mobile menu", "focus styles"),
       ("report query", "date filters", "pagination"), ("upload widget", "size limits", "progress events")]
for i, (x, y, z) in enumerate(INC, 1):
    out.append(fx("incomplete", i, [
        (U, "Help me finish the settings screen this week."),
        (A, f"Done: {x}. Not yet done: {y}. Still need to add {z}."),
        (U, f"Pick up {z} next. It must work with the keyboard only."),
    ], task=[z], constraints=["keyboard only"], completed=[x], outstanding=[y, z], forbid_in_completed=[y]))

# --- several unrelated tasks in one conversation (6)
MUL = [("trip", "500 dollars", "flights", "invoices", "10 MB"), ("garden", "20 plants", "seeds", "reports", "5 MB"),
       ("wedding", "80 guests", "caterers", "payroll", "2 MB"), ("move", "two weeks", "boxes", "tickets", "1 MB"),
       ("party", "a quiet venue", "balloons", "logs", "8 MB"), ("renovation", "one bathroom", "tiles", "receipts", "3 MB")]
for i, (old, oc, oword, new, limit) in enumerate(MUL, 1):
    out.append(fx("multitask", i, [
        (U, f"Plan my {old}: it needs {oc}. Never book {oword} before Tuesday."),
        (A, "Noted."), (U, "Thanks."), (A, "Anything else?"), (U, "No."), (A, "Ok."),
        (U, f"Different thing: write the parser for the {new} export."),
        (A, "Sure, here is a start."),
        (U, f"The {new} parser must reject files over {limit}."),
        (A, "Okay."),
        (U, f"Also make the {new} parser stream rows."),
        (A, "Done: the streaming parser works."),
    ], task=[f"{new} parser"], constraints=[f"reject files over {limit}"], forbid_in_text=[oword], cite={f"reject files over {limit}": 9}))

assert len(out) >= 50, len(out)
DOC = {"version": VERSION, "note": "Synthetic. Generated by build_eval_set.py - do not edit by hand.", "conversations": out}


def main():
    text = json.dumps(DOC, indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        ok = os.path.exists(OUT) and open(OUT, encoding="utf-8").read() == text
        print("up to date" if ok else "OUT OF DATE: run build_eval_set.py")
        return 0 if ok else 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {len(out)} conversations to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
