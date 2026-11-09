# Trace — Desktop landing-page explorations

Project: **7294693543806422121**

Favorite/base screen: **ca72deff06aa432ba67387bf9354b560 — Trace — Desktop Landing Page**

## How to use

Duplicate the favorite screen three times. For each copy, paste the shared brief below followed by ONE of the three direction prompts. These are alternative designs, not sequential edits. Start with Direction 1.

Reference requested by the founder: https://underrated.space/ . Inspect it if accessible for interaction inspiration; do not copy branding or layouts. It could not be inspected during preparation of this brief, so the effects below are original proposals, not descriptions of that site.

The exported desktop PNG appears to have missing styling and a broken logo asset. Preserve the intended HTML design and the actual Stitch screen, not those rendering faults.

## Shared brief — paste with every direction

```text
Create a new variation of the selected Trace desktop landing page. Preserve what makes this particular screen work: its bold headline, immediate signup, readable project-answer demonstration, restrained interface, and clear explanation of the product. Evolve it into a memorable, beautifully composed landing page with a central video experience.

Trace is a Mac app for returning to projects with context intact. Our brand should communicate ease and convenience through beautiful minimalism. The audience includes builders, people using AI at work, students, and nontechnical people with ongoing projects. A visitor should understand Trace without knowing what an agent, semantic index, or MCP is.

Primary message:
“Keep doing your thing. Trace remembers.”
Supporting copy:
“Run Trace in the background while you work. Return to your projects with the decisions, sources, and next steps that matter.”
CTA: “Get early access”. Email placeholder: “Your email address”. Helper: “Starting with macOS.”

Keep the current project design system unless the direction below explicitly changes it. Use the base screen’s Geist display typography, dark surfaces, understated blue accents, and precise interface details. Restrict monospace to small metadata and source labels; use comfortable proportional text for explanations. Make the typography feel considered and the page feel spacious. Avoid turning every section into identical cards.

Video reference, fully described:
Our 30-second concept film begins with small charcoal windows representing documents, code, notes, and browser pages on a near-black canvas. These fragments become project cards. A fine blue line connects them. A bright search field asks “what we decided on pricing”, then a clear sourced answer appears. The closing statement is “Your work, remembered.” Its visual idea is scattered work becoming clear understanding. Translate that idea into the page’s composition and motion.

Use Trace branding everywhere. The existing concept film contains the old memIt name; annotate that the video must be rebranded before publication. If the actual video is unavailable, create a designed poster and playback states rather than inserting an unrelated stock video or broken media URL.

Clean up the base screen:
- Remove the visible Simulator toolbar and state-selection buttons from the public page. Keep form states in separate component examples.
- Remove the account/avatar icon; this is an early-access page, not a logged-in app.
- Replace technical section labels such as “CONTINUOUS SUBSTRATE”, “ZERO-LEAKAGE SOVEREIGNTY”, and “Evolutionary Trajectory” with plain language.
- Replace “External LLM Prompt Injection” with “Bring your AI up to speed”.
- Use a reliable text/vector Trace mark instead of a broken external logo.
- Use meaningful section links rather than empty href placeholders in the proposed behavior.
- Remove unsupported integrations, scheduling capabilities, performance guarantees, export formats, and invented product claims.

Keep product claims accurate: supported sources require permissions; browser evidence currently requires an explicit save. Do not imply every app is automatically recorded. Retained memory stays on the Mac, but users can deliberately share context with external AI. Shared team memory is a future direction. Do not invent testimonials, customer logos, counts, or a launch date.

The page should include:
1. A concise hero and accessible email signup.
2. One major 30-second video centerpiece with a readable poster.
3. The progression “Work as usual” → “Keep the useful context” → “Pick up where you left off”.
4. Examples answering “Why did we choose this?”, “Where was that thing I found?”, and “What was I going to do next?”
5. A short section about bringing retained context into an AI workflow.
6. “In the background. Under your control.” with source selection, pause, and deletion.
7. A compact FAQ and final signup.

Design interaction keyframes as well as the static page. Show entry, midpoint, and settled states for the signature effect, plus video playing/paused and signup success/error. Annotate behavior that requires later implementation; do not pretend a static screen demonstrates working parallax.

UX rules:
- Headline and signup are visible immediately; no loading intro or forced animation.
- Keep the email form, navigation, and reading text stationary and usable.
- Use one signature motion sequence and quieter supporting interactions.
- No scroll hijacking, forced horizontal scrolling, custom cursor replacement, moving CTA targets, or mandatory hover interactions.
- Video sound starts only after an explicit user action. Provide pause, captions when speech is present, and keyboard-accessible controls.
- Desktop parallax affects decorative fragments only; use small travel distances and transform/opacity animation.
- Mobile gets a normal single-column flow and static decorative layers. Disable pinning and pointer effects on touch devices.
- Respect reduced motion. Every story and control must still work without animation.
- Avoid autoplaying the full video on page load; use a lightweight poster. If a muted ambient preview is proposed, stop it offscreen and provide pause.
- Keep essential text at accessible contrast. Decorative subtlety must not make product content unreadable.

Deliver a polished desktop variation plus its mobile adaptation. Favor clarity, ease, and a memorable product moment over the number of effects.
```

