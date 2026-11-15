"""Privacy gates applied BEFORE anything reaches the Observation store.

Two stages, both pure functions with no I/O:

gate(context, policy) -> Decision
    Runs on context metadata only, before any pixel or text is read. A withheld context produces an Observation
    that records only that time passed and why (reason, the app's bundle id), with no screenshot, no reader
    output, no window title or URL, and no interaction events. Reasons:
        excluded_app       bundle id in the built-in list (password managers, Keychain) or the user's list
        private_window     the adapter flagged the window private, or its title matches PRIVATE_TITLE
        excluded_site      the URL matches a built-in sensitive-site pattern or one of the user's patterns
        secure_input       macOS secure keyboard entry is on (a password field has focus)
        screen_locked      the adapter flagged the screen locked (window["locked"] is True) or a screensaver runs
        context_unavailable  the context adapter failed, so no other gate can judge what is in front: fail closed

redact(value) -> (value with secrets replaced, {kind: count})
    Walks reader output and context strings. Each match is replaced with "[REDACTED:<kind>]"; the rest of the
    text stays. Accessibility nodes whose role is AXSecureTextField lose their value entirely. When any secret
    is found in OCR or accessibility text, the screenshot is withheld too, because the pixels still show it.
"""

import re
from dataclasses import dataclass, field

POLICY_VERSION = "privacy/1"

BUILTIN_EXCLUDED_APPS = frozenset({
    "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop", "com.apple.keychainaccess",
    "com.apple.Passwords", "com.lastpass.LastPass", "com.dashlane.dashlanephonefinal", "org.keepassxc.keepassxc",
    "com.nordpass.macos.NordPass",
})
PRIVATE_TITLE = re.compile(r"private browsing|incognito|inprivate|\bprivate (?:tab|window)\b|\(private\)", re.I)
BUILTIN_SITE_PATTERNS = (
    r"accounts\.google\.com", r"appleid\.apple\.com", r"/(?:login|signin|sign-in|password|2fa|mfa)\b",
    r"bank|netbanking|paypal\.|wise\.com|stripe\.com/(?:dashboard|login)",
)


def _luhn(digits):
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


# kind -> pattern. Conservative on purpose: git SHAs and ordinary long words must not be redacted.
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "api_key": re.compile(r"\b(?:sk|pk|rk)-(?:live-|test-|proj-|ant-)?[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "password_assignment": re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*\S+"),
    "card_number": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
}


@dataclass(frozen=True)
class PrivacyPolicy:
    excluded_apps: frozenset = field(default_factory=frozenset)       # the user's own additions
    site_patterns: tuple = ()                                          # the user's own additions (regex)
    redact_secrets: bool = True

    def all_excluded_apps(self):
        return BUILTIN_EXCLUDED_APPS | self.excluded_apps

    def site_regex(self):
        return re.compile("|".join(f"(?:{p})" for p in BUILTIN_SITE_PATTERNS + tuple(self.site_patterns)), re.I)


POLICY_KEY = "privacy_policy"


def load_policy(store):
    from . import settings
    doc = settings.get(store, POLICY_KEY, {})
    return PrivacyPolicy(frozenset(doc.get("excluded_apps", ())), tuple(doc.get("site_patterns", ())),
                         doc.get("redact_secrets", True))


def save_policy(store, excluded_apps=(), site_patterns=(), redact_secrets=True):
    from . import settings
    for p in site_patterns:
        re.compile(p)                                        # refuse an invalid pattern now, not at capture time
    if not isinstance(redact_secrets, bool):
        raise ValueError("redact_secrets must be true or false")
    doc = {"excluded_apps": sorted(set(excluded_apps)), "site_patterns": list(site_patterns),
           "redact_secrets": redact_secrets}
    settings.put(store, POLICY_KEY, doc)
    return load_policy(store)


@dataclass(frozen=True)
class Decision:
    withheld: bool
    reason: str = None


def gate(context, policy, secure_input=False):
    if context.error_code:
        return Decision(True, "context_unavailable")
    app, window = context.application or {}, context.window or {}
    if app.get("bundle_id") in policy.all_excluded_apps():
        return Decision(True, "excluded_app")
    if window.get("private") is True or PRIVATE_TITLE.search(str(window.get("title") or "")):
        return Decision(True, "private_window")
    url = window.get("url")
    if url and policy.site_regex().search(str(url)):
        return Decision(True, "excluded_site")
    if secure_input:
        return Decision(True, "secure_input")
    if window.get("locked") is True:
        return Decision(True, "screen_locked")
    return Decision(False)


def minimal_context(context):
    """What a withheld Observation keeps: the app's bundle id only (no name, title, URL or display detail)."""
    from .models import Context
    return Context({"bundle_id": (context.application or {}).get("bundle_id")}, {}, {}, context.error_code)


def _redact_text(text, counts):
    for kind, pattern in SECRET_PATTERNS.items():
        def sub(m, kind=kind):
            if kind == "card_number" and not _luhn(re.sub(r"\D", "", m.group(0))):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            return f"[REDACTED:{kind}]"
        text = pattern.sub(sub, text)
    return text


def redact(value, counts=None):
    counts = {} if counts is None else counts
    if isinstance(value, str):
        return _redact_text(value, counts), counts
    if isinstance(value, list):
        return [redact(v, counts)[0] for v in value], counts
    if isinstance(value, dict):
        out = {}
        secure = value.get("role") == "AXSecureTextField" or value.get("subrole") == "AXSecureTextField"
        for k, v in value.items():
            if secure and k in ("value", "text", "title_value"):
                counts["secure_field"] = counts.get("secure_field", 0) + 1
                continue
            out[k] = redact(v, counts)[0]
        return out, counts
    return value, counts
