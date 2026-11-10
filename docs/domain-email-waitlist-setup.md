# Trace domain, email and waitlist setup

The founder will finalize the domain with the cofounder and buy it tomorrow. No purchase, Cloudflare account change, DNS change, email send or live signup provisioning has been performed by this work.

## What you do first

1. Finalize a `.com` name. Current preference: `tracewithyou.com`, with `traceforwork.com` also shortlisted. Registry checks found no registration records, but that does not reserve a name or guarantee standard pricing. Check availability and first-year/renewal price in Cloudflare Registrar before purchase. Avoid doubled `tt`, per the founder's preference.
2. Register the chosen domain for one year in your own Cloudflare account. The site can use Cloudflare Pages; a separate paid web-hosting package is not needed just to serve this static landing page.
3. Tell me the purchased domain. Do not post your password, recovery codes or API secrets in chat.

## What we configure next

- **Website:** connect the GitHub repository `adityakhuntia/LMemM`, use the approved `main` website, publish only its runtime assets, verify the Pages preview, and then attach the custom domain. The cofounder may need to approve GitHub app access to the repository. Keep the current Vercel site until the new deployment works. `trace-landing/.vercelignore` is not itself a Cloudflare publishing configuration.
- **Incoming email:** create `hello@<chosen-domain>` and route it to a dedicated Gmail inbox or another verified destination. Cloudflare Email Routing supports forwarding on its free plan. Forwarding is not a full mailbox for sending ordinary replies from that address; choose a sending mailbox/provider separately if you want that workflow. We do not need to pay for a mailbox before deciding this.
- **Actual waitlist:** use a Pages Function with a D1 database for durable signup storage and Turnstile verification for public submissions. Proposed fields: email, which group best describes you (builder / agentic AI at work / student / everyday use), and whether you use a Mac. This helps choose the first 20 diverse beta users. Configure credentials/bindings in Cloudflare, not in browser JavaScript.
- **Acceptance:** submit an authorized test signup, verify a stored record, retry the same email to check deduplication, test invalid email/error states, check keyboard/mobile/light/dark behavior, and only then replace the truthful preview notice. Automatic confirmation emails are a separate setup step; signup storage does not require pretending an email was sent.

The waitlist backend is not implemented or provisioned yet. The published form continues to prevent submission and clearly says registration is not open. The Mac app privacy explanation concerns app capture; the waitlist will also need a short accurate notice about storing submitted contact details.

## Official setup references

- [Cloudflare Registrar costs](https://developers.cloudflare.com/registrar/faq/)
- [Pages Git integration](https://developers.cloudflare.com/pages/get-started/git-integration/)
- [Static HTML on Pages](https://developers.cloudflare.com/pages/framework-guides/deploy-anything/)
- [Cloudflare Email Service and incoming routing](https://developers.cloudflare.com/email-service/)
- [Pages Function bindings, including D1](https://developers.cloudflare.com/pages/functions/bindings/)
- [Turnstile setup](https://developers.cloudflare.com/turnstile/get-started/)
