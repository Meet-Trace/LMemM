from trace_v2.moments.inference import interaction

T0 = 1_800_000_000 * 1_000_000_000
SEC = 1_000_000_000


def scroll_events(count, dy=-40):
    events = [("scroll", {"phase": "began", "dx": 0, "dy": dy, "unit": "pixel", "x": 10, "y": 10})]
    events += [("scroll", {"phase": "changed", "dx": 0, "dy": dy, "unit": "pixel", "x": 10, "y": 10}) for _ in range(count)]
    events.append(("scroll", {"phase": "ended", "dx": 0, "dy": 0, "unit": "pixel", "x": 10, "y": 10}))
    return events


def typing_events(characters, deletes=0):
    events = [("keypress", {"phase": "down", "key_class": "character", "modifiers": [], "is_repeat": False})
              for _ in range(characters)]
    events += [("keypress", {"phase": "down", "key_class": "delete", "modifiers": [], "is_repeat": False})
               for _ in range(deletes)]
    return events


def click_events(count):
    return [("click", {"phase": "down", "button": "left", "click_count": 1, "x": 5, "y": 5}) for _ in range(count)]


def cursor_events(distance):
    return [("cursor_move", {"x": 0, "y": 0, "coalesced": 1}), ("cursor_move", {"x": distance, "y": 0, "coalesced": 1})]


def record(index, app, title, lines=(), events=(), focus=None, url=None, gap=3, ax=()):
    return {"id": f"obs-{index}", "t_ns": T0 + index * gap * SEC, "app": app, "title": title, "url": url,
            "ocr": [{"text": text, "area": "middle-centre"} for text in lines], "ax": list(ax), "focus": focus,
            "interaction": interaction.summarize(list(events))}


SHEET_LINES = ("Outreach Tracker", "Contact Status Owner Last touch", "Harbour Cafe Waiting reply Tara", "Wagtail Labs Booked call Ben")
