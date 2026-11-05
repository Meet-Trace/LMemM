"""card_notes.py: one note per card, grounded names, embedding comparison and the two-order pick."""

import contextlib
import importlib.util
import io
import os
import unittest

import card_notes as cn

ITEM = {"app": "Excel", "title": "Kestrel TAM", "state": {"file": "~/Clients/Kestrel/tam.xlsx"},
        "activity": {"typing": {"text": ["market size 38,000 clinics"]}}}


class NoteTest(unittest.TestCase):
    def test_work_and_names_must_be_on_the_card(self):
        model = lambda s, u, schema: {"work": "Kestrel", "topic": "market sizing",
                                      "names": ["Kestrel", "Northwind", "clinics", "TAM"]}
        n = cn.note(model, ITEM)
        self.assertEqual(n, {"work": "Kestrel", "topic": "market sizing", "names": ["clinics", "TAM"]})
        invented = cn.note(lambda s, u, schema: {"work": "Northwind", "topic": "x", "names": []}, ITEM)
        self.assertEqual(invented["work"], "")

    def test_reference_and_draft_get_the_same_note_text(self):
        pdf = {"app": "Preview", "title": "Kestrel market report.pdf", "activity": {"reading": {"text": ["Kestrel clinics"]}}}
        doc = {"app": "Word", "title": "Kestrel sizing draft", "activity": {"typing": {"text": ["Kestrel clinics market"]}}}
        said = iter([{"work": "Kestrel", "topic": "reading the clinics market PDF", "names": []},
                     {"work": "Kestrel", "topic": "writing clinics market document", "names": []}])
        a, b = (cn.note(lambda s, u, schema: next(said), x) for x in (pdf, doc))
        self.assertEqual(cn.note_text(pdf, a), cn.note_text(doc, b))
        self.assertEqual(cn.note_text(pdf, a), "Kestrel. clinics market")

    def test_failed_or_junk_calls_give_an_empty_note(self):
        def boom(s, u, schema):
            raise OSError("down")
        self.assertEqual(cn.note(boom, ITEM), {"work": "", "topic": "", "names": []})
        self.assertEqual(cn.note(lambda s, u, schema: "yes", ITEM), {"work": "", "topic": "", "names": []})
        self.assertIn("Kestrel TAM", cn.note_text(ITEM, {"work": "", "topic": "", "names": []}))

    def test_embedder_refuses_other_machines(self):
        with self.assertRaises(ValueError):
            cn.OllamaEmbedder(url="https://example.com")


class CompareTest(unittest.TestCase):
    def test_cosine_and_shared_names(self):
        a, b = cn.unit([3, 4]), cn.unit([6, 8])
        self.assertAlmostEqual(cn.cosine(a, b), 1.0)
        self.assertEqual(cn.shared_names({"names": ["Kestrel Health"]}, {"names": ["kestrel"]}), 1)
        self.assertEqual(cn.shared_names({"names": ["Kestrel"]}, {"names": ["Northwind"]}), 0)


class PickTest(unittest.TestCase):
    def test_counts_only_when_both_orders_agree(self):
        first = lambda s, u, schema: {"pick": "A"}
        self.assertIsNone(cn.pick(first, ITEM, ITEM, ITEM))
        calls = iter([{"pick": "A"}, {"pick": "B"}])
        self.assertEqual(cn.pick(lambda s, u, schema: next(calls), ITEM, ITEM, ITEM), "a")
        calls = iter([{"pick": "B"}, {"pick": "A"}])
        self.assertEqual(cn.pick(lambda s, u, schema: next(calls), ITEM, ITEM, ITEM), "b")


class ScriptTest(unittest.TestCase):
    def test_runs_with_fakes(self):
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "note_pairs.py")
        spec = importlib.util.spec_from_file_location("note_pairs", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def model(system, user, schema):
            return {"pick": "A"} if "pick" in schema["properties"] else {"work": "", "topic": "", "names": []}

        def embed(texts):
            return [cn.unit([len(t) % 7 + 1, len(t) % 3 + 1]) for t in texts]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = mod.main(["--max-pairs", "20", "--picks", "4"], model=model, embed=embed)
        self.assertIn("related ranks higher", out.getvalue())
        self.assertEqual(len(res["rows"]), 20)
        self.assertTrue(all(p is None for _, p in res["picks"]))


if __name__ == "__main__":
    unittest.main()
