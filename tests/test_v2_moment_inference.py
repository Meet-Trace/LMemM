import importlib.util
import json
import os
import sys
import unittest
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENT = os.path.join(HERE, "..", "experiments", "moment_goals")
sys.path.insert(0, EXPERIMENT)

import context as C
import inference as I
from trace_v2.observations import Context, ReaderResult
from v2_observation_helpers import T0, TempStore, draft

S = 1_000_000_000
SHEET = ["Outreach Tracker Q4", "Contact Company Status Owner", "Acme Robotics follow up sent", "Northwind Traders reply received",
         "Globex Corporation meeting booked", "Initech Systems waiting for response", "Umbrella Health intro call scheduled"]
PAPER = ["Gene regulation in cellular stress responses", "Abstract We examine transcription factor binding across conditions",
         "Methods Samples were cultured and sequenced", "Results Expression increased under heat shock"]
MAIL = ["Inbox Outreach follow up", "Subject Acme Robotics follow up", "Thanks for the update we will review the proposal"]


def scroll(t, dy=-40):
    return {"type": "scroll", "t_ns": t, "payload": {"dx": 0, "dy": dy, "phase": "changed"}}


def click(t):
    return {"type": "click", "t_ns": t, "payload": {"x": 1, "y": 2, "button": "left", "phase": "down", "click_count": 1}}


def key(t, kind="character"):
    return {"type": "keypress", "t_ns": t, "payload": {"phase": "down", "key_class": kind, "modifiers": [], "is_repeat": False}}


def move(t, x, y):
    return {"type": "cursor_move", "t_ns": t, "payload": {"x": x, "y": y}}


def obs(tag, t, app="Sheets", title="Outreach Tracker", lines=SHEET, events=(), url=None):
    return {"tag": tag, "ids": [f"id-{tag}"], "t_ns": t, "app": app, "title": title, "url": url, "lines": list(lines), "events": list(events)}


def sheet_sequence(events_per_step):
    return [obs(f"O{i + 1}", T0 + i * 10 * S, events=ev(T0 + i * 10 * S)) for i, ev in enumerate(events_per_step)]


