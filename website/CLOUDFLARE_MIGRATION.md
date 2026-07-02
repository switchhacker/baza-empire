# AHB123.com → Cloudflare Migration & AI-Managed Site Architecture

**Business:** All Home Building Co. LLC (AHBCO) · Bensalem, PA
**Goal:** Move the entire public site to Cloudflare so the whole property — pages, images,
forms, leads, and support — can be managed programmatically (i.e. by the Baza agents / AI),
with instant global delivery and near-zero hosting cost.

---

## 1. Why Cloudflare (the pitch in one paragraph)

Today `ahb123.com` is a traditional hosted site: edits mean logging into a host, images live
on one server, lead forms email into a black hole, and nothing is version-controlled. Moving to
**Cloudflare Pages + Workers + R2 + D1/KV** turns the site into a *git-driven, AI-operable
product*. Every change is a commit. Every image is an object in R2 that an agent can upload,
resize, and reference. Every lead is a row an agent can read, score, and follow up on. Deploys
are atomic and global in ~10s, and the bill for a contractor-scale site is effectively $0–5/mo.

---

## 2. Target Architecture

```
                        ┌────────────────────────────────────────────┐
    Visitor ─── DNS ───▶│           Cloudflare (edge, global)          │
                        │                                              │
                        │  Pages  ──────────▶ static site (this repo)  │
                        │   │  build from  website/  on every push     │
                        │   │                                          │
                        │  Pages Functions / Workers                   │
                        │   ├─ POST /api/lead    → capture + notify     │
                        │   ├─ POST /api/estimate→ instant quote calc   │
                        │   └─ GET  /api/gallery → list images from R2  │
                        │                                              │
                        │  R2 (object storage)  → /images, /gallery,   │
                        │        before-after, blueprints, PDFs         │
                        │  D1 (SQLite)  → leads, estimates, page copy   │
                        │  KV           → feature flags, A/B variant     │
                        │  Turnstile    → spam-free forms (no captcha)   │
                        │  Email Routing→ leads@ahb123.com → inbox/agent │
                        └──────────────┬───────────────────────────────┘
                                       │  (webhook / API)
                                       ▼
                        Baza Empire agents (Simon / Sam / dashboard)
                        - read new leads from D1, score, assign
                        - push new site copy & images via Wrangler/API
                        - sync gallery images from Telegram → R2
                        - answer support chats, escalate to Serge
```

### Component map

| Layer | Cloudflare product | What it holds / does |
|-------|-------------------|----------------------|
| **Hosting** | **Pages** | Serves the static marketing site built from `website/`. Auto-deploys on every push to the connected branch. Preview URL per PR. |
| **Dynamic logic** | **Pages Functions / Workers** | Lead capture, instant-estimate math, gallery listing, chat webhook. See `website/functions/`. |
| **Images / files** | **R2** | All photos, before/after, blueprints, quote PDFs. S3-compatible → agents upload with the AWS SDK or `wrangler r2`. No egress fees. |
| **Structured data** | **D1** | `leads`, `estimates`, `gallery_assets`, `page_content` tables. Queryable by agents. |
| **Config / flags** | **KV** | A/B test variant, promo banner text, business hours — editable without a deploy. |
| **Spam control** | **Turnstile** | Invisible bot check on every form. |
| **Inbound email** | **Email Routing** | `leads@`, `contactahbco@gmail.com` → forward to inbox + POST to agent webhook. |
| **DNS + security** | **Cloudflare DNS / WAF / SSL** | Free managed TLS, DDoS protection, caching, analytics. |

---

## 3. How "AI manages the whole site"

Because everything is an API or a git file, the Baza agents get real leverage:

1. **Content updates** — Page copy and the promo banner live in `KV` / `D1.page_content`.
   An agent edits a value → change is live with no rebuild. Structural changes are a commit to
   `website/` → Pages redeploys automatically.
2. **Image sync** — Sam's imaging pipeline / Telegram intake writes finished project photos to
   **R2** and inserts a row in `D1.gallery_assets` (title, service tag, before/after). The
   `/api/gallery` function serves them, so the website gallery updates itself.
3. **Lead funneling** — Every form POSTs to `/api/lead` → row in `D1.leads` + Turnstile check +
   notify. Agents poll new leads, deduplicate, score, and either auto-reply or DISPATCH to Serge.
