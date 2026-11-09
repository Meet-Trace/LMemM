# Trace Landing Page Implementation Plan

**Goal:** Build a local visual prototype with equally deliberate dark and light themes, a video-centered experience, and accessible motion. Real waitlist collection is explicitly deferred by the user.

**Architecture:** One dependency-free responsive site under `website/`, with shared semantic markup and theme tokens. Native CSS and a small requestAnimationFrame loop provide the required effects without a React runtime. Source exports remain reference material, not production code.

**Spec:** `docs/brand/trace/stitch-desktop-wow-prompts.md`, amended by the decisions below and the user's delegation of visual judgment.

## Screen review and choice

All 18 Stitch entries downloaded into `docs/brand/trace/stitch-export/all/`: 17 page entries, 1 design document, 16 available screenshots. Every code export inspected; all available screenshots reviewed in comparison sheets.

- Refined Desktop `a882bf37f1cc4d2ba10c246943f0bd2d`: strongest overall sequence; selected structural foundation. Shorten its technical prose and remove generated stock-computer imagery.
- Expanding Cinema `69f05ff180c5453caee7fd22b48521a3`: selected video placement and restrained reveal treatment.
- Soft Clarity desktop `6aa49a9b3eb64db39a2933f07ed5ca13` and mobile `226ffc463ce04469a78e81f23c4d63b3`: light-theme reference; retain warmth while increasing contrast and simplifying cards.
- Follow the Thread desktop/mobile: useful connecting-line motif; avoid the very long, dense timeline.
- Original Desktop `ca72deff06aa432ba67387bf9354b560`: retain the founder's preferred split hero. Its PNG is visibly missing styles.
- Option A entries `14517901475349112259`, `4510351831025594974`, `08139e3ea6ea4c3bbabbbb48777049b5`, `14530256572035682725` have identical HTML. The screenshot-less mobile-labeled entry `d7e447a30e7e47d6ba1d0950ffccc340` also contains that same HTML; do not treat it as a separate mobile design.
- Option B and Living Thread: borrow selective details only; technical phrasing and unsupported guarantees conflict with an approachable brand.
- Remaining mobile exports: useful reading-order references; discard fake app navigation and account icons.
- No export contains an actual video element. Implement a real player.

## Design decisions

One identity, two complete themes. Figtree for headings and body; monospace only for metadata. Hero 76px desktop / 44px mobile; body 17–19px. Content max width 1160px; 24px mobile gutters. Cards 16–24px radii; pill CTAs; no framework-default widget styling.

Dark: base #101113, surface #202226, text #F5F6F8, muted #B0B5C0, accent text #9BB7FF. Light: base #F7F6F2, surface #FFFFFF, text #191B20, muted #565D69, accent text #2454D6. Action fill #2454D6 with white text in both. Borders and shadow opacity are separately tuned. The video stage remains dark in both modes.

Calculated WCAG ratios: dark body/base 17.47, muted/base 9.19, body/card 14.73, muted/card 7.75; light body/base 15.93, muted/base 6.13, body/card 17.23, muted/card 6.63; white/action 6.34. Verify any additional text pairs during implementation.

Theme uses system preference initially, explicit persistent override after selection, and `?theme=light` / `?theme=dark` preview links. A visible toggle works by keyboard and touch.

Motion: video stage scales from .95 to 1 as it enters; decorative source fragments translate up to 24px; shallow pointer tilt confined to the hero illustration. No scrolling takeover. Disable transforms on narrow/touch viewports and reduced-motion preference. Text and inputs remain stable.

## Implementation tasks

- [x] Create `website/index.html`: accessible navigation, split hero, illustrative sourced answer, cinema, three-step story, everyday use cases, AI handoff, privacy/FAQ, final invitation.
- [x] Create `website/styles.css`: complete semantic tokens and responsive components for both themes. Include focus indicators and reduced-motion rules.
- [x] Create `website/app.js`: safe theme persistence, media controls, decorative motion, and explicitly non-collecting waitlist preview. No false signup success.
- [x] Prepare local video and poster. Preserve original `VIDEO.mp4`; identify the old memIt branding as an early concept-film limitation. Do not claim the film is rebranded.
- [x] Create `website/design-system.html`: visible light/dark token and component specimens, tied to the same stylesheet.
- [x] Add `website/README.md`: local serving, theme links, deferred backend work, and asset limitations.
- [ ] Verify JavaScript syntax, HTML/local assets, computed contrast, and both theme layouts where browser access is available.

## Review focus

1. System theme/localStorage unavailable: page still renders and toggle still works.
2. Reduced motion/touch: narrative remains readable without pinning or pointer dependence.
3. Video failure: poster and honest fallback message remain; no autoplay audio.
4. Waitlist: no email leaves the browser; preview state does not claim collection.
5. Mobile: no clipped text, horizontal overflow, or hover-only content.

## Deferred launch work

Email provider and server endpoint, consent/privacy policy with actual operator details, rebranded final video, deployment/domain, and production signup testing. This pass is a local visual prototype, as requested.

## Implementation status

Visual prototype implemented under `website/`. JavaScript syntax, local asset references/unique IDs, theme toggle with unavailable storage, non-collecting form behavior, and local HTTP response verified. Core theme contrast was calculated above. Browser visual inspection and mobile viewport checks remain incomplete: the browser connector was unavailable and native browser actions were repeatedly interrupted by concurrent user interaction. Do not report those checks as passed. Preview server listens on 127.0.0.1:4173.

## Refinement pass

All landing-page files and design references moved into the self-contained `trace-landing/` directory. Added consistent SVG controls/favicon, interactive question tabs and source previews, refined typography and section rhythm, quieter borders/shadows, and stable content-driven demo positioning. Playwright inspected dark/light desktop and phone layouts. Phone width/scroll width both 390px; tabs confirmed below answer; theme toggle confirmed; demo question and source preview confirmed functional. Asset/ID and JavaScript syntax checks pass. Waitlist remains non-collecting and video retains old branding.

## Approved direction: living workspace

Replaced the prior split hero/card-grid composition with an immersive work-fragment scene and a bounded sticky desktop narrative. Three viewport chapters drive stage states 0/1/2. An interactive payoff changes question/answer, with a native source dialog. Separate film modal uses updated 42.6s video and reduced audio. Mobile/reduced motion uses three extracted concept-film stills. No diagonal CTA arrows. Waitlist remains a non-collecting preview. Implementation uses native scroll, requestAnimationFrame and CSS transitions, without additional animation dependencies.

Living-workspace verification: browser reached desktop stage states 0, 1, 2 in order; changed demo answer; source dialog opened; film played at duration 42.6s and initial player volume .7; close button paused video and restored body scroll. Mobile viewport and scroll width both 390px, sticky stage hidden and static scenes visible. Reduced-motion desktop fallback checked. JavaScript syntax and local asset/ID checks pass.

## Four finishing additions

Implemented continuous scenario-specific note identity, three-way audience selection, an interactive genuine-app screenshot walkthrough, and a final scroll-driven fragment-to-mark composition. No app recording was found; the actual native UI screenshots are labeled as earlier-build synthetic-data captures, with a clickable source hotspot and reset. Nine world/question combinations passed browser checks. Screenshot source/reset passed. Closing gather reached its final mark state. Mobile remains 390px without horizontal page overflow; reduced motion hides the sticky stage and shows a settled closing mark.
