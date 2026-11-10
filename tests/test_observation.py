"""The shared Observation record: merging the accessibility trail and OCR, conservative matching, the log both
readers use, and the excerpts that come out of it. Pure Python (no macOS)."""
import json
import os
import random
import re
import tempfile
import threading
import unittest
from datetime import datetime

import config
import observation as ob
from memory_content import CONTENT_KINDS, DECISION, MAX_TEXT, extract_content, remember_content, screen_content
from observation import Line, Observation, ObservationLog, merge, merge_report, same_place
from test_trail import FakeFront, FakeReader, snap
from trail_engine import Engine
from trail_store import TrailStore

AX = lambda t: Line(t, "ax")
OCR = lambda t, c=0.9, kind="paragraph": Line(t, "ocr", c, kind, [250, 200, 450, 30], True)


def obs(reader, lines, t=0.0, **kw):
    kw.setdefault("bundle_id", "com.brave.Browser")
    kw.setdefault("window", "WhatsApp")
    kw.setdefault("url", "https://web.whatsapp.com/")
    return Observation(t=t, reader=reader, lines=lines, **kw)


PAGE = ["180DC DTU Partnership Leads | 2026 - 27", "Ganant Awasthi Times 180M", "Reminder: APAC Consulting interview",
        "Sunday Sep 27, 2026 at 12:30am"]
NOISY = ["180DC DTU Partner$hip Lead$12026- 27", "Ganant AwasthiTime$ 180M", "Reminder: APAC Consuiting interview",
         "Sunday Sep 27, 2026 at 12:30am"]


class ProvenanceTests(unittest.TestCase):
    def test_source_confidence_and_agreement_are_separate_facts(self):
        out = merge([obs("trail", [AX(t) for t in PAGE]), obs("tracker", [OCR(t, 0.62) for t in NOISY])])
        first = out[0]
        self.assertEqual(first.text, PAGE[0])                      # the app's text is the line...
        self.assertEqual((first.source, first.sources), ("ax", ["ax", "ocr"]))
        self.assertIsNone(first.conf)                              # ...and the accessibility tree reports no confidence
        self.assertEqual(first.agreement, ob.DIFFERED)             # similar, not the same line: kept as a disagreement
        self.assertEqual(first.variants, [{"text": NOISY[0], "source": "ocr", "conf": 0.62}])
        self.assertEqual(out[1].agreement, ob.AGREED)              # the same line once look-alike characters ($ for s) are unified...
        self.assertEqual(out[1].variants[0]["text"], NOISY[1])    # ...and the OCR original is still preserved
        self.assertEqual(out[3].agreement, ob.AGREED)
        self.assertEqual(out[3].variants, [])                      # nothing to preserve when they read it alike

    def test_a_line_one_reader_saw_is_single_source_whatever_its_confidence(self):
        out = merge([obs("tracker", [OCR("The websitelook5 very Al gernrated", 0.99)])])
        self.assertEqual([(m.source, m.agreement, m.conf) for m in out], [("ocr", ob.SINGLE, 0.99)])
        shared = ["First line both readers saw clearly", "Second line both readers saw clearly"]
        out = merge([obs("trail", [AX(t) for t in shared] + [AX("A line only the app exposed here")]),
                     obs("tracker", [OCR(t) for t in shared] + [OCR("Another line OCR saw alone")])])
        self.assertEqual([(m.source, m.agreement) for m in out],
                         [("ax", ob.AGREED), ("ax", ob.AGREED), ("ax", ob.SINGLE), ("ocr", ob.SINGLE)])

    def test_originals_are_never_modified_by_matching(self):
        lines = [OCR(t) for t in NOISY]
        before = [l.text for l in lines]
        merge([obs("trail", [AX(t) for t in PAGE]), obs("tracker", lines)])
        self.assertEqual([l.text for l in lines], before)

    def test_counts(self):
        out = merge([obs("trail", [AX(t) for t in PAGE] + [AX("Only the app has this line")]),
                     obs("tracker", [OCR(t) for t in NOISY] + [OCR("Only ocr has this one")])])
        self.assertEqual(ob.counts(out), {"ax_only": 1, "ocr_only": 1, "agreed": 2, "differed": 2})


