# Trace landing page

Self-contained visual prototype, ready to move into a separate repository. All runtime code and assets are inside this directory. No files from the parent Mac-app repository are needed.

## Run

From this directory:

```sh
python3 -m http.server 4174 --bind 127.0.0.1
```

- Dark: http://127.0.0.1:4174/?theme=dark
- Light: http://127.0.0.1:4174/?theme=light
- Design system: http://127.0.0.1:4174/design-system.html

Deploy this directory as a static site. There is no build step or package installation. Figtree is loaded from Google Fonts with an Arial fallback; the remaining runtime assets are local.

## Files

- `index.html`: landing page and illustrative product interactions.
- `styles.css`: responsive layout, sticky narrative stage, and coordinated theme tokens.
- `app.js`: theme persistence, scroll-chapter selection, product questions, source/film dialogs, and decorative parallax.
- `design-system.html`: theme/component reference.
- `assets/`: local concept film and poster.
- `docs/brand/`: brand briefs, prompts, and full Stitch source exports.
- `docs/implementation-plan.md`: original screen selection and build decisions.

## Scope and launch work

Forms validate email format but do not send or store addresses; preview copy states this. Product examples and controls are illustrations. Theme, demo tabs, source previews, FAQ, and video controls are functional.

The updated 42.6-second Trace concept film is included with audio attenuated to 30% of the supplied track. Playback is user-initiated. Before public launch: connect real signup handling, add operator/privacy/contact details, finalize the rebranded video and speech captions, and test email collection end to end.

The theme follows OS preference initially, then persists explicit choice. `?theme=light` and `?theme=dark` override it for preview. Reduced motion disables decorative transforms. Mobile uses regular document flow; no scroll hijacking or autoplay audio.

## Living workspace redesign

The opening uses custom HTML/SVG work fragments and an original geometric illustration. The desktop sticky stage transforms through scattered work, connected project, and resume states as chapters pass the viewport center. Mobile and reduced-motion layouts replace the sticky stage with three full film stills in normal document flow. These stills are derived from the supplied concept film, not represented as live app captures. Native scroll and CSS transitions keep scrolling under user control. Film playback is separate, user-initiated, and pauses on dialog close. Its track is attenuated to 30%, with initial player volume 70%.

## Finishing interactions

`scenarios.js` defines three synthetic worlds: Building, Studying, and Everyday life. The audience switch consistently updates the hero note, scroll-stage project, answer tabs, retained-source dialog, mobile illustrations, and closing note. The closing fragments gather into the Trace mark; reduced motion shows the settled mark without movement.

The guided actual-app section uses `assets/app-decision.png` and `assets/app-source.png`, copied unchanged from the native synthetic UI captures in the original application repository. They are explicitly labeled earlier-build captures with synthetic data. This is an interactive screenshot walkthrough, not a recording or a live embedded app. The two screenshots are separate from the synthetic audience examples. The supplied concept film remains independently available.

## Shared deployment

Live URL: https://trace-landing-wine.vercel.app

Vercel project: `trace-landing` in `divyaansh-seths-projects`. Deployment ID: `dpl_HfZRX6wzHiT4kkyUUMFDgYWFjFDZ`. Static deployment; no waitlist backend. Design references and browser logs are excluded through `.vercelignore`. GitHub publication was not needed for this deployment.

## Cloudflare Pages: hey-trace.app

Use GitHub repository `adityakhuntia/LMemM`, root directory `trace-landing`, build command `python3 build.py`, and output directory `dist`. The build publishes runtime files and assets only; design exports, docs, source helpers and repository data are excluded. No Node package installation is required.

The purchased domain is `hey-trace.app`. Attach it through Pages → Custom domains after the preview deployment passes. Add `www.hey-trace.app` separately if desired. Keep the existing Vercel deployment until Cloudflare is verified.

The signup form still truthfully remains a preview. Actual storage, spam protection and email provisioning must be configured and verified before enabling signup success. Email provider choice: user requested checking Zoho's free custom-domain plan; no mailbox/DNS records have yet been created.

## Photography

Hero, workday and closing photographs now use free commercial-use Pexels sources. See `IMAGE-LICENSES.md` for authors, exact source pages, verification date and file hashes. Unverified former `assets/web-images` images are removed from the public build.
