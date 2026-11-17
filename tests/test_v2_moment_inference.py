import json
import unittest

from trace_v2.moments.inference import (OUTPUT_SCHEMA, SYSTEM_PROMPT, build_context, build_prompt, interaction,
                                        parse_interpretation, serialize)
from trace_v2.moments.inference.load import load_window
from trace_v2.observations import InteractionEvent
from v2_inference_fixtures import (SEC, SHEET_LINES, T0, click_events, cursor_events, record, scroll_events,
                                   typing_events)
from v2_observation_helpers import T0 as STORE_T0
from v2_observation_helpers import TempStore, draft


def sheet(index, events=(), focus=None, lines=SHEET_LINES, **kw):
    return record(index, "Sheets", "Outreach Tracker", lines, events, focus, **kw)


def text_of(records, **kw):
    return serialize(build_context(records, **kw))


class InteractionTests(unittest.TestCase):
    def test_scroll_direction_and_levels(self):
        down = interaction.summarize(scroll_events(6, -40))
        up = interaction.summarize(scroll_events(6, 40))
        mixed = interaction.summarize(scroll_events(3, -40) + scroll_events(3, 40))
        self.assertEqual(interaction.scroll_direction(down), "down")
        self.assertEqual(interaction.scroll_direction(up), "up")
        self.assertEqual(interaction.scroll_direction(mixed), "back and forth")
        self.assertEqual(interaction.level(down), "scrolling")

    def test_typing_is_not_confused_with_scrolling(self):
        typed = interaction.summarize(typing_events(8, 2))
        self.assertEqual(interaction.level(typed), "typing")
        self.assertIsNone(interaction.scroll_direction(typed))

    def test_repeated_and_key_up_events_do_not_count(self):
        events = [("keypress", {"phase": "up", "key_class": "character"}),
                  ("keypress", {"phase": "down", "key_class": "character", "is_repeat": True})]
        self.assertEqual(interaction.summarize(events)["keys_total"], 0)

    def test_no_events_is_none(self):
        self.assertEqual(interaction.level(interaction.summarize([])), "none")
        self.assertEqual(interaction.describe(interaction.summarize([])), [])

    def test_cursor_path_is_measured(self):
        self.assertEqual(interaction.summarize(cursor_events(300))["cursor_path_px"], 300)


