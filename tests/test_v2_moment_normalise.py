import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "experiments", "moment_goals"))

import norm_app
import norm_ax
import norm_interaction
import norm_ocr
from normalise import normalise

S = 1_000_000_000
T0 = 1_800_000_000_000_000_000
W, H = 2880, 1800
HEADERS = ["Company", "Contact", "Status", "Owner"]
ROWS_A = [["Acme Robotics", "Dana Lee", "Replied", "Priya"], ["Globex", "Sam Ortiz", "Waiting", "Priya"], ["Initech", "Joe Kim", "Booked", "Ravi"]]
ROWS_B = [["Umbrella", "Ana Ruiz", "Intro call", "Ravi"], ["Hooli", "Gavin B", "Waiting", "Priya"], ["Pied Piper", "Rich H", "Replied", "Ravi"]]
CHROME = ["Bookmarks", "Memory usage 340 MB", "Extensions", "Search tabs"]


def line(text, x, y, w=220, h=30):
    return {"text": text, "box": [x, y, w, h]}


def sheet_lines(rows, chrome=True):
    out = [line(t, 100 + i * 260, 110) for i, t in enumerate(CHROME)] if chrome else []
    out.append(line("Outreach Tracker", 120, 260, 420, 60))
    for i, h in enumerate(HEADERS):
        out.append(line(h, 120 + i * 300, 400))
    for r, row in enumerate(rows):
        for i, v in enumerate(row):
            out.append(line(v, 124 + i * 300, 450 + r * 40))
    return out


def scroll(t, n, dy=-60):
    return [{"type": "scroll", "t_ns": t + k * 10 ** 8, "payload": {"dx": 0, "dy": dy, "phase": "changed"}} for k in range(n)]


def click(t, x, y):
    return {"type": "click", "t_ns": t, "payload": {"x": x, "y": y, "button": "left", "phase": "down", "click_count": 1}}


def key(t, kind="character"):
    return {"type": "keypress", "t_ns": t, "payload": {"phase": "down", "key_class": kind, "modifiers": [], "is_repeat": False}}


def rec(i, lines, events=(), app="Google Chrome", bundle="com.google.Chrome", title="Outreach Tracker - Google Sheets",
        url="https://docs.google.com/spreadsheets/d/abc/edit", ax=None, t=None):
    t = T0 + i * 10 * S if t is None else t
    return {"tag": f"O{i + 1}", "ids": [f"id{i}a", f"id{i}b"], "t_ns": t, "app": app, "bundle": bundle, "title": title, "url": url,
            "events": list(events), "ocr": {"lines": lines, "image_size": [W, H]}, "ax": ax, "scale": 2.0}


class BrowserChromeTests(unittest.TestCase):
    def test_bookmarks_memory_and_extensions_are_removed_but_content_stays(self):
        text = normalise([rec(0, sheet_lines(ROWS_A)), rec(1, sheet_lines(ROWS_A))])["text"]
        for noise in CHROME:
            self.assertNotIn(noise, text)
        self.assertIn("Acme Robotics", text)

    def test_chrome_zone_only_applies_to_browsers(self):
        items, dropped = norm_ocr.prepare([line("Bookmarks", 100, 110)], [W, H], False)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(items), 1)

    def test_tab_switch_is_preserved(self):
        recs = [rec(0, sheet_lines(ROWS_A)), rec(1, sheet_lines(ROWS_A), title="Inbox - Mail", url="https://mail.example/inbox")]
        text = normalise(recs)["text"]
        self.assertIn("Tab or page switches observed: O2", text)
        self.assertIn("window title changed to 'Inbox - Mail'", text)