## Direction 1 — The expanding cinema (recommended)

```text
Keep the favorite screen’s split hero: headline and waitlist on the left, a compact sourced-answer scene on the right. Give both more breathing room. Keep the answer legible and its decorative source fragments sparse.

Immediately below the hero, introduce a centered landscape video frame spanning approximately 75% of the content width. Its poster depicts scattered work becoming one clear project. Overlay a restrained “Watch Trace · 30 seconds” play control.

Signature scroll sequence:
As the video enters the viewport, expand the frame smoothly from its inset width to nearly the full content width. Reduce an initial subtle perspective tilt to zero. Decorative source fragments around the outside move only 16–40px at different rates and settle behind the frame. The video itself remains stable and is never distorted during playback. This transition is short and does not pin the entire page. Clicking play finishes the expansion before playback begins.

Below the video, introduce “Your work, remembered.” as a quiet editorial line, then continue into an alternating layout of concise copy and readable product details. Use an occasional fine connecting line to carry the story between sections.

Supporting interactions: source-chip hover/focus reveals a small readable citation preview; play-button feedback is a subtle scale change; navigation gains a restrained surface after scroll. No magnetic signup buttons.

Design the wow moment around the video becoming the focal point, not around decorative spectacle. The hero should still strongly resemble the favorite desktop screen.
```

## Direction 2 — Follow the thread

```text
Preserve the favorite screen’s headline, signup prominence, and dark visual identity, but create a more editorial composition with one continuous line guiding the page.

Start with an oversized left-aligned headline and signup. Place a small project-answer preview offset to the right. Under the hero, give the video a large centered frame. The line begins beside a source fragment, passes toward the video, and continues into the next section.

Signature scroll sequence:
After the video, create one short desktop sticky scene beside three normal-flow text beats: “You work”, “You step away”, “You pick up the thread”. Across roughly 1.5–2 viewport heights, the visual changes from separated source cards to an organized project, then to one sourced answer. Draw the connecting line as the sections enter. Keep each state readable; avoid rapidly scrubbing text or forcing the visitor to watch the full sequence. Scrolling remains native and reversible.

Decorative cards may move at slightly different rates, but foreground answers and body copy remain stationary. Restrict perspective rotation to a few degrees. Use the fine line as a recurring brand device, not a tangled network.

Follow with a spacious AI-context handoff, a compact control section, and a strong final signup. Avoid repeated three-column grids. Use contrasting section proportions and generous space to create rhythm.

Mobile and reduced-motion versions show the three states stacked in ordinary document flow with no sticky scene. The video is click-to-play and independent of scrolling.

Show start, middle, and end keyframes so the intended interaction can be implemented later.
```

## Direction 3 — Soft clarity, cinematic core

```text
Explore the light, warm version of Trace’s ease and minimalism while preserving the favorite desktop page’s hierarchy and content. This is an intentional alternative theme: warm ivory canvas, near-black text, restrained cobalt actions, and clean white product surfaces. Keep a dark cinematic video section as the center of gravity. Use a warm humanist sans-serif such as Manrope for the main type, with very restrained monospace source labels. Avoid introducing a decorative serif solely to look premium.

Keep the hero split: generous headline and signup on the left, one beautifully resolved project-answer preview on the right. Use fine borders, restrained shadows, and spacious alignment instead of many containers.

The next section is a large dark video stage inset into the light page. Allow its frame to gently expand as it enters view. A small number of source fragments at the light/dark boundary travel at different speeds and visually settle into the stage. Do not animate the whole page background or rotate the video.

Under the video, return to the warm canvas. Present everyday questions in an open editorial layout with one detailed product example at a time. Use a slim blue line and small source markers to connect the examples. The feeling should be effortless, beautiful, and inviting to someone who has never used an AI agent.

Supporting interactions: 120–180ms button and input feedback, a calm signup-success transition that preserves layout height, and subtle section reveals of no more than 12px. Ensure all revealed content remains available if JavaScript or motion is disabled.

Keep the cinematic centerpiece visually related to the original Trace film so this still feels like the same brand. Design desktop, mobile, and reduced-motion states.
```

## Optional refinement prompt — after choosing one

```text
Refine the selected Trace variation without changing its overall direction. Improve the first-screen balance, headline line breaks, video prominence, product-example readability, and mobile signup flow. Remove any decorative element that competes with the answer or form. Keep one signature motion idea. Check that the page communicates ease rather than technical complexity. Make the final result feel designed as a whole, with varied spacing and section composition, not assembled from repeated templates. Provide a clean public page and separate annotations for interactions and form states.
```