4. **Instant estimates** — `/api/estimate` runs the same pricing logic the dashboard uses, so a
   visitor gets a ballpark number *and* the agent gets a qualified lead with project scope.
5. **Support (Nova)** — **Nova Sterling**, AHBCO's Director of Client Relations, greets every
   visitor via the chat widget (`assets/nova-chat.js`) on all pages. It POSTs to `/api/chat`,
   which relays to the live `nova_sterling` agent (`env.NOVA_WEBHOOK`). Nova qualifies the lead,
   captures name + phone/email, and hands off to Rex/Simon via her own skills — with a warm
   built-in fallback so the widget stays conversational even before the agent is wired.

---

## 4. Migration Plan (phased, reversible)

**Phase 0 — Inventory (0.5 day)**
- Crawl current `ahb123.com`: list every page, image, form, and inbound email address.
- Export current DNS records. Note the current registrar and nameservers.

**Phase 1 — Stand up Cloudflare in parallel (1 day)**
- Create Cloudflare account + add `ahb123.com` as a zone (keep DNS "grey-cloud" until cutover).
- Connect this repo to **Pages**; set build output to `website/`. First deploy lands on a
  `*.pages.dev` preview URL — **nothing public changes yet.**
- Create R2 bucket `ahb123-media`, D1 db `ahb123`, KV namespace `ahb123-config`, Turnstile keys.

**Phase 2 — Port content & assets (1–2 days)**
- Upload all images to R2; seed `D1.gallery_assets`.
- Finalize the chosen homepage variation (see the three schemes shipped in this PR) and any
  interior pages. Wire forms to `/api/lead`.
- QA on the `pages.dev` preview: forms, click-to-call, mobile, Lighthouse ≥ 95.

**Phase 3 — Cutover (1 hour, low-traffic window)**
- Point the registrar's nameservers to Cloudflare (or, if DNS already on CF, "orange-cloud"
  the root + `www` records to the Pages project).
- Verify TLS (CF universal SSL), redirects (`www` → apex or vice-versa), and email routing.
- Watch analytics for 24h. **Rollback = repoint nameservers to the old host (TTL-limited).**

**Phase 4 — Hand the keys to the agents (ongoing)**
- Store a scoped Cloudflare API token in the Baza secrets store.
- Add skills: `cf_deploy`, `r2_upload`, `d1_query`, `kv_set` so agents can update the live site.
- Wire lead notifications into the existing Telegram/dashboard flow.

---

## 5. Cost (order-of-magnitude, contractor-scale traffic)

| Item | Free tier | Likely monthly |
|------|-----------|----------------|
| Pages (static + functions) | 500 builds/mo, unlimited requests* | **$0** |
| R2 | 10 GB storage, no egress fees | **$0–1** |
| D1 | 5 GB, 5M reads/day | **$0** |
| KV / Turnstile / Email Routing | generous free tiers | **$0** |
| Workers (if broken out) | 100k req/day free | **$0–5** |

\* Functions billed under the Workers free/paid tier. Realistic all-in: **$0–5/mo**, versus a
typical managed host at $20–50/mo — while gaining git history, previews, and AI operability.

---

## 6. What's in this PR

- `website/index.html` — a **scheme picker** showcasing the three homepage directions.
- `website/v1-momentum/` — **Momentum**: dark, premium, AI-forward. Best for "modern & high-end."
- `website/v2-homestead/` — **Homestead**: light, warm, trust-first. Best for local SEO + reviews.
- `website/v3-instant/` — **Instant Estimate**: a conversion-first lead-funnel landing page.
- `website/functions/api/lead.js` — Cloudflare Pages Function that captures leads into D1.
- `website/functions/api/estimate.js` — instant ballpark estimator.
- `website/functions/api/chat.js` + `website/assets/nova-chat.js` — Nova Sterling live chat.
- `website/wrangler.toml`, `_headers`, `_redirects` — Cloudflare Pages config.

> **Note on placeholder content:** review counts, ratings, years-in-business, and testimonial
> names are marked with `<!-- TODO: confirm -->` in the HTML. Swap in the real numbers before
> go-live. Everything else (NAP, phone, email, services) is pulled from the business records.
