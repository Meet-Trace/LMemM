"""Keep one screenshot where several would show the same thing.

Rules, decided with the product owner:
    unchanged   the new screen matches the last stored one apart from a little noise (clock, cursor): the Observation is
                still recorded, but its screenshot is a second name for the earlier file (a hard link), so no pixels are
                stored twice and the OCR text of the earlier capture is reused.
    typing      only one small box changed (the place being typed in) and keys were pressed: the new screenshot is kept
                and the earlier one is removed, because the newer one shows everything typed so far. The removal is audited
                (screenshot_deletions, reason superseded_typing); the Observation rows and key events stay.
    changed     anything else: both screenshots are kept.
"""

import io

import numpy as np

KEY = "screenshot_dedup"
DEFAULTS = {"enabled": True, "noise_fraction": 0.001, "typing_fraction": 0.08, "pixel_delta": 12}
REASON = "superseded_typing"
WORK_WIDTH = 360


def normalise(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError(f"screenshot_dedup takes only {sorted(DEFAULTS)}")
    out = dict(DEFAULTS)
    out.update(value)
    if not isinstance(out["enabled"], bool):
        raise ValueError("enabled must be true or false")
    for name, low, high in (("noise_fraction", 0.0, 0.05), ("typing_fraction", 0.0, 0.5), ("pixel_delta", 1, 128)):
        v = out[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
            raise ValueError(f"{name} must be a number between {low} and {high}")
    return out


def get_params(store):
    from . import settings
    try:
        return normalise(settings.get(store, KEY, dict(DEFAULTS)))
    except ValueError:
        return dict(DEFAULTS)


def set_params(store, value):
    from . import settings
    return settings.put(store, KEY, normalise(value))


def picture(data, keep=None):
    """-> small greyscale array of the screenshot, or None when it cannot be read. With a list as `keep`, the decoded
    image itself is appended to it (the caller closes it) so later steps need not decode the PNG again."""
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        try:
            image.load()
            grey = image.convert("L")
            factor = max(1, grey.width // WORK_WIDTH)
            small = grey.reduce(factor) if factor > 1 else grey
            out = np.asarray(small, dtype=np.int16), (image.width, image.height)
        except BaseException:
            image.close()
            raise
        if keep is None:
            image.close()
        else:
            keep.append(image)
        return out
    except Exception:                                    # noqa: BLE001 - an unreadable image is simply kept
        return None


def compare(before, after, params):
    """-> ('unchanged' | 'typing' | 'changed', share of pixels changed, share of the screen the changes span)."""
    if before is None or after is None or before.shape != after.shape:
        return "changed", 1.0, 1.0
    moved = np.abs(before - after) > params["pixel_delta"]
    count = int(moved.sum())
    share = count / moved.size
    if share <= params["noise_fraction"]:
        return "unchanged", share, 0.0
    rows, cols = np.where(moved.any(axis=1))[0], np.where(moved.any(axis=0))[0]
    span = (rows[-1] - rows[0] + 1) * (cols[-1] - cols[0] + 1) / moved.size
    return ("typing" if span <= params["typing_fraction"] else "changed"), share, span


def pressed_keys(events):
    return any(getattr(e, "type", None) == "keypress" for e in events)


# A capture the trigger took only because time passed: nothing visible changed since the last full capture, so it
# reuses that capture's file and OCR text instead of scanning the screen again.
QUIET_REASONS = frozenset({"heartbeat", "idle_heartbeat"})
