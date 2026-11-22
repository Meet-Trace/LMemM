"""Trace V2 privacy gates: nothing from an excluded context, and no secrets, reaches the store."""

import json
import unittest

from trace_v2.observations import Context, InteractionEvent, ScreenshotUnavailable, privacy
from trace_v2.observations.pipeline import observe_once
from test_v2_observation_pipeline import Clock, Sources, buffer
from v2_observation_helpers import T0, TempStore, click


def ctx(bundle="com.example.editor", title="notes.txt", url=None, private=None):
    window = {"title": title}
    if url:
        window["url"] = url
    if private is not None:
        window["private"] = private
    return Context({"bundle_id": bundle, "name": "App", "pid": 9}, window, {"id": "main"})


def everything_stored(store):
    """Every string in the database and every file on disk, for leak checks."""
    dump = "\n".join(store.db.iterdump())
    for oid in [o["observation_id"] for o in store.list(limit=1000)]:
        dump += json.dumps(store.get(oid))
    return dump


class GateTest(unittest.TestCase):
    CASES = [
        ("excluded_app", ctx(bundle="com.1password.1password", title="Vault: bank login")),
        ("private_window", ctx(bundle="com.google.Chrome", title="Secret plans - Incognito")),
        ("private_window", ctx(bundle="com.apple.Safari", title="Holiday", private=True)),
        ("excluded_site", ctx(bundle="com.apple.Safari", title="Sign in", url="https://accounts.google.com/x")),
    ]

    def test_withheld_contexts_store_only_time_reason_and_bundle_id(self):
        with TempStore() as ts:
            for i, (reason, context) in enumerate(self.CASES):
                with self.subTest(reason):
                    sources, buf = Sources(ctx=context, ocr_text="TOP SECRET TEXT"), buffer()
                    buf.add(click(T0 - 5))
                    buf.add(InteractionEvent("keypress", T0 - 4, {"phase": "down", "key_class": "character"}))
                    o = ts.store.get(observe_once(ts.store, sources, buf, f"s:{i}", clock_ns=Clock()).observation_id)
                    self.assertEqual(sources.screenshots, 0, "pixels must never be captured")
                    self.assertEqual(o["metadata"]["privacy"]["reason"], reason)
                    self.assertTrue(o["metadata"]["privacy"]["withheld"])
                    self.assertEqual(o["context"], {"application": {"bundle_id": context.application["bundle_id"]},
                                                    "window": {}, "display": {}})
                    self.assertEqual(o["interaction_events"], [])
                    self.assertEqual(o["metadata"]["extraction_status"]["events"]["discarded_privacy"], 2)
                    for reader in ("ocr", "accessibility"):
                        self.assertEqual((o["evidence"][reader]["status"], o["evidence"][reader]["raw"]), ("withheld", None))
                    with self.assertRaises(ScreenshotUnavailable) as caught:
                        ts.store.screenshot_bytes(o["observation_id"])
                    self.assertEqual(caught.exception.reason, "withheld")
                    self.assertEqual(buf.pending(), 0)
            dump = everything_stored(ts.store)
            for leaked in ("TOP SECRET TEXT", "Vault", "Incognito", "Holiday", "accounts.google.com"):
                self.assertNotIn(leaked, dump)
            self.assertEqual(ts.store.assets.all_relative_paths(), set())

    def test_secure_input_withholds_and_an_unknown_state_fails_closed(self):
        with TempStore() as ts:
            o = ts.store.get(observe_once(ts.store, Sources(secure=True), buffer(), "s:0", clock_ns=Clock()).observation_id)
            self.assertEqual(o["metadata"]["privacy"]["reason"], "secure_input")
            broken = Sources()
            broken.secure_input = lambda: (_ for _ in ()).throw(OSError("probe failed"))
            o = ts.store.get(observe_once(ts.store, broken, buffer(), "s:1", clock_ns=Clock()).observation_id)
            self.assertEqual(o["metadata"]["privacy"]["reason"], "secure_input")

    def test_user_policy_adds_apps_and_sites(self):
        with TempStore() as ts:
            policy = privacy.save_policy(ts.store, excluded_apps=["com.example.diary"], site_patterns=[r"payroll\.example"])
            self.assertEqual(privacy.load_policy(ts.store), policy)
            self.assertTrue(privacy.gate(ctx(bundle="com.example.diary"), policy).withheld)
            self.assertTrue(privacy.gate(ctx(url="https://payroll.example/me"), policy).withheld)
            self.assertFalse(privacy.gate(ctx(), policy).withheld)
            with self.assertRaises(Exception):
                privacy.save_policy(ts.store, site_patterns=["(unclosed"])

    def test_ordinary_context_passes(self):
        self.assertFalse(privacy.gate(ctx(title="Private equity notes.docx"), privacy.PrivacyPolicy()).withheld)