class ScenarioTests(unittest.TestCase):
    def test_navigating_a_spreadsheet(self):
        records = [sheet(i, scroll_events(5) + click_events(1)) for i in range(4)]
        out = text_of(records)
        self.assertIn("scrolled down", out)
        self.assertNotIn("character keys typed", out)
        self.assertIn("same window", out)
        self.assertIn("interaction scrolling (same)", out)

    def test_editing_spreadsheet_cells(self):
        records = [sheet(i, typing_events(6, 1) + click_events(1), focus="AXTextField") for i in range(4)]
        out = text_of(records)
        self.assertIn("character keys typed", out)
        self.assertIn("delete presses", out)
        self.assertIn("focused element: AXTextField", out)
        self.assertNotIn("scrolled", out)

    def test_reading_without_interaction(self):
        records = [sheet(i) for i in range(4)]
        context = build_context(records)
        out = serialize(context)
        self.assertIn("window total: no interaction events recorded", out)
        self.assertTrue(all(o["delta"]["content"] == "unchanged" for o in context["observations"][1:]))
        self.assertEqual(context["evidence_level"], "ok")

    def test_related_applications_share_terms(self):
        records = [sheet(0, scroll_events(3)), sheet(1, scroll_events(3)),
                   record(2, "Mail", "Outreach Tracker follow up", ("Outreach Tracker status", "Harbour Cafe reply from Tara"),
                          typing_events(10)),
                   record(3, "Mail", "Outreach Tracker follow up", ("Outreach Tracker status", "Harbour Cafe reply from Tara"),
                          typing_events(10))]
        context = build_context(records)
        move = context["transitions"][0]
        self.assertTrue(move["app_changed"])
        self.assertIn("outreach", move["shared_terms"])
        self.assertIn("tracker", move["shared_terms"])
        self.assertGreater(move["overlap"], 0.1)

    def test_unrelated_applications_share_nothing(self):
        records = [sheet(0, scroll_events(3)), sheet(1, scroll_events(3)),
                   record(2, "Music", "Evening playlist", ("Slow jazz for late evenings", "Now playing Blue in Green"), ()),
                   record(3, "Music", "Evening playlist", ("Slow jazz for late evenings", "Now playing Blue in Green"), ())]
        move = build_context(records)["transitions"][0]
        self.assertTrue(move["app_changed"])
        self.assertEqual(move["shared_terms"], [])
        self.assertLess(move["overlap"], 0.05)

    def test_scrolling_then_navigating_elsewhere(self):
        records = [record(0, "Docs", "Pricing memo", ("Take rate is fifteen percent on bookings",), scroll_events(6)),
                   record(1, "Docs", "Pricing memo", ("Refund policy changes for annual plans",), scroll_events(6)),
                   record(2, "Docs", "Hiring plan", ("Engineering roles for next quarter",), click_events(2))]
        context = build_context(records)
        last = context["observations"][-1]["delta"]
        self.assertTrue(last["window_changed"])
        self.assertFalse(last["app_changed"])
        self.assertEqual(last["interaction"], "scrolling -> clicking")
        self.assertEqual(len(context["segments"]), 2)

    def test_noisy_and_duplicated_ocr_is_condensed(self):
        noise = ["||| ~~~ ###", "a b c d e f", "........", "Tara Tara Tara", "Q1 revenue forecast table", "Q1 revenue forecast table",
                 "q1 revenue forecast table.", "xxxxxxxxxx", "12:04", "Menu Bar Chrome File Edit View"]
        records = [record(i, "Sheets", "Forecast", noise + [f"Unique row {i} margin details"], scroll_events(2)) for i in range(5)]
        context = build_context(records)
        self.assertEqual(sum("revenue forecast" in p.lower() for p in context["persistent"]), 1)
        shown = [line["text"] for o in context["observations"] for line in o["lines"]]
        self.assertFalse(any(set(t) <= set("|~#. x") for t in shown))
        self.assertEqual(len(shown), len(set(s.lower() for s in shown)))
        self.assertLess(len(serialize(context)), 2500)

    def test_long_windows_are_sampled_but_totals_cover_everything(self):
        records = [sheet(i, scroll_events(2)) for i in range(60)]
        context = build_context(records, max_observations=10)
        self.assertEqual(context["observation_count"], 60)
        self.assertEqual(len(context["observations"]), 10)
        self.assertEqual(context["observations"][0]["oid"], "O1")
        self.assertEqual(context["observations"][-1]["oid"], "O60")
        self.assertEqual(context["totals"]["scroll_gestures"], 60)

    def test_insufficient_evidence_is_flagged(self):
        empty = build_context([record(0, "", "", (), ())])
        self.assertEqual(empty["evidence_level"], "none")
        titled = build_context([record(0, "Sheets", "Outreach Tracker", (), ())])
        self.assertEqual(titled["evidence_level"], "weak")
        self.assertIn("evidence level: none", serialize(empty))

    def test_neighbours_are_outside_the_window(self):
        before = record(-1, "Mail", "Inbox")
        after = record(5, "Slack", "General")
        out = text_of([sheet(0), sheet(1)], before=before, after=after)
        self.assertIn("before the window", out)
        self.assertIn("outside it", out)
        self.assertIn("Slack", out)

    def test_observation_ids_map_back_to_sources(self):
        context = build_context([sheet(2), sheet(0), sheet(1)])
        self.assertEqual([context["ids"][o["oid"]] for o in context["observations"]], ["obs-0", "obs-1", "obs-2"])


class PromptTests(unittest.TestCase):
    def test_prompt_contains_task_rules_and_evidence(self):
        out = text_of([sheet(0, scroll_events(3)), sheet(1)])
        prompt = build_prompt(out)
        self.assertTrue(prompt.startswith(SYSTEM_PROMPT))
        self.assertIn(out, prompt)
        self.assertIn("most plausible continuous Moment", prompt)
        self.assertIn("Switching applications or windows alone is not a new Moment", prompt)
        self.assertIn("insufficient_evidence", prompt)
        self.assertIn("Do not give advice", prompt)

    def test_schema_requires_every_output_key(self):
        self.assertEqual(set(OUTPUT_SCHEMA["required"]), set(OUTPUT_SCHEMA["properties"]))


def reply(**overrides):
    base = {"moment_description": "Reviewing outreach status in the tracker",
            "evidence": [{"type": "application_context", "description": "Outreach Tracker stayed active", "observations": ["O1", "O2"]},
                         {"type": "interaction", "description": "Repeated scrolling", "observations": ["O2"]}],
            "confidence": 0.78, "alternative_interpretation": "Looking for a specific row",
            "unassigned_observations": [], "insufficient_evidence": False}
    base.update(overrides)
    return json.dumps(base)


IDS = {"O1", "O2", "O3"}


