# Ambient artwork

Put the four supplied backgrounds here, with exactly these names:

    morning.webp   afternoon.webp   evening.webp   night.webp

(`.png` / `.jpg` of the same name also work; `.webp` wins if both exist.) Until **all four** are here, the app keeps
its code-drawn stand-in background. Nothing else needs to change: `./update_app.sh` (or relaunching an alias build)
picks them up. Size: about 2880-3200 px on the long side is plenty; the app decodes at most 3200 px.

Where each picture's light sits is `FOCUS` in `src/ambient_model.py`; when the window's shape differs from the
picture's, the crop keeps that point in view. Adjust it per picture once the art is in. Schedule, fade time and
movement speeds are in `src/config.py` (`AMBIENT_*`). How it works: `docs/ambient-canvas.md`.
