import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

import favicons
import page_model
import projects

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20


def make_store(root, browser, profile, rows):
    folder = os.path.join(root, browser, profile)
    os.makedirs(folder)
    db = sqlite3.connect(os.path.join(folder, "Favicons"))
    db.executescript("CREATE TABLE icon_mapping(id INTEGER PRIMARY KEY, page_url TEXT, icon_id INTEGER);"
                     "CREATE TABLE favicon_bitmaps(id INTEGER PRIMARY KEY, icon_id INTEGER, width INTEGER, image_data BLOB);")
    for n, (url, width, data) in enumerate(rows, 1):
        db.execute("INSERT INTO icon_mapping(page_url, icon_id) VALUES (?, ?)", (url, n))
        db.execute("INSERT INTO favicon_bitmaps(icon_id, width, image_data) VALUES (?, ?, ?)", (n, width, data))
    db.commit()
    db.close()


class FaviconTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_exact_page_then_same_site(self):
        make_store(self.tmp.name, "BraveSoftware/Brave-Browser", "Default",
                   [("https://docs.google.com/spreadsheets/d/abc/edit", 16, PNG + b"16"),
                    ("https://docs.google.com/spreadsheets/d/abc/edit2", 32, PNG + b"32")])
        got = favicons.icon_bytes("https://docs.google.com/spreadsheets/d/abc/edit", self.tmp.name)
        self.assertEqual(got, PNG + b"16")                              # exact page wins
        got = favicons.icon_bytes("https://docs.google.com/other", self.tmp.name)
        self.assertIn(got, (PNG + b"16", PNG + b"32"))                  # any page on the site

    def test_no_icon_cases_return_none(self):
        make_store(self.tmp.name, "Google/Chrome", "Profile 1", [("https://a.com/", 16, b"not a png")])
        self.assertIsNone(favicons.icon_bytes("https://a.com/", self.tmp.name))     # not a PNG
        self.assertIsNone(favicons.icon_bytes("https://b.com/", self.tmp.name))     # unknown site
        self.assertIsNone(favicons.icon_bytes("file:///tmp/x", self.tmp.name))      # not a web page
        self.assertIsNone(favicons.icon_bytes(None, self.tmp.name))
        self.assertIsNone(favicons.icon_bytes("https://a.com/", os.path.join(self.tmp.name, "missing")))

    def test_broken_store_is_skipped(self):
        folder = os.path.join(self.tmp.name, "Arc/User Data", "Default")
        os.makedirs(folder)
        with open(os.path.join(folder, "Favicons"), "w") as fh:
            fh.write("garbage")
        self.assertIsNone(favicons.icon_bytes("https://a.com/", self.tmp.name))

    def test_is_web(self):
        self.assertTrue(favicons.is_web("localhost:8000", "web_page", None))
        self.assertTrue(favicons.is_web("Google Sheets", "document", None))
        self.assertTrue(favicons.is_web("Anything", "", "https://x.org/a"))
        self.assertFalse(favicons.is_web("Keynote", "document", None))

    def test_page_url_from_state_or_saved_text(self):
        self.assertEqual(favicons.page_url({"state": {"url": "http://localhost:8000/#w"}}), "http://localhost:8000/#w")
        item = {"state": {}, "content": {"excerpts": [{"source": {"url": "https://docs.google.com/d/1"}}]}}
        self.assertEqual(favicons.page_url(item), "https://docs.google.com/d/1")
        self.assertIsNone(favicons.page_url({"state": {"url": "javascript:1"}}))

    def test_rows_carry_kind_and_url(self):
        item = {"id": "w1", "app": "Google Sheets", "kind": "document", "title": "Tracker", "last_seen": "2026-11-05T10:00:00",
                "state": {}, "content": {"excerpts": [{"source": {"url": "https://docs.google.com/d/1"}}]}, "notes": []}
        row = page_model._thing(projects.empty(), item, datetime(2026, 11, 5, 11))
        self.assertEqual((row["kind"], row["url"]), ("document", "https://docs.google.com/d/1"))


if __name__ == "__main__":
    unittest.main()
