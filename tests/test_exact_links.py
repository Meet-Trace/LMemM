"""exact_links ties windows that are the same thing seen again; the finder uses them without letting them file anything."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import exact_links as el  # noqa: E402
import project_grouping_eval as pge  # noqa: E402
import project_reasoner_ai as ai  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "project_grouping"


def load(name):
    path = FIXTURES / f"{name}.json"
    return pge.load_day(path), json.loads(path.read_text())["timeline"]


def kinds(bundles):
    return {(b["kind"], tuple(b["ids"])) for b in bundles}


class StemTest(unittest.TestCase):
    def test_version_markers_come_off_and_nothing_else(self):
        self.assertEqual(el.stem("Harbour_Proposal_final_FINAL (1).docx"), ("harbour proposal", "docx"))
        self.assertEqual(el.stem("Copy of Pitch deck v2.key"), ("pitch deck", "key"))
        self.assertEqual(el.stem("Pitch-deck_v1.2.key"), ("pitch deck", "key"))
        self.assertEqual(el.stem("Q3 report.pdf"), ("q3 report", "pdf"))                  # a quarter is not a version
        self.assertNotEqual(el.stem("Invoice 1.pdf"), el.stem("Invoice 2.pdf"))            # a count is not a version
        self.assertNotEqual(el.stem("Budget.xlsx"), el.stem("Budget.pdf"))                 # another kind of file


class BundlesTest(unittest.TestCase):
    def test_version_chains_in_one_folder_are_linked_and_the_same_name_elsewhere_is_not(self):
        items, _ = load("versions_day")
        found = kinds(el.bundles(items))
        self.assertEqual(found, {("versions", ("fl-sheet-v1", "fl-sheet-v2")),
                                 ("versions", ("hb-word-v1", "hb-word-v2", "hb-word-final")),
                                 ("versions", ("ln-pdf-v2", "ln-pdf-final"))})
        tied = {i for _, ids in found for i in ids}
        self.assertNotIn("hb-sheet-fee", tied)                 # Budget_v2.xlsx in Harbour's folder: not Flat's file
        self.assertNotIn("hb-word-final-copy", tied)           # the copy in Downloads is another folder
        self.assertNotIn("dl-finder", tied)                    # a folder listing is not a file

    def test_a_branch_is_linked_across_a_terminal_and_a_pull_request_and_a_stash_message_is_not_a_branch(self):
        items, _ = load("dev_day")
        self.assertEqual(kinds(el.bundles(items)), {("branch", ("cx-term", "cx-page-pr"))})
        self.assertEqual(el._branches(items["lt-term"]), {"fix/login-timeout"})            # "WIP on feat/csv-export" ignored
        self.assertEqual(el._branches(items["cx-page-pr"]), {"feat/csv-export"})

    def test_nothing_is_linked_on_the_days_without_a_chain_or_a_branch(self):
        for name in ("sample_day", "held_out_day", "meaning_day", "noise_day", "pivot_day"):
            self.assertEqual(el.bundles(load(name)[0]), [], name)

    def test_a_window_on_two_branches_or_in_another_repository_is_not_linked(self):
        def term(i, title, cwd, typed=()):
            return {"id": i, "app": "Terminal", "kind": "terminal", "title": title, "state": {"cwd": cwd},
                    "activity": {"typing": {"text": list(typed)}}, "first_seen": "2026-11-11T09:00:00"}
        a = term("a", "repo (feat/one) - zsh", "~/code/repo")
        b = term("b", "repo - zsh", "~/code/repo", ["$ git checkout -b feat/one"])
        c = term("c", "repo - zsh", "~/code/repo", ["$ git checkout -b feat/one", "$ git checkout -b fix/two"])
        d = term("d", "other (feat/one) - zsh", "~/code/other")
        main = term("e", "repo (main) - zsh", "~/code/repo")
        self.assertEqual(kinds(el.bundles({"a": a, "b": b})), {("branch", ("a", "b"))})
        self.assertEqual(el.bundles({"a": a, "c": c}), [])                                  # c names two branches
        self.assertEqual(el.bundles({"a": a, "d": d}), [])                                  # same name, other repository
        self.assertEqual(el.bundles({"e": main, "f": dict(main, id="f")}), [])              # main is on everything


class FinderUsesLinksTest(unittest.TestCase):
    """With links the judge starts a chain as one task and is told, but the model still says which project."""

    def run_day(self, name, links, answer=-2.0):
        items, timeline = load(name)
        asked = []

        def judge(instruct, query, doc):
            asked.append((instruct, query, doc))
            return answer
        _, summary, log = ai.run(items, timeline, judge, None, "raw", links=links)
        return items, asked, summary, log

    def tasks(self, log):
        return [t for e in log if e["step"] == "relate" for t in e["tasks"]]

    def test_two_linked_windows_of_one_sitting_are_one_task_without_asking_the_judge_about_them(self):
        _, asked, _, log = self.run_day("versions_day", links=True)
        self.assertTrue(any({"hb-word-v1", "hb-word-v2"} <= set(t) for t in self.tasks(log)))
        self.assertTrue(any({"fl-sheet-v1", "fl-sheet-v2"} <= set(t) for t in self.tasks(log)))
        self.assertEqual([e for e in log if e["step"] == "links"][0]["bundles"][0]["kind"], "versions")
        # the same judge answering no to every pair keeps them apart when links are off
        _, _, _, plain = self.run_day("versions_day", links=False)
        self.assertFalse(any({"hb-word-v1", "hb-word-v2"} <= set(t) for t in self.tasks(plain)))
        self.assertFalse([e for e in plain if e["step"] == "links"])

    def test_the_judge_of_a_task_is_told_the_link_in_words(self):
        _, asked, _, _ = self.run_day("versions_day", links=True)
        notes = [doc for ins, q, doc in asked if ins == ai.INSTRUCT_PROJECT and "versions of one file" in doc]
        self.assertTrue(notes)
        self.assertTrue(any("Harbour_Proposal_final.docx" in doc for doc in notes))
        _, plain_asked, _, _ = self.run_day("versions_day", links=False)
        self.assertFalse([1 for ins, q, doc in plain_asked if "versions of one file" in doc])

    def test_a_link_never_files_a_task_where_the_model_says_no(self):
        items, _, summary, log = self.run_day("versions_day", links=True, answer=-2.0)
        project_of = {m: k for k, v in summary.items() for m in v["members"]}
        # the evening version is linked to the morning chain, yet the model said no to every project, so it stands alone
        self.assertNotEqual(project_of["hb-word-final"], project_of["hb-word-v1"])
        decisions = [e["decision"] for e in log if e["step"] == "assign"]
        self.assertTrue(all(d.startswith("new project") for d in decisions))


if __name__ == "__main__":
    unittest.main()