class RedactionTest(unittest.TestCase):
    SECRETS = {
        "api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "github_token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
        "card_number": "4111 1111 1111 1111",
        "password_assignment": "password: hunter2",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    }

    def test_each_secret_kind_is_replaced_in_place(self):
        for kind, secret in self.SECRETS.items():
            with self.subTest(kind):
                text, counts = privacy.redact(f"before {secret} after")
                self.assertNotIn(secret, text)
                self.assertTrue(text.startswith("before ") and text.endswith(" after") or kind == "password_assignment")
                self.assertEqual(counts, {kind: 1})

    def test_ordinary_developer_text_is_not_redacted(self):
        for text in ("commit 4b825dc642cb6eb9a060e54bf8d69288fbee4904", "order 1234 5678", "phone +91 98765 43210",
                     "2026-11-15T13:40:27.552083+00:00", "the password field is empty"):
            with self.subTest(text):
                self.assertEqual(privacy.redact(text), (text, {}))

    def test_secure_accessibility_fields_lose_their_value(self):
        tree = {"role": "AXWindow", "children": [{"role": "AXSecureTextField", "value": "hunter2", "title": "Password"}]}
        out, counts = privacy.redact(tree)
        self.assertNotIn("value", out["children"][0])
        self.assertEqual(out["children"][0]["title"], "Password")
        self.assertEqual(counts, {"secure_field": 1})

    def test_secret_on_screen_redacts_text_and_withholds_the_screenshot(self):
        secret = self.SECRETS["github_token"]
        with TempStore() as ts:
            sources = Sources(ocr_text=f"export GH={secret}",
                              ctx=ctx(title=f"env - {secret}", url=f"https://example.com/?t={secret}"))
            oid = observe_once(ts.store, sources, buffer(), "s:0", clock_ns=Clock()).observation_id
            o = ts.store.get(oid)
            self.assertEqual(o["evidence"]["screenshot"]["status"], "withheld")
            self.assertEqual(o["evidence"]["screenshot"]["error_code"], "secret_on_screen")
            self.assertEqual(o["evidence"]["ocr"]["status"], "succeeded")
            self.assertIn("[REDACTED:github_token]", o["evidence"]["ocr"]["text"])
            self.assertEqual(o["metadata"]["privacy"]["redactions"], {"github_token": 4})
            self.assertNotIn(secret, everything_stored(ts.store))
            self.assertEqual(ts.store.assets.all_relative_paths(), set())

    def test_a_secure_field_alone_keeps_the_screenshot(self):
        with TempStore() as ts:
            sources = Sources()
            sources.accessibility.result = ({"role": "AXSecureTextField", "value": "hunter2"}, "")
            o = ts.store.get(observe_once(ts.store, sources, buffer(), "s:0", clock_ns=Clock()).observation_id)
            self.assertEqual(o["evidence"]["screenshot"]["status"], "stored")    # macOS shows bullets, not the text
            self.assertNotIn("hunter2", everything_stored(ts.store))


if __name__ == "__main__":
    unittest.main()
