import json
import random
import sqlite3
import unittest
from dataclasses import replace

from trace_v2.moments import MomentProcessor, builder, run_pass
from trace_v2.moments.builder import ALGORITHM
from trace_v2.moments.similarity import PARAMS, S
from trace_v2.observations import Context, ReaderResult, ScreenshotWithheld, forget
from trace_v2.observations.queries import _decode
from trace_v2.structure.processor import read_connection
from v2_observation_helpers import PNG, T0, TempStore, click, draft

CODE = "renderer pipeline shader buffer vertex texture compile kernel raster mesh pixel frame".split()
MONEY = "invoice payroll quarter ledger budget forecast revenue audit margin vendor payable tax".split()
READ = "protein enzyme membrane cellular receptor binding peptide ligand assay sequence domain fold".split()
SHARED = "momentbuilder similarity threshold stabilise".split()


def text(vocab, seed, extra=()):
    r = random.Random(seed)
    return " ".join(r.sample(vocab, 9) + list(extra))


def context(app, title, url=None):
    window = {"title": title, "id": 1}
    if url:
        window["url"] = url
    return Context({"bundle_id": app, "name": app.split(".")[-1].title(), "pid": 5}, window,
                   {"id": "main", "bounds": [0, 0, 1440, 900], "scale": 2.0})


def add(store, i, t, app, title, body, url=None, events=(), privacy=None, withheld=False):
    ocr = ReaderResult("vision-ocr/1", "succeeded", {"lines": [{"text": body, "confidence": 0.9, "box": [0, 0, 10, 10]}]}, body)
    d = replace(draft(key=f"k{i}", t=t, ocr=ocr, events=events), context=context(app, title, url))
    if withheld:
        skipped = ReaderResult("vision-ocr/1", "withheld", error_code="excluded_app")
        d = replace(d, screenshot=ScreenshotWithheld("excluded_app", t), ocr=skipped,
                    accessibility=replace(skipped, reader_version="ax-tree/1"), events=(),
                    privacy={"withheld": True, "reason": "excluded_app"})
    return store.ingest(d).observation_id


def run(ts, now, **overrides):
    params = {**PARAMS, **overrides}
    reader = read_connection(ts.store)
    try:
        out = run_pass(ts.store, reader, now, params)
    finally:
        reader.close()
    return out


def moments(ts):
    rows = ts.store.derived.execute(
        "SELECT r.moment_id, r.revision, r.state, r.observation_count, r.start_ns, r.end_ns, r.members_encoding, r.members, "
        "m.origin, m.parent_moment_id, r.merged_into FROM moment_revisions r JOIN moments m USING (moment_id) "
        "WHERE r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=r.moment_id) ORDER BY r.start_ns").fetchall()
    return [{"id": r[0], "rev": r[1], "state": r[2], "n": r[3], "start": r[4], "end": r[5],
             "members": [m["o"] for m in _decode(r[6], r[7]) if m["role"] == "core"],
             "origin": r[8], "parent": r[9], "merged_into": r[10]} for r in rows]


def live(ts):
    return [m for m in moments(ts) if m["state"] != "merged"]


def sequence(store, start, count, app, title, vocab, base, step=3, **kw):
    return [add(store, base + i, T0 + (start + i * step) * S, app, title, text(vocab, base + i), **kw) for i in range(count)]


