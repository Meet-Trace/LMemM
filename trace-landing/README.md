# Trace landing page

Static site, no build step. Deploy this directory as is (`vercel.json`, `_headers`, `robots.txt` and `sitemap.xml` are included).

## Run

```sh
cd trace-landing && python3 -m http.server 4174 --bind 127.0.0.1
```

Open http://127.0.0.1:4174.

## Files

- `index.html`: page structure (hero, film, use cases, privacy, effortless, final call to action, footer).
- `hero.css`: all styling. Design tokens are at the top of the file.
- `hero.js`: hero reveal, early-access form, use-case tabs and reconnection lines, privacy controls demo, scroll reveal.
- `brain.svg`: hero illustration (transparent background).
- `trace-launch.mp4`, `trace-launch-poster.jpg`: launch film.
- `icons/`: third-party app logos used in the page. Check each brand's usage guidelines before launch.

## Deploy

Production deploys from `main` only. `vercel.json` has an `ignoreCommand` that skips builds on every other branch. In Vercel, set the project's Root Directory to `trace-landing` and the Production Branch to `main`.

## Notes

- The email forms validate the address but send and store nothing. Connect real signup handling before launch.
- The use-case scenes, file names, numbers and the photographs (drawn in CSS) are placeholders.
- `brain.svg` is about 14 MB; consider optimising it before launch.

The previous site is archived in `archive/trace-landing-v1/`.