class OcrTests(unittest.TestCase):
    def structure(self, lines):
        return norm_ocr.structure_of(lines, [W, H], False)

    def test_table_keeps_header_to_value_relationship(self):
        st = self.structure(sheet_lines(ROWS_A, chrome=False))
        self.assertEqual(len(st["tables"]), 1)
        table = st["tables"][0]
        self.assertEqual(table["header"], HEADERS)
        self.assertEqual(table["rows"][0], ROWS_A[0])
        self.assertEqual(table["rows"][1], ROWS_A[1])

    def test_repeated_values_in_different_rows_are_not_deduplicated(self):
        table = self.structure(sheet_lines(ROWS_A, chrome=False))["tables"][0]
        self.assertEqual([r[3] for r in table["rows"]], ["Priya", "Priya", "Ravi"])

    def test_heading_is_detected_from_text_height(self):
        self.assertIn("Outreach Tracker", self.structure(sheet_lines(ROWS_A, chrome=False))["headings"])

    def test_no_table_is_invented_from_loose_text(self):
        lines = [line("Gene regulation in cellular stress", 200, 500, 900), line("We examine binding across conditions", 200, 560, 900),
                 line("Samples were cultured and sequenced", 200, 620, 900)]
        st = self.structure(lines)
        self.assertEqual(st["tables"], [])
        self.assertTrue(st["blocks"])

    def test_misaligned_rows_do_not_form_a_table(self):
        lines = [line(t, 100 + i * 300, 400) for i, t in enumerate(HEADERS)]
        lines += [line("alpha beta", 500, 450), line("gamma delta", 1500, 450), line("epsilon zeta", 200, 490), line("eta theta", 2100, 490)]
        self.assertEqual(self.structure(lines)["tables"], [])

    def test_formulas_urls_and_code_are_preserved(self):
        lines = [line("=SUM(B2:B40)", 200, 500, 300), line("https://example.com/report?id=7", 200, 560, 600),
                 line("def build(rows): return sorted(rows)", 200, 620, 800), line("~~ ## ~~", 200, 680, 100)]
        st = self.structure(lines)
        joined = " ".join(st["technical"])
        self.assertIn("=SUM(B2:B40)", joined)
        self.assertIn("https://example.com/report?id=7", joined)
        self.assertIn("def build", joined)
        self.assertNotIn("~~", joined)

    def test_duplicate_overlapping_ocr_lines_collapse(self):
        lines = [line("Quarterly planning notes for the team", 200, 500, 700)] * 3
        self.assertEqual(len(self.structure(lines)["blocks"]), 1)


class AccessibilityTests(unittest.TestCase):
    TREE = {"role": "AXWindow", "title": "Outreach Tracker", "children": [
        {"role": "AXToolbar", "children": [{"role": "AXButton", "title": "Bookmarks bar", "frame": [0, 0, 80, 20]}]},
        {"role": "AXWebArea", "title": "Outreach Tracker", "url": "https://docs.google.com/spreadsheets/d/abc", "children": [
            {"role": "AXButton", "title": "Share", "frame": [1200, 100, 80, 30]},
            {"role": "AXButton", "title": "Acme Robotics", "frame": [10, 200, 80, 30]},
            {"role": "AXTextField", "description": "Formula bar", "focused": True, "value": "=SUM(B2:B9)", "frame": [100, 150, 500, 30]}]}]}

    def test_focus_document_and_controls_are_extracted_without_a_dump(self):
        out = norm_ax.summarize(self.TREE, "acme robotics")
        self.assertEqual(out["document"], "Outreach Tracker")
        self.assertEqual(out["focused"]["label"], "Formula bar")
        self.assertEqual(out["focused"]["value"], "=SUM(B2:B9)")
        self.assertIn("Share", out["controls"])
        self.assertNotIn("Acme Robotics", out["controls"])
        self.assertNotIn("Bookmarks bar", out["controls"])

    def test_element_lookup_uses_smallest_labelled_frame(self):
        self.assertEqual(norm_ax.element_at(self.TREE, (1210, 110))["label"], "Share")
        self.assertIsNone(norm_ax.element_at(self.TREE, (5000, 5000)))

    def test_missing_tree_is_handled(self):
        self.assertIsNone(norm_ax.summarize(None))


