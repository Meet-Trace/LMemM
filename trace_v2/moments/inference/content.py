import re

from ..features import tokens

MIN_LENGTH = 4
MAX_LENGTH = 110
GRID_NAMES = (("top", "middle", "bottom"), ("left", "centre", "right"))
EDGE = re.compile(r"^[\W_]+|[\W_]+$")
SPACES = re.compile(r"\s+")


def normalize(text):
    return EDGE.sub("", SPACES.sub(" ", text.strip().lower()))


def area_of(box, size):
    if not box or not size or not size[0] or not size[1]:
        return None
    column = min(2, max(0, int((box[0] + box[2] / 2) / size[0] * 3)))
    row = min(2, max(0, int((box[1] + box[3] / 2) / size[1] * 3)))
    return f"{GRID_NAMES[0][row]}-{GRID_NAMES[1][column]}"


def is_noise(text):
    stripped = text.strip()
    if len(stripped) < MIN_LENGTH:
        return True
    letters = sum(c.isalpha() for c in stripped)
    alnum = sum(c.isalnum() for c in stripped)
    if letters < 3 or alnum / len(stripped) < 0.5:
        return True
    words = stripped.split()
    if len(words) >= 3 and sum(len(w) for w in words) / len(words) < 2.0:
        return True
    return bool(re.search(r"(.)\1{5,}", stripped))


def score(text, title_terms):
    value = min(len(text), 80) / 80
    terms = set(tokens(text))
    value += 0.6 * len(terms & title_terms)
    if any(c.isdigit() for c in text) and any(c.isalpha() for c in text):
        value += 0.2
    if sum(1 for w in text.split() if w[:1].isupper()) >= 2:
        value += 0.2
    return value


def salient(lines, title_terms, limit=40):
    seen = {}
    for entry in lines:
        text = entry["text"].strip()
        if is_noise(text):
            continue
        key = normalize(text)
        if not key or key in seen:
            continue
        seen[key] = {"key": key, "text": text[:MAX_LENGTH], "area": entry.get("area"),
                     "score": score(text, title_terms)}
    ranked = sorted(seen.values(), key=lambda e: (-e["score"], e["key"]))
    kept = ranked[:limit]
    covered = {e["key"] for e in kept}
    return [e for e in kept if not any(e["key"] != other and e["key"] in other for other in covered)]
