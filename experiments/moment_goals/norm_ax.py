import re

DROP_ROLES = frozenset({"AXToolbar", "AXTabGroup", "AXMenuBar", "AXMenuBarItem", "AXScrollBar", "AXSplitter"})
DROP_TEXT = re.compile(r"bookmark|extension|memory usage|reading list|sidebar|customi[sz]e", re.I)
INTERACTIVE = frozenset({"AXButton", "AXLink", "AXMenuItem", "AXTextField", "AXTextArea", "AXCheckBox", "AXRadioButton",
                         "AXPopUpButton", "AXComboBox", "AXTab", "AXSearchField"})
VALUE_CAP = 80


def label_of(node):
    for key in ("title", "description", "identifier"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:60]
    value = node.get("value")
    if isinstance(value, str) and value.strip():
        return value.strip()[:60]
    return None


def contains(frame, point):
    if not frame or len(frame) < 4:
        return False
    x, y, w, h = frame[:4]
    return x <= point[0] <= x + w and y <= point[1] <= y + h


def walk(node, in_drop=False, out=None, depth=0):
    out = out if out is not None else []
    if not isinstance(node, dict) or depth > 14 or len(out) > 600:
        return out
    role = node.get("role")
    label = label_of(node) or ""
    dropped = in_drop or role in DROP_ROLES or bool(DROP_TEXT.search(label))
    out.append((node, dropped))
    for child in node.get("children") or []:
        walk(child, dropped, out, depth + 1)
    return out


def summarize(tree, ocr_text=""):
    if not isinstance(tree, dict):
        return None
    flat = walk(tree)
    ocr = ocr_text.lower()
    out = {"window": label_of(tree), "document": None, "url": None, "focused": None, "controls": [], "tabs": []}
    for node, dropped in flat:
        role = node.get("role")
        if role == "AXWebArea" and not out["document"]:
            out["document"] = label_of(node)
            out["url"] = node.get("url")
        if node.get("focused") and not dropped:
            value = node.get("value") if isinstance(node.get("value"), str) else None
            out["focused"] = {"role": role, "label": label_of(node), "value": (value or "")[:VALUE_CAP] or None,
                              "selected": (node.get("selected_text") or "")[:VALUE_CAP] or None}
        if role == "AXTab" and node.get("title"):
            out["tabs"].append(node["title"][:60])
        if dropped or role not in INTERACTIVE:
            continue
        label = label_of(node)
        if not label or label.lower() in ocr or label in out["controls"]:
            continue
        out["controls"].append(label)
    out["controls"] = out["controls"][:8]
    out["tabs"] = list(dict.fromkeys(out["tabs"]))[:8]
    return out


def element_at(tree, point):
    best, area = None, None
    for node, _ in walk(tree):
        frame = node.get("frame")
        if not contains(frame, point):
            continue
        label = label_of(node)
        if not label:
            continue
        size = frame[2] * frame[3]
        if area is None or size < area:
            best, area = node, size
    if best is None:
        return None
    return {"role": best.get("role"), "label": label_of(best)}