class FormationTest(unittest.TestCase):
    def test_unrelated_topics_form_separate_moments_in_time_order(self):
        with TempStore() as ts:
            a = sequence(ts.store, 0, 12, "com.apple.dt.Xcode", "shader.cpp", CODE, 0)
            b = sequence(ts.store, 40, 12, "com.apple.Numbers", "budget.numbers", MONEY, 100)
            c = sequence(ts.store, 80, 12, "com.apple.Preview", "paper.pdf", READ, 200)
            run(ts, T0 + 300 * S)
            ms = live(ts)
            self.assertEqual([m["members"] for m in ms], [a, b, c])
            self.assertTrue(all(m["state"] == "provisional" for m in ms))

    def test_one_context_spans_applications_when_content_is_shared(self):
        with TempStore() as ts:
            ids = []
            for i in range(30):
                app, title = (("com.microsoft.VSCode", "momentbuilder.py"), ("com.apple.Safari", "momentbuilder similarity pull request"),
                              ("com.apple.Preview", "similarity threshold notes.pdf"))[(i // 3) % 3]
                ids.append(add(ts.store, i, T0 + i * 3 * S, app, title, text(CODE, i, SHARED)))
            run(ts, T0 + 300 * S)
            ms = live(ts)
            self.assertEqual(len(ms), 1)
            self.assertEqual(ms[0]["members"], ids)

    def test_same_application_with_changed_content_starts_a_new_moment(self):
        with TempStore() as ts:
            a = sequence(ts.store, 0, 12, "com.apple.Safari", "page", CODE, 0)
            b = sequence(ts.store, 40, 12, "com.apple.Safari", "page", MONEY, 100)
            run(ts, T0 + 300 * S)
            self.assertEqual([m["members"] for m in live(ts)], [a, b])

    def test_long_gap_splits_identical_context(self):
        with TempStore() as ts:
            a = sequence(ts.store, 0, 10, "com.apple.Xcode", "same.cpp", CODE, 0)
            b = sequence(ts.store, 3 * 3600, 10, "com.apple.Xcode", "same.cpp", CODE, 100)
            run(ts, T0 + 4 * 3600 * S)
            self.assertEqual([m["members"] for m in live(ts)], [a, b])

    def test_observations_without_evidence_or_withheld_are_not_promoted(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 10, "com.apple.Xcode", "shader.cpp", CODE, 0)
            empty = add(ts.store, 50, T0 + 31 * S, "com.apple.finder", "", "")
            held = add(ts.store, 51, T0 + 32 * S, "com.apple.Xcode", "shader.cpp", text(CODE, 3), withheld=True)
            out = run(ts, T0 + 300 * S)
            self.assertEqual(out["skipped"], 2)
            skips = dict(ts.store.derived.execute("SELECT observation_id, reason FROM moment_skips"))
            self.assertEqual(skips, {empty: "no_evidence", held: "withheld"})
            self.assertTrue(all(empty not in m["members"] and held not in m["members"] for m in live(ts)))

    def test_interaction_alone_counts_as_evidence(self):
        with TempStore() as ts:
            oid = add(ts.store, 0, T0, "com.apple.finder", "", "", events=[click(T0 - S)])
            out = run(ts, T0 + 300 * S)
            self.assertEqual((out["processed"], out["skipped"]), (1, 0))
            self.assertEqual([m["members"] for m in live(ts)], [[oid]])

    def test_unready_observations_wait_then_are_processed(self):
        with TempStore() as ts:
            add(ts.store, 0, T0, "com.apple.Xcode", "a.cpp", text(CODE, 1))
            self.assertEqual(run(ts, T0 + 5 * S)["processed"], 0)
            self.assertEqual(run(ts, T0 + 60 * S)["processed"], 1)


class LifecycleTest(unittest.TestCase):
    def test_provisional_then_stable_and_stable_is_never_modified(self):
        with TempStore() as ts:
            ids = sequence(ts.store, 0, 10, "com.apple.Xcode", "shader.cpp", CODE, 0)
            run(ts, T0 + 100 * S)
            self.assertEqual(live(ts)[0]["state"], "provisional")
            run(ts, T0 + 100 * S + 1000 * S)
            m = live(ts)[0]
            self.assertEqual((m["state"], m["n"]), ("stable", 10))
            with self.assertRaises(sqlite3.DatabaseError):
                ts.store.derived.execute("INSERT INTO moment_revisions SELECT moment_id, revision+1, 'provisional', NULL, start_ns, end_ns, "
                                    "observation_count, params_json, summary_json, members_encoding, members, members_sha256, recorded_at "
                                    "FROM moment_revisions WHERE moment_id=?", (m["id"],))
            with self.assertRaises(sqlite3.DatabaseError):
                ts.store.derived.execute("UPDATE moment_revisions SET state='provisional'")

    def test_late_evidence_after_stability_forms_a_new_moment(self):
        with TempStore() as ts:
            first = sequence(ts.store, 0, 10, "com.apple.Xcode", "shader.cpp", CODE, 0)
            run(ts, T0 + 2000 * S)
            self.assertEqual(live(ts)[0]["state"], "stable")
            late = add(ts.store, 900, T0 + 20 * S, "com.apple.Xcode", "shader.cpp", text(CODE, 77))
            run(ts, T0 + 2100 * S, stabilise_s=1e9)
            ms = live(ts)
            self.assertEqual(len(ms), 2)
            self.assertEqual(ms[0]["members"], first)
            self.assertIn(late, ms[1]["members"])

    def test_too_small_moments_become_insufficient(self):
        with TempStore() as ts:
            add(ts.store, 0, T0, "com.apple.Xcode", "x.cpp", text(CODE, 1))
            run(ts, T0 + 100 * S)
            run(ts, T0 + 5000 * S)
            self.assertEqual([m["state"] for m in live(ts)], ["insufficient"])

    def test_rerun_is_idempotent_and_deterministic(self):
        def build():
            with TempStore() as ts:
                sequence(ts.store, 0, 12, "com.apple.Xcode", "shader.cpp", CODE, 0)
                sequence(ts.store, 40, 12, "com.apple.Numbers", "budget", MONEY, 100)
                run(ts, T0 + 300 * S)
                before = ts.store.derived.execute("SELECT count(*) FROM moment_revisions").fetchone()[0]
                again = run(ts, T0 + 300 * S)
                after = ts.store.derived.execute("SELECT count(*) FROM moment_revisions").fetchone()[0]
                self.assertEqual((again["processed"], again["revisions"], before), (0, 0, after))
                return [[m["id"], m["members"] == m["members"], m["n"]] for m in live(ts)], [
                    [r[0] for r in ts.store.derived.execute("SELECT moment_id FROM moments ORDER BY created_at_ns")]]
        first, second = build(), build()
        self.assertEqual(first[0][0][2:], second[0][0][2:])
        self.assertEqual(len(first[0]), len(second[0]))

    def test_incremental_batches_equal_one_batch(self):
        def build(batches):
            with TempStore() as ts:
                ids = sequence(ts.store, 0, 12, "com.apple.Xcode", "shader.cpp", CODE, 0) + \
                    sequence(ts.store, 40, 12, "com.apple.Numbers", "budget", MONEY, 100)
                for now in batches:
                    run(ts, T0 + now * S)
                return [[ids.index(o) for o in m["members"]] for m in live(ts)]
        self.assertEqual(build([300]), build([30, 60, 90, 300]))

    def test_overlapping_moments_share_an_observation_without_duplicating_it(self):
        with TempStore() as ts:
            a = sequence(ts.store, 0, 8, "com.apple.Xcode", "shader.cpp", CODE, 0)
            b = sequence(ts.store, 30, 8, "com.apple.Numbers", "budget", MONEY, 100)
            bridge = add(ts.store, 200, T0 + 60 * S, "com.apple.Notes", "note", text(CODE[:5] + MONEY[:5], 5))
            run(ts, T0 + 300 * S, secondary=0.1, join=0.6)
            rows = ts.store.db.execute("SELECT count(*) FROM observations").fetchone()[0]
            self.assertEqual(rows, 17)
            holders = ts.store.derived.execute("SELECT count(*) FROM moment_member_index WHERE observation_id=?", (bridge,)).fetchone()[0]
            self.assertGreaterEqual(holders, 1)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM screenshots").fetchone()[0], 17)

    def test_merge_of_provisional_moments_preserves_history(self):
        with TempStore() as ts:
            a = sequence(ts.store, 0, 6, "com.apple.Xcode", "shader.cpp", CODE, 0)
            b = sequence(ts.store, 60, 6, "com.apple.Xcode", "shader.cpp", CODE, 100)
            run(ts, T0 + 120 * S, join=0.99, secondary=0.99, merge=0.99)
            self.assertGreaterEqual(len(live(ts)), 12)
            c = sequence(ts.store, 120, 6, "com.apple.Xcode", "shader.cpp", CODE, 200)
            run(ts, T0 + 400 * S, join=0.3, merge=0.5)
            states = [m["state"] for m in moments(ts)]
            self.assertIn("merged", states)
            kept = [m for m in moments(ts) if m["state"] != "merged"]
            merged = [m for m in moments(ts) if m["state"] == "merged"]
            self.assertTrue(all(m["merged_into"] in {k["id"] for k in kept} for m in merged))
            union = set(sum((k["members"] for k in kept), []))
            self.assertTrue(set(a + b + c) <= union)

    def test_provisional_moment_is_split_when_context_changed_inside_it(self):
        with TempStore() as ts:
            a = sequence(ts.store, 0, 8, "com.apple.Safari", "page", CODE, 0)
            b = sequence(ts.store, 25, 8, "com.apple.Safari", "page", MONEY, 100)
            run(ts, T0 + 200 * S, join=0.05, secondary=0.99, merge=0.99, tau_s=1e6)
            ms = live(ts)
            self.assertEqual(len(ms), 2)
            self.assertEqual(ms[1]["origin"], "split")
            self.assertEqual(ms[1]["parent"], ms[0]["id"])
            self.assertEqual(set(ms[0]["members"]) | set(ms[1]["members"]), set(a + b))
            self.assertFalse(set(ms[0]["members"]) & set(ms[1]["members"]))


class RobustnessTest(unittest.TestCase):
    def test_stale_pass_writes_nothing(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 6, "com.apple.Xcode", "shader.cpp", CODE, 0)
            reader = read_connection(ts.store)
            real = builder.write
            def racing(store, reps, new_features, skips, idf, params, now_ns=None):
                for rep in reps:
                    if rep.dirty:
                        rep.revision += 5
                return real(store, reps, new_features, skips, idf, params, now_ns)
            builder.write = racing
            try:
                out = run_pass(ts.store, reader, T0 + 100 * S)
            finally:
                builder.write = real
                reader.close()
            self.assertEqual(out["stale"], 1)
            self.assertEqual(ts.store.derived.execute("SELECT count(*) FROM moment_revisions").fetchone()[0], 0)
            self.assertEqual(ts.store.derived.execute("SELECT count(*) FROM observation_features").fetchone()[0], 0)
            self.assertEqual(run(ts, T0 + 100 * S)["processed"], 6)

    def test_survives_restart_without_reprocessing(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 6, "com.apple.Xcode", "shader.cpp", CODE, 0)
            run(ts, T0 + 100 * S)
            before = moments(ts)
            ts.reopen()
            out = run(ts, T0 + 100 * S)
            self.assertEqual((out["processed"], out["revisions"]), (0, 0))
            self.assertEqual(moments(ts), before)

    def test_evidence_is_untouched(self):
        with TempStore() as ts:
            ids = sequence(ts.store, 0, 6, "com.apple.Xcode", "shader.cpp", CODE, 0)
            before = [ts.store.get(i) for i in ids]
            run(ts, T0 + 100 * S)
            self.assertEqual(before, [ts.store.get(i) for i in ids])

    def test_features_carry_extraction_provenance(self):
        with TempStore() as ts:
            oid = add(ts.store, 0, T0, "com.apple.Xcode", "shader.cpp", text(CODE, 1))
            run(ts, T0 + 100 * S)
            ocr = ts.store.db.execute("SELECT extraction_id FROM extractions WHERE observation_id=? AND reader='ocr'", (oid,)).fetchone()[0]
            row = ts.store.derived.execute("SELECT algorithm, ocr_extraction_id FROM observation_features WHERE observation_id=?", (oid,)).fetchone()
            self.assertEqual(row, ("moment-features/1", ocr))
            params = json.loads(ts.store.derived.execute("SELECT params_json FROM moment_revisions").fetchone()[0])
            self.assertEqual(params["join"], PARAMS["join"])
            self.assertEqual(ts.store.derived.execute("SELECT algorithm FROM moments").fetchone()[0], ALGORITHM)

    def test_processor_thread_runs_and_stops(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 6, "com.apple.Xcode", "shader.cpp", CODE, 0)
            proc = MomentProcessor(ts.store, interval_s=0.05, lookback_s=None, clock_ns=lambda: T0 + 100 * S)
            out = proc.run_once()
            self.assertEqual(out["processed"], 6)
            proc.start()
            proc.stop()
            self.assertEqual(len(live(ts)), 1)
            self.assertEqual(proc.stats()["processed"], 6)

    def test_processor_catches_up_two_days_but_not_further(self):
        with TempStore() as ts:
            day = 86400
            now = T0 + 5 * day * S
            old = sequence(ts.store, 5 * day - 60 * 3600, 6, "com.apple.Numbers", "budget", MONEY, 100)
            recent = sequence(ts.store, 5 * day - 10 * 3600, 6, "com.apple.Xcode", "shader.cpp", CODE, 0)
            out = MomentProcessor(ts.store, clock_ns=lambda: now).run_once()
            self.assertEqual(out["processed"], 6)
            members = {i for m in live(ts) for i in m["members"]}
            self.assertEqual(members, set(recent))

    def test_large_history_pass_is_fast(self):
        import time as _time
        with TempStore() as ts:
            for blk in range(20):
                sequence(ts.store, blk * 400, 15, "com.apple.Xcode", f"f{blk}.cpp", (CODE, MONEY, READ)[blk % 3], blk * 1000)
            started = _time.perf_counter()
            reader = read_connection(ts.store)
            out = run_pass(ts.store, reader, T0 + 20 * 400 * S + 10000 * S, limit=1000)
            reader.close()
            self.assertEqual(out["processed"], 300)
            self.assertLess(_time.perf_counter() - started, 10)


class ForgetTest(unittest.TestCase):
    def test_forget_removes_moments_with_the_observation_and_reforms_the_rest(self):
        with TempStore() as ts:
            a = sequence(ts.store, 0, 8, "com.apple.Xcode", "shader.cpp", CODE, 0)
            b = sequence(ts.store, 40, 8, "com.apple.Numbers", "budget", MONEY, 100)
            run(ts, T0 + 300 * S)
            self.assertEqual(len(live(ts)), 2)
            forget.forget(ts.store, {"observation_ids": [a[3]]}, dry_run=False)
            self.assertEqual([m["members"] for m in live(ts)], [b])
            for table in ("observation_features", "moment_skips", "moment_member_index"):
                n = ts.store.derived.execute(f"SELECT count(*) FROM {table} WHERE observation_id=?", (a[3],)).fetchone()[0]
                self.assertEqual(n, 0)
            out = run(ts, T0 + 300 * S)
            self.assertEqual(out["processed"], 7)
            self.assertEqual(sorted(len(m["members"]) for m in live(ts)), [7, 8])
            forget.forget(ts.store, {"everything": True}, dry_run=False)
            for table in ("moments", "moment_revisions", "moment_member_index", "observation_features", "moment_skips"):
                self.assertEqual(ts.store.derived.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_derived_rows_cannot_be_deleted_without_forget(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 6, "com.apple.Xcode", "shader.cpp", CODE, 0)
            run(ts, T0 + 100 * S)
            for table in ("moment_revisions", "moments", "moment_member_index", "observation_features"):
                with self.assertRaises(sqlite3.DatabaseError):
                    ts.store.derived.execute(f"DELETE FROM {table}")


if __name__ == "__main__":
    unittest.main()


class IncrementalTest(unittest.TestCase):
    def passes(self, ts, times, state):
        reader = read_connection(ts.store)
        try:
            params = {**PARAMS, "revision_every_s": 0.0}
            return [run_pass(ts.store, reader, T0 + t * S, params, state=state) for t in times]
        finally:
            reader.close()

    def test_cached_passes_match_database_passes(self):
        results = []
        for cached in (False, True):
            with TempStore() as ts:
                sequence(ts.store, 0, 10, "com.apple.dt.Xcode", "shader.cpp", CODE, 0)
                sequence(ts.store, 40, 10, "com.apple.Numbers", "budget.numbers", MONEY, 100)
                state = {} if cached else None
                self.passes(ts, (100, 200, 300), state)
                results.append([(m["n"], len(m["members"]), m["state"]) for m in live(ts)])
        self.assertEqual(results[0], results[1])

    def test_growth_revisions_are_throttled(self):
        with TempStore() as ts:
            state = {}
            reader = read_connection(ts.store)
            try:
                for i in range(12):
                    add(ts.store, i, T0 + i * 3 * S, "com.apple.dt.Xcode", "shader.cpp", text(CODE, i))
                    run_pass(ts.store, reader, T0 + (i * 3 + 60) * S, PARAMS, state=state)
            finally:
                reader.close()
            revisions = ts.store.derived.execute("SELECT count(*) FROM moment_revisions").fetchone()[0]
            self.assertLess(revisions, 6)
            self.assertEqual(len(live(ts)), 1)


class LongMomentTest(unittest.TestCase):
    PARAMS = {"min_length_s": 120.0, "pause_s": 30.0, "max_length_s": 300.0, "cap_pause_s": 6.0, "hard_slack_s": 60.0}

    def test_a_pause_after_a_long_stretch_closes_the_moment(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 60, "com.apple.dt.Xcode", "shader.cpp", CODE, 0, step=3)
            sequence(ts.store, 180 + 60, 20, "com.apple.dt.Xcode", "shader.cpp", CODE, 100, step=3)
            run(ts, T0 + 400 * S, **self.PARAMS)
            ms = live(ts)
            self.assertEqual(len(ms), 2)
            self.assertEqual(ms[0]["state"], "stable")
            self.assertLess(ms[0]["end"], ms[1]["start"])

    def test_without_a_long_stretch_a_pause_does_not_split(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 10, "com.apple.dt.Xcode", "shader.cpp", CODE, 0, step=3)
            sequence(ts.store, 30 + 60, 10, "com.apple.dt.Xcode", "shader.cpp", CODE, 100, step=3)
            run(ts, T0 + 400 * S, **self.PARAMS)
            self.assertEqual(len(live(ts)), 1)

    def test_a_never_ending_moment_is_cut_at_the_maximum_even_without_a_pause(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 130, "com.apple.dt.Xcode", "shader.cpp", CODE, 0, step=3)
            run(ts, T0 + 500 * S, **self.PARAMS)
            ms = live(ts)
            self.assertGreaterEqual(len(ms), 2)
            self.assertEqual(ms[0]["state"], "stable")
            self.assertLessEqual((ms[0]["end"] - ms[0]["start"]) / S, 300 + 60 + 3)


class OcrWaitTest(unittest.TestCase):
    def pending(self, ts, i, t):
        ocr = ReaderResult("vision-ocr/1", "pending")
        d = replace(draft(key=f"p{i}", t=t, ocr=ocr), context=context("com.apple.dt.Xcode", "shader.cpp"))
        return ts.store.ingest(d).observation_id

    def test_an_observation_waits_for_unfinished_ocr_until_the_cap(self):
        with TempStore() as ts:
            self.pending(ts, 0, T0)
            self.assertEqual(run(ts, T0 + 100 * S)["processed"], 0)
            self.assertEqual(run(ts, T0 + 100 * S)["skipped"], 0)
            out = run(ts, T0 + 400 * S)
            self.assertEqual(out["processed"] + out["skipped"], 1)
