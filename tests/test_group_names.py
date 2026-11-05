"""group_names.py: a tiny model names a group code made; names must be grounded."""

import unittest

import group_names as gn

ITEMS = {
    "a": {"app": "WhatsApp", "title": "180DC DTU Partnership Leads | 2026 - 27"},
    "b": {"app": "WhatsApp", "title": "180DC DTU Partnership Leads | 2026 - 27"},
    "c": {"app": "WhatsApp", "title": "180DC DTU Marketing Leads | 2026 - 27"},
}


def says(name):
    return lambda system, user, schema: {"name": name}


class NameTest(unittest.TestCase):
    def test_grounded_model_name_is_used(self):
        self.assertEqual(gn.suggest(says("180DC DTU Leads"), ITEMS, ITEMS), {"name": "180DC DTU Leads", "by": "model"})

    def test_invented_app_or_generic_names_fall_back_to_the_shared_title(self):
        for bad in ("Consulting Club", "WhatsApp", "Project", "unsure", ""):
            r = gn.suggest(says(bad), ITEMS, ITEMS)
            self.assertEqual(r["by"], "title", bad)
            self.assertEqual(r["name"], "180DC DTU Partnership Leads | 2026 - 27"[:40])

    def test_failed_model_and_no_shared_title_use_the_code_name(self):
        def boom(system, user, schema):
            raise OSError("down")
        items = {"x": {"app": "Terminal", "title": "terminal-4d5ce746"}, "y": {"app": "Finder", "title": "Downloads"},
                 "z": {"app": "Preview", "title": "GroundEval.pdf"}}
        self.assertEqual(gn.suggest(boom, items, items, fallback="Craftlng"), {"name": "Craftlng", "by": "code"})
        self.assertEqual(gn.suggest(None, items, items, fallback="X")["by"], "code")


if __name__ == "__main__":
    unittest.main()
