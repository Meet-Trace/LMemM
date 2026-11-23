import re

WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)


TECHNICAL = re.compile(r"(https?://\S+|^=[A-Z]+\(|^=\S|\b(def|class|import|function|return|const|let|var|SELECT|FROM)\b|[{};]|=>|::|->|\w+\(.*\))")


def is_technical(text):
    return bool(TECHNICAL.search(text))


def usable(text):
    if len(text) < 4:
        return False
    if is_technical(text) and len(text) >= 6:
        return True
    letters = sum(c.isalpha() for c in text)
    if letters / len(text) < 0.4:
        return False
    symbols = sum(not (c.isalnum() or c.isspace()) for c in text)
    if symbols / len(text) > 0.35:
        return False
    words = WORD.findall(text)
    if not words:
        return False
    if len(words) >= 3 and len({w.lower() for w in words}) / len(words) < 0.5:
        return False
    return True