class SerializerTests(unittest.TestCase):
    def test_spreadsheet_navigation_is_scrolling_not_editing(self):
        seq = sheet_sequence([lambda t: [scroll(t + k) for k in range(6)] + [click(t + 8)]] * 3)
        built = C.serialize(seq)
        self.assertIn("scroll 6 events", built["text"])
        self.assertIn("pattern: scrolling", built["text"])
        self.assertNotIn("pattern: typing", built["text"])
        self.assertIn("no meaningful change", built["text"])

    def test_spreadsheet_editing_is_typing(self):
        edit = lambda t: [click(t)] + [key(t + k) for k in range(1, 9)] + [key(t + 9, "delete"), key(t + 10, "delete"), key(t + 11, "return")]
        built = C.serialize(sheet_sequence([edit, edit]))
        self.assertIn("pattern: typing", built["text"])
        self.assertIn("2 deletes", built["text"])
        self.assertIn("1 returns", built["text"])

    def test_reading_without_interaction(self):
        built = C.serialize([obs("O1", T0, title="Paper", lines=PAPER), obs("O2", T0 + 20 * S, title="Paper", lines=PAPER)])
        self.assertIn("none observed", built["text"])
        self.assertIn("pattern: no_interaction", built["text"])
        self.assertIn("application and document stayed the same throughout", built["text"])

    def test_related_application_switch_is_reported_with_shared_content(self):
        seq = [obs("O1", T0, lines=SHEET), obs("O2", T0 + 10 * S, app="Mail", title="Outreach follow up", lines=MAIL + SHEET[2:4]),
               obs("O3", T0 + 20 * S, lines=SHEET)]
        built = C.serialize(seq)
        self.assertIn("application changed from Sheets to Mail", built["text"])
        self.assertIn("application/document sequence", built["text"])
        self.assertTrue(0 < built["deltas"][0]["overlap"] < 1)

    def test_unrelated_application_switch_replaces_content(self):
        seq = [obs("O1", T0, lines=SHEET), obs("O2", T0 + 10 * S, app="Safari", title="Paper", lines=PAPER)]
        built = C.serialize(seq)
        self.assertIn("visible content mostly replaced", built["text"])
        self.assertEqual(built["deltas"][0]["overlap"], 0.0)

    def test_scroll_then_navigate_elsewhere(self):
        seq = [obs("O1", T0, events=[scroll(T0 + k) for k in range(5)]),
               obs("O2", T0 + 10 * S, events=[scroll(T0 + 10 * S + k) for k in range(5)]),
               obs("O3", T0 + 20 * S, title="Pipeline", lines=PAPER, events=[click(T0 + 20 * S)])]
        built = C.serialize(seq)
        self.assertIn("window title changed", built["text"])
        self.assertIn("interaction changed from scrolling to pointer_clicks", built["text"])

    def test_noise_and_duplicates_are_removed(self):
        noisy = ["~~~ ||| ///", "a", "Outreach Tracker Q4", "outreach tracker q4", "ok ok ok ok ok ok", "  Outreach   Tracker  Q4 ", "= = - -",
                 "Acme Robotics follow up sent", "Acme Robotics follow up sent"]
        cleaned = C.clean_lines(noisy)
        self.assertEqual(cleaned, ["Outreach Tracker Q4", "Acme Robotics follow up sent"])
        built = C.serialize([obs("O1", T0, lines=noisy)])
        self.assertEqual(built["text"].lower().count("outreach tracker q4"), 1)
        self.assertNotIn("|||", built["text"])

    def test_insufficient_evidence_still_serializes(self):
        built = C.serialize([obs("O1", T0, lines=["x"])])
        self.assertIn("none observed", built["text"])
        self.assertEqual(built["tags"], {"O1": ["id-O1"]})
        self.assertEqual(C.serialize([])["text"], "")

    def test_persistent_content_is_listed_once(self):
        seq = [obs(f"O{i + 1}", T0 + i * 10 * S, lines=SHEET + [f"Row detail number {i} unique"]) for i in range(5)]
        built = C.serialize(seq)
        self.assertIn("ON SCREEN THROUGHOUT:", built["text"])
        self.assertEqual(built["text"].count("Globex Corporation meeting booked"), 1)

    def test_budget_is_respected_for_large_screens(self):
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
        big = [f"Report {i} {words[i % 8]} {words[(i * 3) % 8]} summary line" for i in range(600)]
        built = C.serialize([obs(f"O{i + 1}", T0 + i * 10 * S, lines=big[i * 50:(i + 1) * 50 + 200]) for i in range(6)], budget=4500)
        self.assertLess(len(built["text"]), 6500)

    def test_before_and_after_context_is_marked_as_outside_the_window(self):
        before = [dict(obs("B", T0 - 60 * S, app="Slack", title="General", lines=[]))]
        after = [dict(obs("A", T0 + 90 * S, app="Finder", title="Downloads", lines=[]))]
        built = C.serialize([obs("O1", T0), obs("O2", T0 + 10 * S)], before, after)
        self.assertIn("BEFORE THE WINDOW (context only", built["text"])
        self.assertIn("AFTER THE WINDOW (context only", built["text"])
        self.assertIn("Slack | General", built["text"])

    def test_interaction_summary_counts_observed_events_only(self):
        s = C.interaction_summary([move(T0, 0, 0), move(T0 + 1, 30, 40), click(T0 + 2), key(T0 + 3), scroll(T0 + 4, 10)])
        self.assertEqual(s["cursor_travel"], 50)
        self.assertEqual(s["click_total"], 1)
        self.assertEqual(s["typed"], 1)
        self.assertEqual(s["scroll_events"], 1)


class PromptTests(unittest.TestCase):
    def test_prompt_contains_task_rules_and_context(self):
        built = C.serialize([obs("O1", T0), obs("O2", T0 + 10 * S)])
        prompt = I.build_prompt(built["text"])
        for phrase in ("Do not invent user intentions", "Do not make decisions about temporal grouping",
                       "insufficient_evidence", "Scrolling indicates navigation through visible content", "Clicking alone does not establish editing", "[O1]"):
            self.assertIn(phrase, prompt)
        self.assertTrue(prompt.endswith(built["text"]))


