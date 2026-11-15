"""macOS virtual keycodes -> coarse key classes. The keycode is used in memory only to pick a class and is then
discarded; nothing here (or in a stored event) lets a sequence of keys be turned back into text."""

CHARACTER_MAX_KEYCODE = 0x2F                       # letters, digits, punctuation on the main block (0x24 is return)
_SPECIAL = {0x24: "return", 0x4C: "return", 0x30: "tab", 0x31: "whitespace", 0x33: "delete", 0x75: "delete",
            0x35: "escape"}
_MODIFIER_KEYS = {0x37: "command", 0x36: "command", 0x38: "shift", 0x3C: "shift", 0x39: "caps_lock",
                  0x3A: "option", 0x3D: "option", 0x3B: "control", 0x3E: "control", 0x3F: "fn"}
_NAVIGATION = frozenset({0x7B, 0x7C, 0x7D, 0x7E, 0x73, 0x77, 0x74, 0x79, 0x72})
_FUNCTION = frozenset({0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D, 0x67, 0x6F, 0x69, 0x6B, 0x71,
                       0x6A, 0x40, 0x4F, 0x50, 0x5A})
_OTHER_CHARACTERS = frozenset({0x41, 0x43, 0x45, 0x4B, 0x4E, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
                                0x5B, 0x5C, 0x32, 0x5D, 0x5E, 0x5F})   # + grave and the JIS yen/underscore/comma keys

# CGEventFlags masks
FLAG_MASKS = {"caps_lock": 0x10000, "shift": 0x20000, "control": 0x40000, "option": 0x80000, "command": 0x100000,
              "fn": 0x800000}


def key_class(keycode):
    if keycode in _MODIFIER_KEYS:
        return "modifier"
    if keycode in _SPECIAL:
        return _SPECIAL[keycode]
    if keycode in _NAVIGATION:
        return "navigation"
    if keycode in _FUNCTION:
        return "function"
    if keycode <= CHARACTER_MAX_KEYCODE or keycode in _OTHER_CHARACTERS:
        return "character"
    return "other"


def modifiers_from_flags(flags, klass=None):
    """Held modifiers in a stable order. macOS sets the fn flag on arrow and function keys by itself, so it is
    dropped for those classes."""
    held = [name for name in ("command", "shift", "option", "control", "fn", "caps_lock") if flags & FLAG_MASKS[name]]
    if klass in ("navigation", "function") and "fn" in held:
        held.remove("fn")
    return held


def modifier_phase(keycode, flags):
    """For a flagsChanged event: "down" when the flag of that modifier key is now set, else "up"."""
    name = _MODIFIER_KEYS.get(keycode)
    if name is None:
        return None
    return "down" if flags & FLAG_MASKS[name] else "up"