class InteractionTests(unittest.TestCase):
    def phrases(self, events, **kw):
        return norm_interaction.summarize(events, **kw)["phrases"]

    def test_extensive_scrolling_is_phrased_as_navigation(self):
        out = self.phrases(scroll(T0, 29, -100))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("Extensive vertical scrolling, downward"))

    def test_typing_is_reported_by_counts_never_content(self):
        events = [key(T0 + k) for k in range(8)] + [key(T0 + 9, "delete"), key(T0 + 10, "return")]
        self.assertEqual(self.phrases(events), ["Keyboard entry: 8 characters typed, 1 deletions, 1 Return presses"])

    def test_click_target_from_accessibility_and_ocr_and_unknown(self):
        ax = AccessibilityTests.TREE
        items = [{"text": "Acme Robotics", "x": 400, "y": 800, "w": 300, "h": 60}]
        self.assertEqual(self.phrases([click(T0, 1210, 110)], ax_tree=ax), ["Click on 'Share' (AXButton)"])
        self.assertEqual(self.phrases([click(T0, 250, 415)], ocr_items=items, scale=2.0), ["Click on text 'Acme Robotics'"])
        self.assertEqual(self.phrases([click(T0, 9, 9)]), ["Click (target not identifiable)"])

    def test_clicks_do_not_become_editing(self):
        result = norm_interaction.summarize([click(T0, 9, 9)])
        self.assertEqual(result["kinds"], ["clicking"])

    def test_cursor_only_is_weak_and_none_is_none(self):
        moves = [{"type": "cursor_move", "t_ns": T0 + k, "payload": {"x": k * 30, "y": 0}} for k in range(5)]
        self.assertIn("weak evidence", self.phrases(moves)[0])
        self.assertEqual(self.phrases([]), ["No interaction recorded"])

    def test_chronological_order_is_preserved(self):
        events = scroll(T0, 5) + [click(T0 + 10 * S, 1, 1)] + [key(T0 + 20 * S + k) for k in range(4)]
        out = self.phrases(events)
        self.assertTrue(out[0].endswith("(5 scroll events)"))
        self.assertTrue(out[1].startswith("Click"))
        self.assertTrue(out[2].startswith("Keyboard entry"))