class ParserTests(unittest.TestCase):
    TAGS = {"O1": ["a", "b"], "O2": ["c"], "O3": ["d"]}

    def good(self, **over):
        data = {"moment_description": "Reviewing outreach status in the tracker",
                "evidence": [{"type": "application_context", "description": "Outreach Tracker stayed active", "observations": ["O1", "O2"]},
                             {"type": "interaction", "description": "Repeated scrolling", "observations": ["O2"]}],
                "confidence": 0.78, "alternative_interpretation": "Looking for a specific contact", "insufficient_evidence": False}
        data.update(over)
        return json.dumps(data)

    def test_valid_output_maps_observations_to_real_ids(self):
        out = I.parse_output(self.good(), self.TAGS)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["observation_ids"], ["a", "b", "c"])
        self.assertEqual(out["confidence"], 0.78)
        self.assertEqual(out["alternative"], "Looking for a specific contact")

    def test_fenced_and_wrapped_json_is_accepted(self):
        self.assertEqual(I.parse_output("```json\n" + self.good() + "\n```", self.TAGS)["status"], "ok")
        self.assertEqual(I.parse_output("Here you go: " + self.good() + " done", self.TAGS)["status"], "ok")

    def test_malformed_output_is_invalid_not_a_crash(self):
        for bad in ("", "not json", "[1, 2]", "{\"moment_description\": ", "null"):
            out = I.parse_output(bad, self.TAGS)
            self.assertEqual(out["status"], "invalid")
            self.assertIsNone(out["description"])

    def test_fabricated_evidence_is_dropped(self):
        out = I.parse_output(self.good(evidence=[{"type": "content", "description": "invented", "observations": ["O9"]},
                                                 {"type": "interaction", "description": "Scrolling", "observations": ["O3"]},
                                                 {"type": "nonsense", "description": "x", "observations": ["O1"]},
                                                 {"type": "content", "description": "", "observations": ["O1"]}, "junk"]), self.TAGS)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["evidence"]), 1)
        self.assertEqual(out["dropped_evidence"], 4)
        self.assertEqual(out["observation_ids"], ["d"])

    def test_loose_observation_references_are_normalised(self):
        out = I.parse_output(self.good(evidence=[{"type": "content", "description": "Tracker open", "observations": "[O1], O3"},
                                                 {"type": "interaction", "description": "Scrolling", "observations": ["[O2]"]}]), self.TAGS)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["observation_ids"], ["a", "b", "d", "c"])

    def test_unsupported_description_is_kept_for_inspection(self):
        out = I.parse_output(self.good(evidence=[{"type": "content", "description": "invented", "observations": ["O9"]}]), self.TAGS)
        self.assertEqual(out["unsupported_description"], "Reviewing outreach status in the tracker")

    def test_description_without_traceable_evidence_becomes_insufficient(self):
        out = I.parse_output(self.good(evidence=[{"type": "content", "description": "invented", "observations": ["O9"]}]), self.TAGS)
        self.assertEqual(out["status"], "insufficient")
        self.assertIsNone(out["description"])

    def test_insufficient_evidence_flag_and_null_description(self):
        out = I.parse_output(self.good(insufficient_evidence=True), self.TAGS)
        self.assertEqual(out["status"], "insufficient")
        self.assertIsNone(out["description"])
        out = I.parse_output(self.good(moment_description=None), self.TAGS)
        self.assertEqual(out["status"], "insufficient")
        self.assertEqual(I.parse_output(self.good(moment_description="unknown"), self.TAGS)["status"], "insufficient")

    def test_confidence_is_clamped_and_validated(self):
        self.assertEqual(I.parse_output(self.good(confidence=7), self.TAGS)["confidence"], 1.0)
        self.assertEqual(I.parse_output(self.good(confidence=-2), self.TAGS)["confidence"], 0.0)
        out = I.parse_output(self.good(confidence="high"), self.TAGS)
        self.assertEqual(out["confidence"], 0.0)
        self.assertTrue(any("confidence" in e for e in out["errors"]))


class StoreLoadTests(unittest.TestCase):
    def load_run(self):
        spec = importlib.util.spec_from_file_location("moment_goals_run", os.path.join(EXPERIMENT, "run.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_steps_carry_events_titles_and_neighbours_from_the_store(self):
        run = self.load_run()
        from trace_v2.structure.processor import read_connection
        from trace_v2.observations import InteractionEvent
        with TempStore() as ts:
            ids = []
            for i in range(6):
                t = T0 + i * 10 * S
                ocr = ReaderResult("vision-ocr/1", "succeeded", {"lines": [{"text": line, "confidence": 0.9, "box": [0, 100, 10, 10]}
                                                                           for line in SHEET], "image_size": [1000, 1000]}, " ".join(SHEET))
                ctx = Context({"bundle_id": "com.example.sheets", "name": "Sheets", "pid": 3}, {"title": "Outreach Tracker", "id": 1,
                                                                                               "url": "https://sheets.example/outreach"},
                              {"id": "main", "bounds": [0, 0, 1440, 900], "scale": 2.0})
                d = replace(draft(key=f"k{i}", t=t, ocr=ocr, events=[InteractionEvent("click", t - S, {"x": 1.0, "y": 2.0, "button": "left", "phase": "down"})]),
                            context=ctx)
                ids.append(ts.store.ingest(d).observation_id)
            conn = read_connection(ts.store)
            try:
                members = ids[1:5]
                moment = {"members": members, "start_ns": T0 + 10 * S, "end_ns": T0 + 40 * S}
                text, tags = run.packet(conn, moment, 4500, 3)
                self.assertEqual(sorted(tags), ["O1", "O2", "O3"])
                self.assertEqual(sorted(i for v in tags.values() for i in v), sorted(members))
                self.assertIn("Outreach Tracker", text)
                self.assertIn("clicks left x", text)
                self.assertIn("https://sheets.example/outreach", text)
                self.assertIn("BEFORE THE WINDOW", text)
                self.assertIn("AFTER THE WINDOW", text)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
