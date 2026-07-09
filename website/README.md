# AHB123.com — Website Redesign (Cloudflare-ready)

Marketing & lead-funnel redesign for **All Home Building Co. LLC** (ahb123.com), built as a
static site that deploys to **Cloudflare Pages** with lead capture via Pages Functions.

## What's here

```
website/
├── index.html              ← LIVE HOMEPAGE — Scheme A · Momentum (chosen design)
├── schemes.html            ← scheme showcase / picker (all three directions)
├── v2-homestead/index.html ← Scheme B · light, trust-first (before/after slider)
├── v3-instant/index.html   ← Scheme C · conversion landing page (instant estimator)
├── review/index.html       ← "Leave a Review" page — the QR code target (/review)
├── functions/api/
│   ├── lead.js             ← POST /api/lead        — captures leads into D1 + notifies agents
│   ├── estimate.js         ← POST /api/estimate    — server-side ballpark pricing
│   ├── chat.js             ← POST /api/chat        — relays to the Nova Sterling agent
│   ├── reviews.js          ← GET  /api/reviews     — real reviews (QR + Thumbtack/Angi)
│   └── review/submit.js    ← POST /api/review/submit — stores a new QR review (pending)
├── assets/
│   ├── nova-chat.js        ← Nova Sterling live chat widget (on every page)
│   ├── review-qr.svg       ← QR code → https://ahb123.com/review (scan to review)
│   └── (logo — images sync from R2 in production)
├── wrangler.toml           ← Pages project + D1/KV/R2 bindings
├── _headers                ← security + cache headers
├── _redirects              ← route the winning scheme to /
└── CLOUDFLARE_MIGRATION.md ← full migration + AI-management plan
```

## The three schemes

| Scheme | Vibe | Best for | Signature feature |
|--------|------|----------|-------------------|
| **A · Momentum** | Dark, premium, modern | High-ticket positioning | Multi-step estimate funnel |
| **B · Homestead** | Light, warm, editorial | Local SEO + reviews | Drag before/after slider |
| **C · Instant** | High-contrast, focused | Paid ads / Google | Live instant-price calculator |

All three share the same brand (logo, cyan→blue→orange palette, NAP) so you can also **mix**:
e.g. Homestead's trust content with Momentum's funnel.

## Preview locally

No build step — it's static HTML. Any static server works:

```bash
cd website
python3 -m http.server 8080
# open http://localhost:8080
```

The `/api/*` calls will 404 locally (that's fine — every form degrades gracefully and still
shows the success state). To test Functions locally, use Wrangler:

```bash
npx wrangler pages dev website
```

## Deploy to Cloudflare Pages

```bash
# one-time: create the backing resources
npx wrangler d1 create ahb123
npx wrangler kv namespace create ahb123-config
npx wrangler r2 bucket create ahb123-media

# seed the leads table
npx wrangler d1 execute ahb123 --command "CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, name TEXT, phone TEXT, email TEXT, zip TEXT, service TEXT, budget TEXT, timeline TEXT, estimate TEXT, message TEXT, source TEXT, ip TEXT, user_agent TEXT, status TEXT DEFAULT 'new');"

# deploy
npx wrangler pages deploy website --project-name ahb123
```

Then fill the real `database_id` / KV `id` into `wrangler.toml` (or bind them in the Pages
dashboard), pick the winning scheme in `_redirects`, and point `ahb123.com` at the Pages project.

Full step-by-step migration (DNS cutover, R2 image sync, agent hand-off) is in
[`CLOUDFLARE_MIGRATION.md`](./CLOUDFLARE_MIGRATION.md).

## Honest-by-default content

The pages ship with **no fabricated proof** — there are no invented ratings, project
counts, years-in-business, or made-up customer reviews. The old "Reviews" section is now an
**"Our Promise"** section (real guarantees, no fake people), and trust strips state only
verifiable facts (licensed, insured, free estimates, warranty). This is safe to publish as-is.

**When you have real numbers, upgrade the proof** (all optional, all a plus for conversion):

- **Real reviews load automatically.** The homepage calls `GET /api/reviews`, which merges the
  business's existing sources — first-party QR reviews (dashboard `/api/reviews/published`) and
  Thumbtack + Angi/HomeAdvisor (`/api/ahb/reviews/external`). Set `REVIEWS_UPSTREAM` /
  `REVIEWS_EXTERNAL_UPSTREAM` (or bind D1) and the "Our Promise" cards are replaced by real
  reviews. Until then the promise cards show — nothing is fabricated.
- **Reviews QR code** is live on the homepage (`assets/review-qr.svg` → `/review`). The `/review`
  page posts to `/api/review/submit` (stored pending moderation, mirroring today's flow).
  Regenerate the QR for a different URL with: `python3 -c "import segno; segno.make('https://ahb123.com/review',error='q').save('assets/review-qr.svg',scale=10,border=2,dark='#0a0d14')"`
- Add real **rating + review count** and **years in business / projects completed** once verified.
- **Financing** is phrased as "ask about financing" (an invitation, not a claim) — search
  `TODO: confirm financing` before advertising any specific lender or terms.
- On `/review`, paste your real **Google / Thumbtack / Angi profile URLs** (search `TODO: paste`).

Everything else — company name, phone (215-554-5488), email
(contactahbco@gmail.com), address (2725 Colmar Ave, Bensalem, PA), social
(@allhomebuilding), and the service list — is pulled from the business records.
