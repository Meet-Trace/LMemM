# Trace — Stitch design brief and prompts

Use Prompt 1 to establish the project’s design system. Then use Prompt 2 to generate the landing page. Use the three variation prompts afterward to explore layouts within the same brand. No image or storyboard attachment is required.

## Prompt 1 — Design system

Create a reusable brand and web design system for **Trace**, a Mac app being developed into shared memory for people and their AI.

### Brand idea

Trace helps people return to their work with the relevant context intact. It brings together project activity, retained sources, decisions, and next steps. The desired feeling is: “I keep doing my thing. Trace remembers.”

Personality: quiet intelligence, clarity, discretion, continuity, and confidence. Accessible to students and nontechnical people while credible to builders and professionals using AI. The product should feel like a useful companion already belonging on your computer.

### Visual reference, described in full

Our 30-second concept video uses an almost-black background with generous empty space. Small charcoal application windows float across the scene: documents, code, notes, browser pages, and messages. Their details are subdued while the central message is bright and readable. The scattered windows resolve into compact project cards. A thin blue line connects the organized cards. A bright pill-shaped search field appears above a dark answer card, which includes a decision and small source chips. The final scene shows a blue rounded-square icon containing a short white horizontal pill, with the line “Your work, remembered.”

Translate this visual language into a cohesive website. Replace the video's old “memIt” branding with “Trace” everywhere. Preserve the idea of scattered context becoming clear, connected understanding.

### Suggested tokens

These are proposed web tokens inspired by the video, not exact sampled values.

- Main background: ink black #101113.
- Alternate section background: #151619.
- Card surface: charcoal #202226.
- Elevated surface: #292C32.
- Primary text: soft white #F5F6F8.
- Secondary text: #B0B5C0; keep essential copy readable.
- Muted decorative border: #343842.
- Primary accent: vivid blue #4778F5.
- Accent hover: #6790FF.
- Focus indicator: #9BB7FF.
- Optional source-type colors: restrained lavender, teal, and coral, restricted to tiny source icons or indicators.
- Blue is reserved for key actions, active states, meaningful connections, and important phrases. Keep glows small and localized.
- Primary typeface: Figtree; use Inter or a system sans-serif as a fallback.
- Display typography: medium or semibold, compact line height, slightly tight tracking. Desktop hero approximately 64–80px; mobile 40–48px. Avoid ultra-thin text.
- Body: 17–19px with comfortable line height. Small labels should remain readable.
- Spacing: an 8px base rhythm; generous section spacing and clear alignment.
- Main content width: approximately 1120–1200px, with spacious outer gutters.
- Product-card corners: approximately 12–16px. Search fields and primary CTA may use pill shapes.
- Borders: fine and restrained. Shadows should establish layered depth without making everything look luminous.

### Brand assets and components

Create a simple lowercase “trace” wordmark. Explore a compact app mark based on the video’s blue rounded square and white horizontal pill. Keep it readable at favicon size. Treat the mark as a concept, not a finalized logo.

Define components for navigation, primary/secondary buttons, email inputs, source chips, project cards, sourced-answer cards, video containers, FAQ accordions, and footer links. Include hover, keyboard focus, disabled, loading, validation-error, and signup-success states.

The signature graphic is a fine continuous line connecting fragments of work to a project and then to a useful answer. Make it purposeful rather than an arbitrary network diagram.

### Motion and accessibility

Use gentle fades and short translations. Scattered cards can settle into alignment; a connecting line can draw toward an answer. The useful answer is the visual payoff. Keep text stationary while being read. Avoid perpetual spinning, fast parallax, elaborate particle fields, and scroll hijacking. Provide reduced-motion and static fallbacks.

Meet WCAG AA contrast for readable content and controls. Background fragments may be subdued only when decorative. Use visible focus states, labeled form fields, and comfortably sized touch targets.

### Avoid