class ConservativeMatchingTests(unittest.TestCase):
    def test_wrapped_and_spanning_lines(self):
        out = merge([obs("trail", [AX("We decided to use SQLite because the data stays on the machine"), AX("Padding line number one here"),
                                   AX("Meeting moved to Thursday"), AX("bring the signed contract")]),
                     obs("tracker", [OCR("We decided to use SQLite because the data"), OCR("Padding line number one here"),
                                     OCR("Meeting moved to Thursday bring the signed contract")])])
        self.assertEqual(len(out), 4)
        self.assertTrue(all(m.agreement == ob.AGREED for m in out))

    def test_an_ocr_line_that_fits_two_app_lines_is_left_alone(self):
        app = [AX("Order 4412 shipped to Delhi warehouse"), AX("Order 4413 shipped to Delhi warehouse"), AX("Unrelated filler content line")]
        report = merge_report([obs("trail", app), obs("tracker", [OCR("Order 4412 shipped to Delhi warehouse"),
                                                                  OCR("Order 441Z shipped to Delhi warehouse"), OCR("Unrelated filler content line")])])
        out, _ = report
        garbled = [m for m in out if m.source == "ocr"]
        self.assertEqual([m.text for m in garbled], ["Order 441Z shipped to Delhi warehouse"])   # ambiguous: not replaced
        self.assertEqual(garbled[0].agreement, ob.SINGLE)

    def test_two_ocr_lines_cannot_both_replace_one_app_line_by_similarity(self):
        out, _ = merge_report([obs("trail", [AX("Total amount due is 1,200 rupees"), AX("Filler content for the gate here"), AX("Another filler for the gate too")]),
                               obs("tracker", [OCR("Total amount due is 1,260 rupees"), OCR("Total amount due is 1,200 rupees."),
                                               OCR("Filler content for the gate here"), OCR("Another filler for the gate too")])])
        texts = [m.text for m in out]
        self.assertIn("Total amount due is 1,200 rupees", texts)
        self.assertEqual(sum(1 for m in out if m.source == "ocr"), 1)         # the exact-after-normalising repeat joined; the 1,260 one stays alone

    def test_lines_that_differ_in_a_digit_are_not_confused_when_the_app_has_both(self):
        out = merge([obs("trail", [AX("Table 3 is booked for six people"), AX("Table 5 is booked for six people")]),
                     obs("tracker", [OCR("Table 5 is booked for six people"), OCR("Table 3 is booked for six people")])])
        self.assertEqual([(m.text, m.agreement) for m in out],
                         [("Table 3 is booked for six people", ob.AGREED), ("Table 5 is booked for six people", ob.AGREED)])

    def test_a_different_window_with_the_same_title_is_not_merged(self):
        other = ["Quarterly numbers for the sales team", "Pipeline review notes from Monday", "Hiring plan for the next quarter"]
        mine = ["Draft email to the printing company", "Invoice 4412 needs another approval", "Remind Priya about the deadline"]
        a, o = obs("trail", [AX(t) for t in other], window="Untitled", url=None), obs("tracker", [OCR(t) for t in mine], window="Untitled", url=None)
        self.assertTrue(same_place(a, o))                          # the title alone cannot tell the windows apart...
        out, report = merge_report([a, o])
        self.assertEqual(report["gate"], "disagrees")              # ...the lines can
        self.assertEqual([m.text for m in out], mine)
        self.assertTrue(all(m.source == "ocr" for m in out))

    def test_different_os_window_ids_never_merge(self):
        self.assertFalse(same_place(obs("trail", [], window_id=7), obs("tracker", [], window_id=9)))
        self.assertFalse(same_place(obs("trail", [], pid=10), obs("tracker", [], pid=11)))

    def test_too_little_agreement_means_the_app_read_is_ignored(self):
        lines = ["Line number %d of a long list of text" % i for i in range(10)]
        out, report = merge_report([obs("trail", [AX("Line number 1 of a long list of text")] + [AX("Something else %d is here now" % i) for i in range(9)]),
                                    obs("tracker", [OCR(t) for t in lines])])
        self.assertEqual(report["gate"], "disagrees")
        self.assertEqual([m.text for m in out], lines)

    def test_ocr_with_nothing_substantial_keeps_the_app_text_as_single_source(self):
        out, report = merge_report([obs("trail", [AX("Some app text that is long enough"), AX("Another app line that is long")]),
                                    obs("tracker", [OCR("ok"), OCR("x", 0.4, "text")])])
        self.assertEqual(report["gate"], "ax_only")
        ax = [m for m in out if m.source == "ax"]
        self.assertEqual([m.text for m in ax], ["Some app text that is long enough", "Another app line that is long"])
        self.assertTrue(all(m.agreement == ob.SINGLE and m.sources == ["ax"] and m.variants == [] for m in ax))
        self.assertEqual(ob.counts(out)["agreed"], 0)

    def test_an_app_read_with_no_ocr_at_all_is_single_source(self):
        out, report = merge_report([obs("trail", [AX("Only the app could read this line")])])
        self.assertEqual((report["gate"], [(m.source, m.agreement) for m in out]), ("ax_only", [("ax", ob.SINGLE)]))

    def test_an_app_read_that_cannot_name_its_app_and_page_is_never_used(self):
        out, report = merge_report([obs("trail", [AX("Only the app could read this line")], bundle_id=None, window=None, url=None),
                                    obs("tracker", [OCR("ok")])])
        self.assertEqual(report["gate"], "unreliable_context")
        self.assertTrue(all(m.source == "ocr" for m in out))

    def test_the_agreement_gate_still_applies_when_the_ocr_has_substantial_lines(self):
        _, report = merge_report([obs("trail", [AX("Some app text that is long enough")]),
                                  obs("tracker", [OCR("A completely different line on screen")])])
        self.assertEqual(report["gate"], "disagrees")

    def test_unrelated_lines_stay_separate_and_short_lines_need_exact_matches(self):
        out = merge([obs("trail", [AX("Shared long line on the page"), AX("A second shared line on the page"), AX("Remind me to change this as per the guidelines"), AX("Search")]),
                     obs("tracker", [OCR("Shared long line on the page"), OCR("A second shared line on the page"), OCR("Add project pricing document in this folder"),
                                     OCR("Search"), OCR("Seaech")])])
        got = {m.text: (m.source, m.agreement) for m in out}
        self.assertEqual(got, {"Shared long line on the page": ("ax", ob.AGREED), "A second shared line on the page": ("ax", ob.AGREED), "Remind me to change this as per the guidelines": ("ax", ob.SINGLE),
                               "Search": ("ax", ob.AGREED), "Add project pricing document in this folder": ("ocr", ob.SINGLE),
                               "Seaech": ("ocr", ob.SINGLE)})