class ModelFacingTests(unittest.TestCase):
    def test_spreadsheet_navigation(self):
        recs = [rec(0, sheet_lines(ROWS_A)), rec(1, sheet_lines(ROWS_A), scroll(T0 + 10 * S, 14)), rec(2, sheet_lines(ROWS_B), scroll(T0 + 20 * S, 14)),
                rec(3, sheet_lines(ROWS_B))]
        out = normalise(recs)
        text = out["text"]
        self.assertEqual([text.find(h) for h in ("APPLICATION CONTEXT", "DOCUMENT STRUCTURE", "OBSERVATION SEQUENCE", "TEMPORAL SUMMARY")],
                         sorted(text.find(h) for h in ("APPLICATION CONTEXT", "DOCUMENT STRUCTURE", "OBSERVATION SEQUENCE", "TEMPORAL SUMMARY")))
        self.assertIn("Document type: spreadsheet (Google Sheets)", text)
        self.assertIn("| Company | Contact | Status | Owner |", text)
        self.assertIn("scrolling", text)
        self.assertIn("visible table rows changed", text)
        self.assertNotIn("editing", text.lower().replace("does not establish editing", ""))
        self.assertIn("table rows now shown: Umbrella", text)

    def test_spreadsheet_editing_reports_typing(self):
        typing = [click(T0 + 10 * S, 500, 500)] + [key(T0 + 10 * S + k * 10 ** 8) for k in range(1, 9)] + [key(T0 + 11 * S, "return")]
        recs = [rec(0, sheet_lines(ROWS_A)), rec(1, sheet_lines(ROWS_A), typing), rec(2, sheet_lines(ROWS_A))]
        text = normalise(recs)["text"]
        self.assertIn("Keyboard entry: 8 characters typed, 1 Return presses", text)
        self.assertIn("typing + clicking", text)

    def test_reading_without_interaction(self):
        article = [line("Why local models matter", 300, 300, 900, 64), line("Small models can run on a laptop without a network connection.", 300, 420, 1500),
                   line("They trade accuracy for privacy and cost.", 300, 470, 1200)]
        recs = [rec(i, article, title="Why local models matter", url="https://blog.example/local-models", app="Safari", bundle="com.apple.Safari") for i in range(3)]
        text = normalise(recs)["text"]
        self.assertIn("No interaction recorded", text)
        self.assertIn("Headings: Why local models matter", text)
        self.assertNotIn("Table", text)

    def test_application_switch_between_related_apps(self):
        mail = [line("Subject Acme Robotics follow up", 200, 400, 900), line("Thanks, we will review the proposal", 200, 460, 900)]
        recs = [rec(0, sheet_lines(ROWS_A)), rec(1, mail, app="Mail", bundle="com.apple.mail", title="Acme follow up", url=None), rec(2, sheet_lines(ROWS_A))]
        text = normalise(recs)["text"]
        self.assertIn("application changed from Google Chrome to Mail", text)
        self.assertIn("2 applications used", text)

    def test_unrelated_switch_changes_content(self):
        paper = [line("Gene regulation in cellular stress", 200, 500, 900), line("We examine binding across conditions", 200, 560, 900)]
        recs = [rec(0, sheet_lines(ROWS_A)), rec(1, paper, app="Preview", bundle="com.apple.Preview", title="paper.pdf", url=None)]
        text = normalise(recs)["text"]
        self.assertIn("content: ", text)
        self.assertIn("no longer visible", text)

    def test_noisy_ocr_is_filtered(self):
        noise = sheet_lines(ROWS_A) + [line("~~~ ||| ///", 100, 900), line("x", 100, 940), line("ok ok ok ok ok ok ok", 100, 980)]
        text = normalise([rec(0, noise), rec(1, noise)])["text"]
        self.assertNotIn("|||", text)
        self.assertNotIn("ok ok ok", text)

    def test_insufficient_evidence_is_still_valid_and_has_no_empty_fields(self):
        text = normalise([rec(0, [], url=None, title=None)])["text"]
        self.assertIn("No document structure could be extracted", text)
        self.assertNotIn("URL:", text)
        self.assertNotIn("Active document:", text)

    def test_every_observation_is_traceable_to_source_ids(self):
        out = normalise([rec(0, sheet_lines(ROWS_A)), rec(1, sheet_lines(ROWS_A))])
        self.assertEqual(out["tags"], {"O1": ["id0a", "id0b"], "O2": ["id1a", "id1b"]})
        for tag in out["tags"]:
            self.assertIn(f"{tag} — t+", out["text"])

    def test_before_and_after_are_labelled_as_outside_the_window(self):
        before = [{"app": "Slack", "title": "General", "t_ns": T0 - 60 * S}]
        after = [{"app": "Finder", "title": "Downloads", "t_ns": T0 + 100 * S}]
        text = normalise([rec(0, sheet_lines(ROWS_A)), rec(1, sheet_lines(ROWS_A))], before, after)["text"]
        self.assertIn("BEFORE THE WINDOW (context only, not evidence for this window)", text)
        self.assertIn("AFTER THE WINDOW (context only, not evidence for this window)", text)
        self.assertLess(text.index("TEMPORAL SUMMARY"), text.index("BEFORE THE WINDOW"))

    def test_output_is_deterministic_and_within_budget(self):
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
        big = [line(f"Report {i} {words[i % 8]} {words[(i * 3) % 8]} summary of findings", 200, 300 + i * 40, 1200) for i in range(120)]
        recs = [rec(i, big[i * 10:i * 10 + 60]) for i in range(6)]
        a, b = normalise(recs, budget=4500), normalise(recs, budget=4500)
        self.assertEqual(a["text"], b["text"])
        self.assertLess(len(a["text"]), 5200)

    def test_document_type_requires_supporting_evidence(self):
        self.assertIsNone(norm_app.document_type("com.apple.Notes", "Notes", "Shopping", None, False))
        self.assertEqual(norm_app.document_type("com.microsoft.VSCode", "Code", "main.py", None, False), "code editor")
        self.assertEqual(norm_app.document_type("com.google.Chrome", "Chrome", "x", "https://www.youtube.com/watch?v=1", False), "video site (YouTube)")


if __name__ == "__main__":
    unittest.main()