Generic AI brains, robot mascots, neon-purple gradients, stock-photo teams, excessive glass effects, giant bento grids, unreadable miniature dashboards, fabricated customer logos, invented metrics, and enterprise jargon.

Output a coherent design system that can support three distinct landing-page compositions without changing Trace’s identity.

## Prompt 2 — Landing page

Design a polished, responsive waitlist landing page for **Trace**, using the established Trace design system. Create desktop and mobile designs. This is a design exploration before implementation.

### Context and goal

Trace is a Mac app that helps people recover the context behind their projects: retained sources, decisions, and next steps. Its long-term vision is shared memory for people and their AI, across tools and sessions.

The first cohort will include builders, people using agentic AI at work, students, and nontechnical users. They share one need: returning to an ongoing project without reconstructing everything. Explain the product through recognizable situations rather than technical architecture.

Primary conversion: join the early-access waitlist. Secondary action: watch a 30-second concept demo. Make the background-assistant experience feel effortless, tangible, and under the user’s control.

### Accuracy boundaries

Background activity tracking does not mean every app’s content is automatically captured. Supported sources require permissions, and browser evidence currently requires an explicit save. Do not claim universal capture, automatic recording of every conversation, or complete compatibility with all apps. Cross-user shared memory is the longer-term vision, not a feature to present as already shipped.

Retained memory is local, but voluntarily sending context to an external AI is a separate action. Do not use absolute claims such as “nothing ever leaves your device.” Do not invent user counts, testimonials, endorsements, integrations, or launch dates.

### Page structure and exact copy

1. **Minimal navigation**
   - Trace wordmark on the left.
   - Links: How it works, Your control.
   - Primary button: Get early access.
   - Keep navigation unobtrusive and easy to use on mobile.

2. **Hero: immediate value and signup**
   - Eyebrow: YOUR WORK, REMEMBERED.
   - Headline: “Keep doing your thing. Trace remembers.”
   - Supporting copy: “Run Trace in the background while you work. Return to your projects with the decisions, sources, and next steps that matter.”
   - Inline email form with a persistent accessible label and placeholder “Your email address”.
   - Submit button: “Get early access”.
   - Helper text: “Starting with macOS.”
   - Secondary action: “Watch the 30-second demo”.
   - Make the headline and signup immediately visible, without waiting for an animation.

3. **Hero product moment**
   - Show a few fragments of work resolving into a coherent project, with one readable answer as the focal point.
   - Example project: “Pricing launch”.
   - Example question: “What did we decide about pricing?”
   - Example answer: “We chose usage-based pricing. The free tier stays, and team features move to Pro.”
   - Supporting source chips: “Pricing notes” and “Saved research”.
   - Small label: “Illustrative example”.
   - Depict document, browser, and note fragments generically. Do not imply unsupported integrations with third-party logos.
   - Keep the answer readable at actual display size. On mobile, simplify the surrounding fragments instead of shrinking the whole scene.

4. **The story: fragmented work becomes continuity**
   - Section headline: “Your work is scattered. Your context doesn’t have to be.”
   - Three beats: “Work as usual.” → “Keep the useful context.” → “Pick up where you left off.”
   - Supporting sentence: “Connect supported sources, save what matters, and let Trace help you find your way back.”
   - Show the same project evolving through these beats. Use composition and a connecting line rather than three unrelated feature illustrations.

5. **Three familiar questions**
   - “Why did we choose this?” — “Return to decisions and the sources behind them.”
   - “Where was that thing I found?” — “Find useful material you retained for the project.”
   - “What was I going to do next?” — “Recover the next steps you recorded.”
   - Show short product examples, not an abstract feature checklist. Make the section feel relevant to work, studying, and personal projects.