class SamePlaceTests(unittest.TestCase):
    def test_same_page_and_title(self):
        self.assertTrue(same_place(obs("trail", []), obs("tracker", [], window="WhatsApp - Brave")))

    def test_different_page_title_url_or_app_never_merge(self):
        base = obs("trail", [])
        self.assertFalse(same_place(base, obs("tracker", [], url="https://mail.google.com/mail/u/0/")))
        self.assertFalse(same_place(base, obs("tracker", [], window="Inbox (3)", url=None)))
        self.assertFalse(same_place(base, obs("tracker", [], bundle_id="com.apple.Notes")))

    def test_two_gmail_threads_do_not_merge_even_though_the_path_matches(self):
        a = obs("trail", [], url="https://mail.google.com/mail/u/0/#inbox/abc", window="Re: Offer")
        b = obs("tracker", [], url="https://mail.google.com/mail/u/0/#inbox/xyz", window="Re: Offer")
        self.assertFalse(same_place(a, b))

    def test_nothing_concrete_to_compare_means_no_merge(self):
        self.assertFalse(same_place(obs("trail", [], window=None, url=None), obs("tracker", [], window=None, url=None)))


class LogLifecycleTests(unittest.TestCase):
    def test_a_read_just_before_matches_an_old_one_does_not(self):
        log = ObservationLog()
        log.add(obs("trail", [AX("hello there my friend")], t=100))
        self.assertIsNotNone(log.match(obs("tracker", [], t=104)))
        self.assertIsNone(log.match(obs("tracker", [], t=100 + config.OBS_MAX_AGE + 1)))

    def test_a_reader_does_not_match_itself_and_ocr_trail_reads_are_not_accessibility(self):
        log = ObservationLog()
        log.add(obs("tracker", [OCR("some line of text here")], t=100))
        log.add(obs("trail", [Line("fast screenshot read here", "ocr", 0.7)], t=101))
        self.assertIsNone(log.match(obs("tracker", [], t=102)))

    def test_another_page_is_not_returned(self):
        log = ObservationLog()
        log.add(obs("trail", [AX("hello there my friend")], t=100, url="https://mail.google.com/"))
        self.assertIsNone(log.match(obs("tracker", [], t=101)))
        self.assertEqual(len(log.near(obs("tracker", [], t=101))), 1)

    def test_size_is_bounded(self):
        log = ObservationLog(size=3)
        for i in range(10):
            log.add(obs("trail", [AX("line %d of text" % i)], t=i))
        self.assertEqual(len(log), 3)

    def test_old_entries_expire_relative_to_the_newest(self):
        log = ObservationLog(retention=60)
        log.add(obs("trail", [AX("first read of the page")], t=0))
        log.add(obs("trail", [AX("second read of the page")], t=50))
        log.add(obs("trail", [AX("third read of the page")], t=100))
        self.assertEqual(len(log), 2)                              # t=0 is more than 60 s before t=100

    def test_out_of_order_arrivals_are_kept_in_time_order_and_matched_by_time(self):
        log = ObservationLog()
        log.add(obs("trail", [AX("read taken later on")], t=110))
        log.add(obs("trail", [AX("read taken earlier on")], t=100))                # arrives second, happened first
        self.assertEqual([o.t for o in reversed(log.near(obs("tracker", [], t=105)))], [100, 110])
        self.assertEqual(log.match(obs("tracker", [], t=101)).lines[0].text, "read taken earlier on")
        late = ObservationLog(retention=60)
        late.add(obs("trail", [AX("recent read of the page")], t=200))
        late.add(obs("trail", [AX("ancient read of the page")], t=10))             # already past retention: dropped
        self.assertEqual(len(late), 1)

    def test_an_exact_repeat_refreshes_the_time_instead_of_duplicating(self):
        log = ObservationLog()
        a = log.add(obs("trail", [AX("same read of the page")], t=100))
        b = log.add(obs("trail", [AX("same read of the page")], t=104))
        self.assertIs(a, b)
        self.assertEqual((len(log), a.t), (1, 104))
        log.add(obs("trail", [AX("same read of the page")], t=90))               # an older copy never moves time back
        self.assertEqual(a.t, 104)
        log.add(obs("trail", [AX("a changed read of the page")], t=105))
        self.assertEqual(len(log), 2)

    def test_clear_forgets_everything_and_nothing_is_written_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                log = ObservationLog()
                log.add(obs("trail", [AX("hello there my friend")], t=1))
                log.clear()
                self.assertEqual(len(log), 0)
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(cwd)

    def test_two_readers_hammering_the_log_at_once(self):
        log, errors = ObservationLog(size=16), []

        def trail():
            try:
                for i in range(300):
                    log.add(obs("trail", [AX("page line %d of the shared screen" % (i % 40))], t=i * 0.01))
            except Exception as e:                                                  # noqa: BLE001 - the test is the net
                errors.append(e)

        def tracker():
            try:
                for i in range(300):
                    probe = obs("tracker", [OCR("page line %d of the shared screen" % (i % 40))], t=i * 0.01)
                    log.add(probe)
                    found = log.match(probe)
                    if found is not None:
                        self.assertEqual(found.reader, "trail")
                        merge([found, probe])
            except Exception as e:                                                  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=trail), threading.Thread(target=tracker)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertLessEqual(len(log), 16)
        times = [o.t for o in log._items]
        self.assertEqual(times, sorted(times))


