# Living ambient canvas

The window's background is four supplied pictures (morning, afternoon, evening, night) with very slow light on top.

| Piece | Where |
|---|---|
| Which period is showing (schedule from `config.AMBIENT_SCHEDULE`, local time, pin in Settings > General) | `src/backdrop.py` |
| Artwork lookup, crop around the lit region, light-field data | `src/ambient_model.py` (pure, tested) |
| Layers, animations, cross-fade, pause/resume | `src/ambient.py` (Core Animation only) |
| Wiring (one view behind the sidebar and pages; clock tick once a minute) | `src/window.py` `_sync_ambient` |
| Code-drawn stand-in, used until all four pictures are in `assets/ambient/` | `src/window.py` `_Backdrop` |

Behaviour: a period change fades the new picture over the old (`AMBIENT_FADE_SECONDS`, 14 s; a manual pick in Settings
about 1.2 s). Lights drift 45-90 s and swell 30-60 s with opacity/transform animations run by the compositor; nothing is drawn per
frame. Movement stops when the window is hidden, minimised, covered or the app is hidden, and when Reduce Motion is on
(or `AMBIENT_MOTION = False`). The grain is one static tile (`AMBIENT_GRAIN`); the pictures' own grain is untouched.
Only the showing picture is kept decoded.

Checking CPU/GPU on a Mac: open Activity Monitor (CPU and GPU history, filter "LMemM"), leave the window open and still for a
minute, then compare with `AMBIENT_MOTION = False` and with the pictures moved away (stand-in). Expect near-zero CPU
between redraws; Instruments > Core Animation shows the frame rate.