6. **Bring AI into the story**
   - Headline: “Your next AI conversation shouldn’t start from zero.”
   - Body: “Bring relevant project context into your AI workflow, so you can spend less time explaining and more time moving forward.”
   - Show a project-context preview being deliberately handed to an assistant. Keep the interaction understandable without protocol names.
   - Vision caption: “We’re building toward shared memory for people and their AI.”
   - Clearly distinguish this future vision from current individual context export.

7. **Trust and control**
   - Headline: “In the background. Under your control.”
   - Three concise controls: “Choose supported sources.” “Pause collection.” “Inspect and delete retained memory.”
   - Supporting copy: “Retained memory stays on your Mac. You choose when to share context with an external AI.”
   - Show these controls as a simple product detail, not a wall of security badges.

8. **Compact FAQ**
   - “Does Trace remember everything on my computer?” — “No. Trace works with supported sources and the permissions you choose. Some content, including browser evidence, needs an explicit save.”
   - “Do I need to use AI?” — “No. Trace also helps you return to saved project context yourself.”
   - “Can my team use it together?” — “Shared team memory is part of our vision. Early access starts with the individual Mac experience.”
   - “When can I try it?” — “Join the waitlist. We’re inviting a small first group to try Trace and help shape it.”

9. **Final invitation**
   - Headline: “Come back with context.”
   - Body: “Join the first people shaping Trace—for work, studies, and everyday projects.”
   - Repeat the email signup and “Get early access” CTA.
   - Footer: Trace, Contact, Privacy, and a short copyright line. Treat link destinations as implementation placeholders.

### Form and interaction design

Show a successful signup state: “You’re on the list.” Supporting text: “We’ll email you when early access is ready.”

After success, offer optional questions: “What would you use Trace for?” and “Do you use a Mac?” Include a clear Skip action. Do not make these questions prerequisites for signup.

Also design loading, invalid-email, duplicate-signup, and temporary-error states. Do not imply these are functional until the implementation is connected.

### Deliverable

Produce a complete landing-page design with a deliberate reading rhythm, a strong first screen, a readable product demonstration, and clear signup opportunities. Include a desktop design around 1440px wide and a mobile design around 390px wide. Indicate how the concept video would fit, but make the design self-contained without a supplied video file. Include static poster and reduced-motion states.

## Prompt 3A — UI option: Cinematic continuity

Create a variant of the Trace landing page using the existing design system and content. Center the hero headline and signup. Place the product demonstration immediately below them, with scattered work fragments at the outer edges resolving into one central answer. Make the visual storytelling feel continuous across the first two sections. The connecting line should guide the eye down the page. Keep the first screen useful even with motion disabled. Favor spacious, cinematic composition with the product answer as the focal point. Preserve readable content and a prominent waitlist form.

## Prompt 3B — UI option: Product first

Create a distinctly different layout using the same Trace design system and copy. Use a split hero: headline, explanation, and signup on the left; a large, readable project-memory interaction on the right. Add a small “Watch the demo” action below the interaction. Organize the lower page as alternating text and product details rather than a repeated card grid. On mobile, place the signup before the product interaction. This option should make the product easiest to understand and the signup easiest to find.

## Prompt 3C — UI option: The living thread

Create a third layout using the same Trace design system and copy. Build an editorial composition around a continuous vertical trace connecting three moments: working on a project, stepping away, and returning with context. Start with an oversized left-aligned headline and a compact signup form. Use staggered product fragments beside the narrative, resolving into a large answer card. Let generous spacing and varied section proportions establish rhythm. Keep decorative lines out of text and controls. On mobile, simplify to a clean single-column sequence with a slim trace at the edge. Avoid turning the page into a dense timeline or a dashboard.

## Selection guidance

- Choose A if the priority is matching the concept video’s atmosphere.
- Choose B if the priority is immediate product clarity and waitlist conversion.
- Choose C if the priority is a distinctive brand story with room for restrained motion.
- Recommended starting point: generate B first, then explore A and C as variants. Compare the first screen, product readability, mobile layout, and signup clarity before selecting a direction.