def screen(text_objs, region=(0, 0, 1, 1)):
    res = {"image_size": {"w": 1000, "h": 800}, "objects": [
        {"kind": k, "text": t, "conf": c, "box": [250, 200 + 40 * i, 450, 30]} for i, (t, k, c) in enumerate(text_objs)]}
    meta = {"iso": "2026-11-10T10:00:00", "app": "Brave Browser", "bundle_id": "com.brave.Browser",
            "window": "WhatsApp", "url": "https://web.whatsapp.com/", "window_region": list(region), "image": "x.jpg"}
    return res, meta


FIRST = ("Partner$hip Lead$ for the 2026 - 27 cycle", "paragraph", 0.8)
SECOND = ("Hiring plan for the next quarter is ready", "paragraph", 0.9)
APP_FIRST, APP_SECOND = "Partnership Leads for the 2026 - 27 cycle", "Hiring plan for the next quarter is ready"


class ExcerptTests(unittest.TestCase):
    def test_accessibility_disabled_the_text_is_ocr_with_honest_provenance(self):
        res, meta = screen([("We chose SQLite because the data stays local.", "paragraph", 0.99),
                            ("Unrelated sidebar navigation content", "sidebar_item", 1)])
        c = extract_content(res, meta)
        self.assertEqual(c["text"], "We chose SQLite because the data stays local.")
        self.assertEqual(c["provenance"], {"ax_only": 0, "ocr_only": 1, "agreed": 0, "differed": 0})
        self.assertNotIn("variants", c)

    def test_accessibility_enabled_the_app_text_is_stored_and_the_ocr_reading_is_kept(self):
        res, meta = screen([FIRST, SECOND])
        c = extract_content(res, meta, obs("trail", [AX(APP_FIRST), AX(APP_SECOND)]))
        self.assertEqual(c["text"], APP_FIRST + "\n" + APP_SECOND)
        self.assertEqual(c["variants"], [{"text": FIRST[0], "source": "ocr", "conf": 0.8}])
        self.assertEqual(c["provenance"], {"ax_only": 0, "ocr_only": 0, "agreed": 2, "differed": 0})

    def test_both_scenarios_through_the_whole_trail_to_excerpt_path(self):
        """The same screen, once with a working accessibility reader and once with none (this Mac today)."""
        res, meta = screen([FIRST, SECOND])
        meta.update(app="WhatsApp", bundle_id="net.whatsapp.WhatsApp", url=None, pid=1)     # the app FakeFront reports
        for enabled in (True, False):
            with self.subTest(accessibility=enabled), tempfile.TemporaryDirectory() as d:
                log = ObservationLog()
                reader = FakeReader()
                reader.full.texts = [APP_FIRST, APP_SECOND]
                if not enabled:
                    reader.light = None                               # what "accessibility: false" looks like to the engine
                e = Engine(TrailStore(d, "t"), FakeFront(), reader, observations=log)
                e.step({"place", "text"}, 0)
                c = screen_content(log, res, meta, e.wall())
                if enabled:
                    self.assertEqual(c["text"], APP_FIRST + "\n" + APP_SECOND)
                else:
                    self.assertEqual(c["text"], FIRST[0] + "\n" + SECOND[0])
                    self.assertEqual(c["provenance"]["ocr_only"], 2)

    def test_app_only_lines_pass_the_same_length_and_word_rules(self):
        res, meta = screen([SECOND])
        c = extract_content(res, meta, obs("trail", [AX("Search"), AX("A longer line only the app exposed"), AX(APP_SECOND)]))
        self.assertIn("A longer line only the app exposed", c["text"])
        self.assertNotIn("Search\n", c["text"] + "\n")

    def test_low_confidence_or_outside_window_ocr_is_still_dropped(self):
        res, meta = screen([("Maybe this is some garbled text", "paragraph", 0.3)])
        self.assertIsNone(extract_content(res, meta))
        res, meta = screen([("A line that sits in the corner", "paragraph", 0.9)], region=(0.9, 0.9, 0.1, 0.1))
        self.assertIsNone(extract_content(res, meta))

    def test_screen_content_keeps_app_text_when_the_ocr_found_nothing_usable(self):
        res, meta = screen([("ok", "text", 0.9)])
        log = ObservationLog()
        log.add(obs("trail", [AX(APP_FIRST), AX(APP_SECOND)], t=1000))
        c = screen_content(log, res, meta, 1002)
        self.assertEqual(c["text"], APP_FIRST + "\n" + APP_SECOND)
        self.assertEqual(c["provenance"], {"ax_only": 2, "ocr_only": 0, "agreed": 0, "differed": 0})

    def test_no_window_region_still_means_no_content(self):
        res, meta = screen([SECOND])
        del meta["window_region"]
        self.assertIsNone(extract_content(res, meta, obs("trail", [AX(APP_SECOND)])))

    def test_excerpt_stores_provenance_and_old_excerpts_still_work(self):
        res, meta = screen([FIRST, SECOND])
        item = {"content": {"version": 1, "excerpts": [{"id": "old", "text": "old ocr text only", "source": {"app": "X"},
                                                        "first_seen": "2026-11-09T10:00:00", "last_seen": "2026-11-09T10:00:00",
                                                        "decision_quotes": []}]}}
        c = extract_content(res, meta, obs("trail", [AX(APP_FIRST), AX(APP_SECOND)]))
        self.assertTrue(remember_content(item, c))
        old, new = item["content"]["excerpts"]
        self.assertNotIn("provenance", old)
        self.assertEqual(new["provenance"]["agreed"], 2)
        self.assertEqual(new["variants"][0]["source"], "ocr")
        json.dumps(item)
        import page_model
        self.assertEqual(page_model.screen_of(item["content"], datetime(2026, 11, 10, 11))["count"], 2)

    def test_screen_content_finds_the_trail_read_through_the_shared_log_but_not_another_pages(self):
        res, meta = screen([FIRST, SECOND])
        log = ObservationLog()
        log.add(obs("trail", [AX(APP_FIRST), AX(APP_SECOND)], t=1000))
        self.assertIn(APP_FIRST, screen_content(log, res, dict(meta, window="WhatsApp - Brave"), 1003)["text"])
        other = ObservationLog()
        other.add(obs("trail", [AX(APP_FIRST), AX(APP_SECOND)], t=1000, url="https://x.org/"))
        self.assertIn("Partner$hip", screen_content(other, res, meta, 1003)["text"])

    def test_a_stale_app_read_from_another_screen_state_does_not_replace_the_ocr(self):
        res, meta = screen([FIRST, SECOND])
        log = ObservationLog()
        log.add(obs("trail", [AX("Completely different page content one"), AX("Completely different page content two")], t=1000))
        c = screen_content(log, res, meta, 1003)
        self.assertEqual(c["text"], FIRST[0] + "\n" + SECOND[0])

    def test_eval_log_records_before_after_and_the_gate_and_is_owner_only(self):
        res, meta = screen([FIRST, SECOND])
        log = ObservationLog()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "evidence_eval.jsonl")
            log.add(obs("trail", [AX(APP_FIRST), AX(APP_SECOND)], t=1000))
            screen_content(log, res, meta, 1002, fresh=True, eval_path=path)
            rec = json.loads(open(path).read())
            self.assertEqual((rec["before"], rec["after"]), (FIRST[0] + "\n" + SECOND[0], APP_FIRST + "\n" + APP_SECOND))
            self.assertEqual((rec["matched"], rec["gate"]), (True, "merged"))
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(len(log.near(obs("trail", [], t=1002), source="ocr")), 1)