class ParserTests(unittest.TestCase):
    def test_valid_reply(self):
        out = parse_interpretation(reply(), IDS)
        self.assertEqual(out.status, "ok")
        self.assertEqual(out.description, "Reviewing outreach status in the tracker")
        self.assertEqual(len(out.evidence), 2)
        self.assertAlmostEqual(out.confidence, 0.78)
        self.assertEqual(out.problems, ())

    def test_fenced_and_prefixed_json_is_accepted(self):
        self.assertEqual(parse_interpretation("```json\n" + reply() + "\n```", IDS).status, "ok")
        self.assertEqual(parse_interpretation("Here you go: " + reply() + " thanks", IDS).status, "ok")

    def test_garbage_and_truncation_fail_cleanly(self):
        for raw in ("", None, "no json here", reply()[:60], "[1, 2]", "{}"):
            out = parse_interpretation(raw, IDS)
            self.assertEqual(out.status, "failed", raw)
            self.assertIsNone(out.description)

    def test_bad_confidence_and_flag_fail(self):
        self.assertEqual(parse_interpretation(reply(confidence="high"), IDS).problems, ("bad_confidence",))
        self.assertEqual(parse_interpretation(reply(confidence=True), IDS).status, "failed")
        self.assertEqual(parse_interpretation(reply(insufficient_evidence="no"), IDS).problems, ("bad_insufficient_flag",))

    def test_confidence_is_clamped(self):
        self.assertEqual(parse_interpretation(reply(confidence=7), IDS).confidence, 1.0)
        self.assertEqual(parse_interpretation(reply(confidence=-1), IDS).confidence, 0.0)

    def test_evidence_must_cite_known_observations(self):
        evidence = [{"type": "interaction", "description": "Made up", "observations": ["O9"]},
                    {"type": "interaction", "description": "Real", "observations": ["O1", "O9"]}]
        out = parse_interpretation(reply(evidence=evidence), IDS)
        self.assertEqual(out.status, "ok")
        self.assertEqual(len(out.evidence), 1)
        self.assertEqual(out.evidence[0]["observations"], ["O1"])
        self.assertIn("dropped_1_ungrounded_evidence", out.problems)

    def test_no_grounded_evidence_downgrades_to_insufficient(self):
        evidence = [{"type": "interaction", "description": "Made up", "observations": ["O9"]}]
        out = parse_interpretation(reply(evidence=evidence), IDS)
        self.assertEqual(out.status, "insufficient")
        self.assertIsNone(out.description)
        self.assertLessEqual(out.confidence, 0.4)
        self.assertIn("no_grounded_evidence", out.problems)

    def test_unknown_evidence_type_is_dropped(self):
        evidence = [{"type": "vibes", "description": "x", "observations": ["O1"]}]
        self.assertEqual(parse_interpretation(reply(evidence=evidence), IDS).status, "insufficient")

    def test_observation_refs_with_trailing_text_are_normalised(self):
        evidence = [{"type": "temporal", "description": "Stayed", "observations": ["O2 14:02:19: no interaction"]}]
        out = parse_interpretation(reply(evidence=evidence), IDS)
        self.assertEqual(out.evidence[0]["observations"], ["O2"])

    def test_insufficient_flag_clears_description_and_caps_confidence(self):
        out = parse_interpretation(reply(insufficient_evidence=True, confidence=0.9, unassigned_observations=["O1", "O7"]), IDS)
        self.assertEqual(out.status, "insufficient")
        self.assertIsNone(out.description)
        self.assertEqual(out.confidence, 0.4)
        self.assertEqual(out.unassigned, ("O1",))

    def test_null_description_is_insufficient(self):
        self.assertEqual(parse_interpretation(reply(moment_description=None), IDS).status, "insufficient")

    def test_evidence_count_and_length_are_bounded(self):
        evidence = [{"type": "temporal", "description": "x" * 500, "observations": ["O1"]} for _ in range(8)]
        out = parse_interpretation(reply(evidence=evidence), IDS)
        self.assertEqual(len(out.evidence), 3)
        self.assertLessEqual(len(out.evidence[0]["description"]), 200)


class LoaderTests(unittest.TestCase):
    def test_window_loads_interaction_and_neighbours(self):
        t = STORE_T0
        with TempStore() as ts:
            ids = []
            for i in range(4):
                events = [InteractionEvent("scroll", t + i * 3 * SEC + 1, {"phase": "changed", "dx": 0, "dy": -30, "unit": "pixel", "x": 1, "y": 1}),
                          InteractionEvent("scroll", t + i * 3 * SEC + 2, {"phase": "changed", "dx": 0, "dy": -30, "unit": "pixel", "x": 1, "y": 1}),
                          InteractionEvent("scroll", t + i * 3 * SEC + 3, {"phase": "changed", "dx": 0, "dy": -30, "unit": "pixel", "x": 1, "y": 1})]
                ids.append(ts.store.ingest(draft(key=f"k{i}", t=t + i * 3 * SEC, events=events)).observation_id)
            records, before, after = load_window(ts.store.db, ids[1:3])
            self.assertEqual([r["id"] for r in records], ids[1:3])
            self.assertEqual(before["id"], ids[0])
            self.assertEqual(after["id"], ids[3])
            self.assertEqual(interaction.scroll_direction(records[0]["interaction"]), "down")
            self.assertIn("hello world", [e["text"] for e in records[0]["ocr"]])
            self.assertEqual(records[0]["focus"], None)
            out = serialize(build_context(records, before, after))
            self.assertIn("scrolled down", out)


if __name__ == "__main__":
    unittest.main()
