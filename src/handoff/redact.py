"""Remove secret-looking values before a handoff leaves LMemM or reaches a log."""

import re

PLACEHOLDER = "[redacted]"

_RULES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),                  # API keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),                         # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                 # AWS access key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),                      # Slack tokens
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),   # JWT
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"),
]
# password = hunter2  /  api_key: abc123...   keeps the name, drops the value
_ASSIGN = re.compile(r"(?i)\b((?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|token)"
                     r"\s*[:=]\s*)(?!\[redacted\])(\"[^\"\n]+\"|'[^'\n]+'|[^\s\"',;]{4,})")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


def redact(text):
    """(clean text, number of values removed)."""
    count = 0

    def sub(_m):
        nonlocal count
        count += 1
        return PLACEHOLDER

    for rule in _RULES:
        text = rule.sub(sub, text)

    def assign(m):
        nonlocal count
        count += 1
        return m.group(1) + PLACEHOLDER
    text = _ASSIGN.sub(assign, text)

    def card(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return sub(m)
        return m.group(0)
    text = _CARD.sub(card, text)
    return text, count