# ---------------------------------------------------------------- OCR-only output versus the previous implementation

def legacy_extract_content(res, meta):
    """Keep readable lines fully inside the foreground window, excluding UI chrome.

    Older frames have no window region. Do not attribute their entire display
    to the foreground app; they still support the existing activity rules.
    """
    region = meta.get("window_region")
    if not region:
        return None
    width, height = res["image_size"]["w"], res["image_size"]["h"]
    x, y, w, h = [region[0] * width, region[1] * height,
                   region[2] * width, region[3] * height]
    lines, seen = [], set()
    remaining = MAX_TEXT
    for obj in res["objects"]:
        if obj["kind"] not in CONTENT_KINDS or obj.get("conf", 0) < 0.5:
            continue
        bx, by, bw, bh = obj["box"]
        if bx < x or by < y or bx + bw > x + w or by + bh > y + h:
            continue
        text = re.sub(r"\s+", " ", obj.get("text") or "").strip()
        if len(text) < 12 or len(re.findall(r"\w+", text)) < 3 or text in seen:
            continue
        seen.add(text)
        text = text[:remaining]
        if not text:
            break
        lines.append(text)
        remaining -= len(text) + 1
        if remaining <= 0:
            break
    if not lines:
        return None
    return {"text": "\n".join(lines),
            "decision_quotes": [line for line in lines if DECISION.search(line) and "?" not in line][:5],
            "observed_at": meta["iso"],
            "source": {"app": meta.get("app"), "window": meta.get("window"), "url": meta.get("url")}}




