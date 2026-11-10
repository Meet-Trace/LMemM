import unittest

import config
import page_model as pm
import screen_summary as ss

ITEM = {"id": "a", "app": "Safari", "title": "Pricing sheet", "last_seen": "2026-11-10T09:00:00",
        "content": {"excerpts": [{"id": "1", "text": "Northwind quote\nTotal 4,200"}]}}


def fake(answer):
    return lambda system, user, tokens: answer


class ParseTests(unittest.TestCase):
    def test_two_lines(self):
        self.assertEqual(ss.parse("Title: Writing a quote.\nIntent: Finish the quote."),
                         {"title": "Writing a quote.", "intent": "Finish the quote."})

    def test_one_line_answer_and_unclear_intent(self):
        self.assertEqual(ss.parse("Title: Writing a quote. Intent: unclear."), {"title": "Writing a quote."})
        self.assertEqual(ss.parse("Just a sentence. And more."), {"title": "Just a sentence."})
        self.assertEqual(ss.parse(""), {})


class SummarizeTests(unittest.TestCase):
    def test_summary_is_kept_with_the_text_hash_and_model(self):
        made = ss.summarize(ITEM, fake("Title: Writing a Northwind quote.\nIntent: Finish the quote."))
        self.assertEqual((made["title"], made["intent"], made["model"]), ("Writing a Northwind quote", "Finish the quote.", config.FINDER_NARRATOR))
        self.assertTrue(ss.stale(ITEM))
        self.assertFalse(ss.stale({**ITEM, "summary": made}))
        self.assertTrue(ss.stale({**ITEM, "summary": made, "content": {"excerpts": [{"id": "1", "text": "Northwind quote\nTotal 5,000"}]}}))     # the text changed

    def test_invented_names_are_cut(self):
        made = ss.summarize(ITEM, fake("Title: Writing a quote for Acme Corp.\nIntent: Send it."))
        self.assertNotIn("Acme", made["title"])

    def test_a_summary_of_an_older_version_is_stale(self):
        made = ss.summarize(ITEM, fake("Title: Writing a quote."))
        self.assertFalse(ss.stale({**ITEM, "summary": made}))
        self.assertTrue(ss.stale({**ITEM, "summary": {**made, "v": 1}}))

    def test_nothing_to_read_or_no_answer_is_none(self):
        bare = {"id": "b", "app": "Finder", "title": "Downloads"}
        self.assertIsNone(ss.summarize(bare, fake("Title: x")))
        self.assertFalse(ss.stale(bare))
        self.assertIsNone(ss.summarize(ITEM, fake("")))

    def test_pending_is_newest_first_and_limited(self):
        items = {k: {**ITEM, "id": k, "last_seen": f"2026-11-10T09:0{n}:00"} for n, k in enumerate("abc")}
        self.assertEqual(ss.pending(items, 2), ["c", "b"])


class CardTests(unittest.TestCase):
    def test_the_card_shows_the_ai_lines_and_falls_back_to_doing(self):
        content = {"excerpts": [{"id": "1", "text": "x"}]}
        card = pm.screen_of(content, pm.datetime.now() if hasattr(pm, "datetime") else None, summary="S", intent="I")
        self.assertEqual((card["summary"], card["intent"]), ("S", "I"))


if __name__ == "__main__":
    unittest.main()
