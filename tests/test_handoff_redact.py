import unittest

from handoff.redact import PLACEHOLDER, redact


class RedactTests(unittest.TestCase):
    def test_removes_known_secret_shapes(self):
        for secret in ("sk-abcdefghijklmnop1234", "ghp_" + "a" * 30, "AKIAABCDEFGHIJKLMNOP",
                       "xoxb-1234567890-abcdef", "eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4",
                       "Bearer abcdefghijklmnopqrstuv"):
            out, n = redact(f"use {secret} here")
            self.assertNotIn(secret, out)
            self.assertEqual(n, 1, secret)

    def test_keeps_the_name_of_an_assigned_secret(self):
        out, n = redact("password = hunter2hunter2 and api_key: 'abc12345'")
        self.assertEqual(n, 2)
        self.assertIn(f"password = {PLACEHOLDER}", out)

    def test_card_numbers_need_a_valid_checksum(self):
        self.assertEqual(redact("pay 4111 1111 1111 1111 now")[1], 1)
        self.assertEqual(redact("order 1234 5678 9012 3456 shipped")[1], 0)

    def test_private_key_block(self):
        out, n = redact("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
        self.assertEqual((out, n), (PLACEHOLDER, 1))

    def test_ordinary_text_is_untouched(self):
        text = "Use the token bucket algorithm; the password field must be hashed."
        self.assertEqual(redact(text), (text, 0))


if __name__ == "__main__":
    unittest.main()