class OcrOnlyCompatibilityTests(unittest.TestCase):
    """With no accessibility read, the stored text is exactly what the previous implementation produced.
    (Lines are not collapsed beyond the exact-text de-duplication that was already there.)"""

    def random_screen(self, rnd):
        words = ["Invoice", "total", "Partner$hip", "Lead$", "meeting", "SQLite", "decided", "to", "use", "the", "data", "ag0",
                 "Table", "3", "5", "booked", "for", "six", "people", "!", "|", "OK", "Search"]
        objs = []
        for i in range(rnd.randint(0, 25)):
            text = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 9)))
            if objs and rnd.random() < 0.25:
                text = rnd.choice(objs)[0]                                         # exact repeats
            if objs and rnd.random() < 0.15:
                text = rnd.choice(objs)[0].upper()                                 # case-only repeats stay separate lines
            kind = rnd.choice(["paragraph", "paragraph", "text", "heading", "link", "sidebar_item", "button", "tab"])
            objs.append((text, kind, round(rnd.random(), 2)))
        res = {"image_size": {"w": 1000, "h": 800}, "objects": [
            {"kind": k, "text": t, "conf": c, "box": [rnd.randint(0, 900), rnd.randint(0, 760), rnd.randint(20, 300), 20]}
            for t, k, c in objs]}
        meta = {"iso": "2026-11-10T10:00:00", "app": "A", "window": "W", "url": None,
                "window_region": [0.1, 0.1, 0.8, 0.8]}
        return res, meta

    def test_identical_text_to_the_previous_implementation_on_random_screens(self):
        rnd = random.Random(7)
        for _ in range(400):
            res, meta = self.random_screen(rnd)
            old, new = legacy_extract_content(res, meta), extract_content(res, meta)
            self.assertEqual(old is None, new is None)
            if old:
                self.assertEqual({k: new[k] for k in old}, old)

    def test_case_and_punctuation_variants_are_kept_as_before(self):
        res, meta = screen([("Total amount due is 1,200 rupees", "paragraph", 0.9), ("TOTAL AMOUNT DUE IS 1,200 RUPEES.", "paragraph", 0.9)])
        self.assertEqual(extract_content(res, meta)["text"].count("\n"), 1)
        self.assertEqual(extract_content(res, meta)["text"], legacy_extract_content(res, meta)["text"])


