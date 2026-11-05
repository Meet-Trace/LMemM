# Trace landing page

Static page (no build step). Open `site/index.html` in a browser, or run `python3 -m http.server -d site 8000`.

- Tokens at the top of `styles.css` mirror `src/palette.py`; the font is the app's Figtree.
- `assets/launch.mp4` is the launch video (copied from `launchv2.mp4`); `poster.jpg` is a frame from it.
- Waitlist: `main.js` has `WAITLIST_ENDPOINT = ""`. Empty means demo mode: the form validates and says nothing was saved. Plan is a Google Apps Script web app writing email + timestamp to a Sheet.