class EvalScriptTests(unittest.TestCase):
    """The evaluation script runs on a small memory.json and reports what the matching does."""

    def load(self):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "eval_evidence.py")
        spec = importlib.util.spec_from_file_location("eval_evidence", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_simulation_repairs_noise_without_wrong_replacements_and_refuses_another_page(self):
        ev = self.load()
        words = ["quarterly", "pipeline", "hiring", "invoice", "contract", "printing", "deadline", "approval", "budget", "review"]
        rnd = random.Random(3)
        items = [{"id": "p%d" % p, "content": {"excerpts": [{"text": "\n".join(
            "%s %s %s number %d for project %d" % (rnd.choice(words), rnd.choice(words), rnd.choice(words), i * 7 + p, p)
            for i in range(10))}]}} for p in range(6)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "memory.json")
            json.dump({"items": items}, open(path, "w"))
            out = ev.simulate(path, True)
            self.assertGreater(out["same_page"]["corrupted"], 0)
            self.assertEqual(out["same_page"]["wrongly_replaced"], 0)
            self.assertGreater(out["same_page"]["repair_rate_of_corrupted"], 0.8)
            self.assertEqual(out["app_read_from_another_page_with_the_same_title"]["leaked_pairs"], 0)
            self.assertEqual(ev.from_memory(path)["frames"], 6)

    def test_paired_log_with_no_trail_reads_says_so(self):
        ev = self.load()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "evidence_eval.jsonl")
            rec = {"window": "W", "before": "Partner$hip Lead$ for the cycle", "after": "Partner$hip Lead$ for the cycle",
                   "matched": False, "ax_near": 0, "gate": "no_trail_read", "provenance": {"ocr_only": 1}}
            open(path, "w").write(json.dumps(rec) + "\n")
            out = ev.from_log(path)
            self.assertEqual((out["frames_with_trail_read"], out["grounded_frames"]), (0, 0))
            self.assertEqual(out["before"], out["after"])


class TrailWritesObservationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = TrailStore(self.tmp.name, "t")
        self.log = ObservationLog()

    def test_the_trails_text_read_becomes_a_shared_observation_with_secrets_removed(self):
        reader = FakeReader()
        reader.full.texts = ["hello there my friend", "key sk-abcdefghijklmnopqrstu"]
        e = Engine(self.store, FakeFront(), reader, observations=self.log)
        e.step({"place", "text"}, 0)
        seen = self.log.near(obs("tracker", [], t=e.wall()), max_age=60, source="ax")
        self.assertEqual(len(seen), 1)
        self.assertEqual([(l.text, l.conf) for l in seen[0].lines], [("hello there my friend", None)])
        self.assertEqual(self.store.read(kinds={"text"})[0]["observation"], seen[0].id)

    def test_an_ocr_fallback_read_is_recorded_as_ocr_with_its_confidence(self):
        def vision(front, s):
            return snap(texts=["Invoice 4412", "Total 120"], confs=[0.9, 0.4])
        e = Engine(self.store, FakeFront(app="Canvas", bundle_id="com.x.canvas"), FakeReader(), vision=vision,
                   observations=self.log)
        e.reader.full = snap(title="Canvas")
        e.reader.light = snap(title="Canvas")
        e.step({"place", "text"}, 0)
        got = self.log.near(obs("tracker", [], t=e.wall()), max_age=60, source="ocr")
        self.assertEqual([(l.text, l.source, l.conf) for l in got[0].lines],
                         [("Invoice 4412", "ocr", 0.9), ("Total 120", "ocr", 0.4)])

    def test_an_excluded_app_adds_nothing(self):
        e = Engine(self.store, FakeFront(app="1Password", bundle_id="com.1password.1password"), FakeReader(),
                   observations=self.log)
        e.step({"place", "text"}, 0)
        self.assertEqual(len(self.log), 0)

    def test_switching_the_merge_off_leaves_the_log_and_the_events_as_before(self):
        reader = FakeReader()
        reader.full.texts = ["hello there my friend"]
        config.OBSERVATION_MERGE = False
        try:
            Engine(self.store, FakeFront(), reader, observations=self.log).step({"place", "text"}, 0)
        finally:
            config.OBSERVATION_MERGE = True
        self.assertEqual(len(self.log), 0)
        self.assertNotIn("observation", self.store.read(kinds={"text"})[0])

    def test_no_accessibility_adds_nothing_and_logs_the_gap(self):
        reader = FakeReader()
        reader.light = None
        Engine(self.store, FakeFront(), reader, observations=self.log).step({"place", "text"}, 0)
        self.assertEqual(len(self.log), 0)
        self.assertEqual([e["reason"] for e in self.store.read(kinds={"gap"})], ["no_accessibility"])


if __name__ == "__main__":
    unittest.main()
